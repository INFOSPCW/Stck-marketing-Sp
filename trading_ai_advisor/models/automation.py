# -*- coding: utf-8 -*-
"""
automation.py — Fully automated multi-session daily trading pipeline.

Sessions run 3× per day at peak liquidity windows:
  06:00 NL  Pre-Market  — crypto + Asian pairs
  09:00 NL  London Open — EUR/GBP pairs, XAU/USD
  15:00 NL  NY Open     — ALL forex majors + US ETFs (highest quality)

Each session creates a FRESH DailyAnalysis with live data.
The timed-entry checker (every 30 min) opens positions when the AI's
recommended entry time window is reached.
"""

import logging
import datetime as dt
import re as _re
import calendar as _cal

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


def _parse_nl_time(time_str, reference_dt):
    """Parse 'HH:MM CEST', '13:00 GMT', '09:00' → datetime on same date as reference_dt."""
    if not time_str:
        return None
    try:
        m = _re.search(r'(\d{1,2}):(\d{2})', time_str)
        if not m:
            return None
        h, mn = int(m.group(1)), int(m.group(2))
        return reference_dt.replace(hour=h, minute=mn, second=0, microsecond=0)
    except Exception:
        return None


def _nl_offset_now():
    """Netherlands UTC offset: +2 CEST (Apr–Oct), +1 CET (Oct–Apr)."""
    now = dt.datetime.utcnow()
    def last_sun(yr, mo):
        ld = _cal.monthrange(yr, mo)[1]
        d  = dt.date(yr, mo, ld)
        return d - dt.timedelta(days=d.weekday() + 1 if d.weekday() != 6 else 0)
    dst_s = last_sun(now.year, 3)
    dst_e = last_sun(now.year, 10)
    return 2 if dst_s <= now.date() < dst_e else 1


def _nl_now():
    return dt.datetime.utcnow() + dt.timedelta(hours=_nl_offset_now())


class TradingAutomation(models.Model):
    _name        = 'trading.automation'
    _description = 'Trading AI — Automation Settings'

    name = fields.Char(default='Automation Settings', readonly=True)

    # ── Settings ─────────────────────────────────────────────────────────────
    enabled = fields.Boolean(
        string='Enable Full Automation', default=False,
        help='When ON all scheduled jobs run. When OFF everything is manual.')

    min_score = fields.Integer(
        string='Minimum Score to Trade', default=6,
        help='Only open positions for signals scoring this or higher.\n'
             '7 = recommended (balanced), 8 = conservative, 6 = aggressive.')

    max_positions = fields.Integer(
        string='Max Open Positions at Once', default=3,
        help='Safety cap — never open more than this many positions simultaneously.')

    trade_low_confidence = fields.Boolean(
        string='Trade LOW Confidence Signals', default=False,
        help='Include LOW confidence signals (not recommended for beginners).')

    skip_weekends = fields.Boolean(
        string='Skip Weekends', default=True,
        help='Skip all jobs on Saturday and Sunday (forex/indices are closed).')

    open_window_minutes = fields.Integer(
        string='Entry Window (minutes)', default=30,
        help='How many minutes around the AI entry time to allow opening.\n'
             '30 min = open if within ±30 min of best_open_time_nl.\n'
             'The checker runs every 30 min so this guarantees one hit.')

    # ── Run logs ─────────────────────────────────────────────────────────────
    last_analysis_run   = fields.Datetime(string='Last Analysis Run',    readonly=True)
    last_entry_check    = fields.Datetime(string='Last Entry Check',     readonly=True)
    last_position_check = fields.Datetime(string='Last Position Check',  readonly=True)
    last_learning_run   = fields.Datetime(string='Last Learning Run',    readonly=True)
    last_run_log        = fields.Text(string='Last Run Log',             readonly=True)
    positions_opened_today = fields.Integer(string='Positions Opened Today', readonly=True)
    total_auto_trades   = fields.Integer(string='Total Auto Trades',     readonly=True)

    @api.model
    def get_singleton(self):
        rec = self.sudo().search([], limit=1)
        if not rec:
            rec = self.sudo().create({'name': 'Automation Settings'})
        return rec

    # ─────────────────────────────────────────────────────────────────────────
    # CORE — shared session analysis runner
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def _run_session_analysis(self, session_label):
        """
        Run a fresh analysis for a named session (Pre-Market / London Open / NY Open).
        Always fetches live data — each session reflects current market conditions.
        Returns the DailyAnalysis record, or None on failure.
        """
        # ── CONCURRENT RUN GUARD ──────────────────────────────────────────────
        # If another analysis is already in state='running', skip this trigger —
        # UNLESS it has been running for > 15 minutes (stuck due to timeout/kill),
        # in which case auto-reset it so the pipeline isn't blocked forever.
        running = self.env['trading.daily_analysis'].search(
            [('state', '=', 'running')], limit=1)
        if running:
            stuck_threshold = dt.timedelta(minutes=8)
            age = dt.datetime.utcnow() - (running.write_date or dt.datetime.utcnow())
            if age > stuck_threshold:
                _logger.warning(
                    "⚠ Auto-resetting stuck analysis '%s' (running for %.0f min). "
                    "Was likely killed by HTTP timeout mid-run.",
                    running.name, age.total_seconds() / 60
                )
                running.write({
                    'state':   'error',
                    'run_log': (running.run_log or '') +
                               f'\n\n⚠ Auto-reset after {age.total_seconds()/60:.0f} min stuck in running state. '
                               f'Session will be re-run now.',
                })
                self.env['trading.system_log'].log(
                    'warning', 'analysis',
                    f"⚠ Stuck analysis auto-reset: {running.name}",
                    detail=f"Was in state=running for {age.total_seconds()/60:.0f} min. Reset to error."
                )
            else:
                _logger.info(
                    "⏭ Skipping %s — analysis '%s' already running (since %s). "
                    "Will resume at next scheduled session.",
                    session_label, running.name, running.write_date
                )
                return None

        config = self.get_singleton()
        now_nl = _nl_now()
        today  = fields.Date.today()
        log    = [f"🤖 {session_label.upper()} — {now_nl.strftime('%Y-%m-%d %H:%M')} NL"]

        try:
            # Only use confirmed free-tier instruments — skip any stale entries still in DB
            # ── SESSION-SPECIFIC INSTRUMENT LISTS ─────────────────────────────
            # Each session analyses a relevant subset to stay within time limits.
            # All 44 instruments are covered across the full day.
            # At ~5-8s per instrument, limits are ~40 instruments per session max.
            _ALL_FOREX = [
                'EUR/USD','GBP/USD','USD/JPY','AUD/USD','USD/CAD','USD/CHF','NZD/USD',
                'GBP/JPY','EUR/JPY','AUD/JPY','EUR/GBP','USD/SGD','USD/NOK',
                'GBP/CHF','USD/ZAR','USD/MXN','EUR/CAD',
            ]
            _ALL_CRYPTO = ['BTC/USDT','ETH/USDT','SOL/USDT','XRP/USDT','BNB/USDT']
            _ALL_STOCKS = ['AAPL','TSLA','NVDA','MSFT','AMZN','META','GOOGL']
            _ALL_INDICES = ['DIA','SPY','QQQ','EWG']
            _ALL_METALS = ['XAU/USD','GC=F','SI=F','HG=F','PL=F']
            _ALL_ENERGY = ['CL=F','BZ=F','NG=F']
            _ALL_AG     = ['ZW=F','ZC=F','KC=F']
            _ALL_COMMOD = _ALL_METALS + _ALL_ENERGY + _ALL_AG

            _SESSION_INSTRUMENTS = {
                # Pre-Market (06:00 NL / 04:00 UTC) — crypto + Asian forex
                # Markets: Tokyo/Sydney closing, London not yet open
                'Pre-Market': (
                    _ALL_CRYPTO +
                    ['USD/JPY','AUD/USD','NZD/USD','AUD/JPY','USD/SGD',
                     'BTC/USDT','ETH/USDT','SOL/USDT','XRP/USDT','BNB/USDT']
                ),
                # London Open (09:00 NL / 07:00 UTC) — EUR/GBP pairs + metals
                # Markets: London open, Frankfurt open
                'London Open': (
                    ['EUR/USD','GBP/USD','EUR/GBP','GBP/CHF','EUR/CAD',
                     'USD/CHF','USD/NOK','GBP/JPY','EUR/JPY','AUD/USD',
                     'XAU/USD','GC=F','SI=F'] +
                    _ALL_CRYPTO +
                    _ALL_INDICES
                ),
                # London Mid-Morning (11:00 NL / 09:00 UTC) — full forex + energy
                'London Mid-Morning': (
                    _ALL_FOREX +
                    ['XAU/USD','CL=F','BZ=F','NG=F'] +
                    _ALL_CRYPTO
                ),
                # Pre-NY (13:00 NL / 11:00 UTC) — all forex + commodities
                'Pre-NY': (
                    _ALL_FOREX +
                    _ALL_COMMOD +
                    _ALL_CRYPTO
                ),
                # NY Open (15:00 NL / 13:00 UTC) — ALL 44 instruments
                # Best session — highest liquidity, stocks approaching open
                'NY Open': (
                    _ALL_FOREX + _ALL_CRYPTO + _ALL_INDICES +
                    _ALL_STOCKS + _ALL_COMMOD
                ),
                # US Market Open (15:30 NL / 13:30 UTC) — stocks + indices go live
                'US Market Open': (
                    _ALL_STOCKS + _ALL_INDICES +
                    ['EUR/USD','GBP/USD','XAU/USD','CL=F','NG=F'] +
                    _ALL_CRYPTO
                ),
                # NY Mid-Session (17:30 NL / 15:30 UTC) — full mid-day check
                'NY Mid-Session': (
                    _ALL_FOREX + _ALL_CRYPTO + _ALL_INDICES +
                    _ALL_STOCKS + _ALL_COMMOD
                ),
                # NY Close Approach (19:00 NL / 17:00 UTC) — overnight decisions
                'NY Close Approach': (
                    _ALL_FOREX + _ALL_CRYPTO + _ALL_INDICES +
                    _ALL_STOCKS + _ALL_COMMOD
                ),
            }

            # Get session-specific list, deduplicated, fall back to all 44
            _raw = _SESSION_INSTRUMENTS.get(session_label, (
                _ALL_FOREX + _ALL_CRYPTO + _ALL_INDICES + _ALL_STOCKS + _ALL_COMMOD
            ))
            VALID_INSTRUMENTS = list(dict.fromkeys(_raw))  # preserve order, dedupe

            # Auto-provision any missing trading.daily_instrument records so
            # the user never has to add them manually.
            # Use active_test=False to also find inactive records — re-activate them
            # rather than skipping them (default search drops active=False records).
            all_existing = self.env['trading.daily_instrument'].with_context(
                active_test=False).search([('instrument_key', 'in', VALID_INSTRUMENTS)])
            all_keys = set(all_existing.mapped('instrument_key'))
            missing_keys = [k for k in VALID_INSTRUMENTS if k not in all_keys]
            inactive = all_existing.filtered(lambda r: not r.active)
            if inactive:
                inactive.write({'active': True})
                log.append(f"♻ Re-activated {len(inactive)} instrument(s)")
            if missing_keys:
                for key in missing_keys:
                    self.env['trading.daily_instrument'].create({
                        'instrument_key': key,
                        'active': True,
                    })
                _logger.info("Auto-provisioned %d instruments for %s: %s",
                             len(missing_keys), session_label, missing_keys)
                log.append(f"🔧 Auto-added {len(missing_keys)} new instrument(s)")

            instruments = self.env['trading.daily_instrument'].search([
                ('active', '=', True),
                ('instrument_key', 'in', VALID_INSTRUMENTS),
            ])
            log.append(f"📡 Instruments selected: {len(instruments)}")
            try:
                config.write({'last_run_log': '\n'.join(log), 'last_analysis_run': fields.Datetime.now()})
                self.env.cr.commit()
            except Exception:
                pass
            if not instruments:
                log.append("❌ No active instruments. Check Daily → Instruments.")
                config.write({'last_run_log': '\n'.join(log), 'last_analysis_run': fields.Datetime.now()})
                return None

            # Delete previous session of same label today (clean re-run)
            old = self.env['trading.daily_analysis'].search([
                ('analysis_date', '=', today),
                ('name', 'like', session_label),
            ], limit=1)
            if old:
                old.result_ids.unlink()
                old.write({'state': 'draft', 'run_log': '', 'briefing': ''})
                analysis = old
                log.append(f"♻ Re-running {session_label} with fresh live data")
            else:
                analysis = self.env['trading.daily_analysis'].create({
                    'analysis_date':  today,
                    'instrument_ids': [(6, 0, instruments.ids)],
                })
                analysis.write({'name': f"{session_label} — {today}"})
                log.append(f"📋 New {session_label} session ({len(instruments)} instruments)")

            config.write({'last_run_log': '\n'.join(log), 'last_analysis_run': fields.Datetime.now()})
            self.env.cr.commit()

            analysis.action_run_analysis()

            actionable = analysis.result_ids.filtered(
                lambda r: r.signal not in ('NO TRADE', 'HOLD'))
            log.append(
                f"✅ Done. Top: {analysis.top_opportunity} | "
                f"{len(actionable)}/{len(analysis.result_ids)} actionable"
            )

            # Show top 5 results
            for r in analysis.result_ids.sorted('score', reverse=True)[:5]:
                flag = '🟢' if 'BUY'  in r.signal else \
                       '🔴' if 'SELL' in r.signal else '⚪'
                log.append(
                    f"  {flag} {r.instrument}: {r.signal} {r.score}/10 "
                    f"({r.confidence}) → open {r.best_open_time_nl or r.best_open_time}"
                )

            qualifying = analysis.result_ids.filtered(
                lambda r: (
                    r.score >= config.min_score
                    and r.signal in ('BUY', 'STRONG BUY', 'SELL', 'STRONG SELL')
                    and (config.trade_low_confidence or r.confidence != 'LOW')
                )
            )
            if qualifying:
                log.append(f"\n⏰ {len(qualifying)} signal(s) qualify — queuing pending positions")
            else:
                log.append(f"\n📊 No signals met criteria (score ≥ {config.min_score}, non-LOW conf)")

            # ── Queue pending positions immediately ───────────────────────────
            queued = self._queue_pending_positions(analysis, config)
            if queued:
                log.append(f"⏳ {queued} pending position(s) created — will open at entry time")

        except Exception as e:
            _logger.error("%s analysis failed: %s", session_label, e, exc_info=True)
            log.append(f"❌ Failed: {e}")
            analysis = None

        config.write({'last_run_log': '\n'.join(log), 'last_analysis_run': fields.Datetime.now()})
        return analysis

    # ─────────────────────────────────────────────────────────────────────────
    # SESSION CRONS
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def cron_daily_analysis_and_trade(self):
        """06:00 NL — Pre-market. Best for crypto + Asian pairs."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return
        self._run_session_analysis("Pre-Market")

    @api.model
    def cron_london_open(self):
        """09:00 NL — London open. Best for EUR/GBP pairs, GBP/JPY, XAU/USD."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return
        self._run_session_analysis("London Open")

    @api.model
    def cron_ny_open(self):
        """15:00 NL — NY open / London-NY overlap. Highest volume — best signals."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return
        self._run_session_analysis("NY Open")


    @api.model
    def cron_us_market_open(self):
        """15:30 NL — US Market Open. NYSE/NASDAQ go live. Best for SPY, QQQ, DIA, USD pairs."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return
        self._run_session_analysis("US Market Open")

    @api.model
    def cron_london_midmorning(self):
        """11:00 NL — London mid-morning. EUR/USD trend confirmation + XAU/USD."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return
        self._run_session_analysis("London Mid-Morning")

    @api.model
    def cron_pre_ny(self):
        """13:00 NL — Pre-NY / European afternoon. EUR/USD before NY, GBP pairs."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return
        self._run_session_analysis("Pre-NY")

    @api.model
    def cron_ny_midsession(self):
        """17:30 NL — NY mid-session. US stocks 2h after open + commodities peak."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return
        self._run_session_analysis("NY Mid-Session")

    @api.model
    def cron_ny_close_approach(self):
        """19:00 NL — NY close approach. Last intraday chance before NYSE close."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return
        self._run_session_analysis("NY Close Approach")

    # ─────────────────────────────────────────────────────────────────────────
    # Queue pending positions immediately after analysis
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def _queue_pending_positions(self, analysis, config):
        """
        Called right after analysis completes. For each qualifying signal,
        create a pending SimPosition with scheduled_open_time set to the AI's
        recommended entry time. Returns the count of positions queued.
        """
        simulator = self.env['trading.simulator'].search([('state', '=', 'active')], limit=1)
        if not simulator:
            return 0

        now_nl  = _nl_now()
        now_utc = dt.datetime.utcnow()
        offset  = _nl_offset_now()
        queued  = 0
        queued_details = []

        for result in analysis.result_ids.sorted('score', reverse=True):
            if not (result.score >= config.min_score
                    and result.signal in ('BUY', 'STRONG BUY', 'SELL', 'STRONG SELL')
                    and (config.trade_low_confidence or result.confidence != 'LOW')):
                continue

            instrument = result.instrument

            # Skip if already have open or pending position for this instrument
            existing = simulator.position_ids.filtered(
                lambda p: p.instrument == instrument and p.state in ('open', 'pending'))
            if existing:
                continue

            # Parse entry time (NL timezone)
            entry_time_str = result.best_open_time_nl or result.best_open_time or ''
            entry_nl = _parse_nl_time(entry_time_str, now_nl)

            if entry_nl is None:
                # No entry time — schedule 30 min from now
                entry_nl = now_nl + dt.timedelta(minutes=30)

            # If entry time is already far in the past (> 2× window), open window has closed
            if (now_nl - entry_nl).total_seconds() / 60 > config.open_window_minutes * 2:
                continue

            # Store as UTC in DB
            entry_utc = entry_nl - dt.timedelta(hours=offset)

            direction = 'BUY' if 'BUY' in result.signal else 'SELL'

            # Cortex pre-check before queueing
            try:
                cortex = self.env['trading.cortex'].get_singleton()
                verdict, cortex_reason = cortex.evaluate_trade(
                    instrument=instrument,
                    direction=direction,
                    score=result.score,
                    confidence=result.confidence,
                    session=analysis.name or 'auto',
                )
                if verdict == 'VETO':
                    _logger.info("Cortex pre-veto (not queuing) %s: %s", instrument, cortex_reason)
                    continue
            except Exception:
                pass

            try:
                with self.env.cr.savepoint():
                    inst_type = (result.inst_type
                                 if hasattr(result, 'inst_type') and result.inst_type
                                 else 'crypto' if any(x in instrument for x in
                                                      ('USDT','BTC','ETH','SOL','XRP','BNB'))
                                 else 'index' if instrument in ('DIA','SPY','QQQ','EWG')
                                 else 'forex')
                    self.env['trading.sim_position'].create({
                        'simulator_id':       simulator.id,
                        'result_id':          result.id,
                        'instrument':         instrument,
                        'inst_type':          inst_type,
                        'direction':          direction,
                        'state':              'pending',
                        'scheduled_open_time': entry_utc,
                        'entry_price':        result.entry_price or 0,
                        'stop_loss':          result.stop_loss  or 0,
                        'take_profit':        result.take_profit or 0,
                        'position_size_usd':  0,
                        'ai_score':           result.score,
                        'ai_confidence':      result.confidence,
                        'ai_reasoning':       (result.reasoning or '')[:2000],
                        'validity_notes':     f"Queued {now_nl.strftime('%H:%M')} NL → open @ {entry_time_str}",
                    })
                    queued += 1
                    queued_details.append(
                        f"  ⏳ {instrument} {direction} — entry @ {entry_time_str} | Score {result.score}/10")
            except Exception as e:
                _logger.warning("Could not queue pending position for %s: %s", instrument, e)

        if queued:
            simulator.message_post(
                body=(f"⏰ {queued} position(s) queued from {analysis.name}.<br/>"
                      f"They will open automatically at their entry time after validation.<br/>"
                      + "<br/>".join(queued_details))
            )
            self.env['trading.system_log'].log(
                'info', 'automation',
                f"⏳ {queued} pending position(s) queued from {analysis.name}",
                detail='\n'.join(queued_details)
            )

        return queued

    # ─────────────────────────────────────────────────────────────────────────
    # JOB 4 — Timed Entry Check (every 30 min)
    # Opens pending positions whose scheduled entry time window has arrived
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def cron_timed_entry(self):
        """Every 30 min: open pending positions whose scheduled entry time has arrived."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return

        now_nl  = _nl_now()
        now_utc = dt.datetime.utcnow()
        _logger.info("Timed entry check at %s NL", now_nl.strftime('%H:%M'))

        try:
            simulator = self.env['trading.simulator'].search([('state', '=', 'active')], limit=1)
            if not simulator:
                return

            open_count = len(simulator.position_ids.filtered(lambda p: p.state == 'open'))
            if open_count >= config.max_positions:
                config.write({'last_entry_check': fields.Datetime.now()})
                return

            window_secs = config.open_window_minutes * 60

            # Pending positions whose scheduled time is within the entry window
            pending_due = simulator.position_ids.filtered(
                lambda p: (
                    p.state == 'pending'
                    and bool(p.scheduled_open_time)
                    and -(window_secs) <= (now_utc - p.scheduled_open_time).total_seconds() <= window_secs * 2
                )
            ).sorted(key=lambda p: p.ai_score or 0, reverse=True)

            opened_this_run = 0
            log_lines       = []

            for pos in pending_due:
                if open_count + opened_this_run >= config.max_positions:
                    break

                instrument = pos.instrument

                # Skip if already have open position for this instrument
                if simulator.position_ids.filtered(
                        lambda p: p.state == 'open' and p.instrument == instrument):
                    pos.write({'state': 'cancelled',
                               'validity_notes': 'Skipped — already have open position for this instrument'})
                    continue

                try:
                    # ── Cortex evaluation ──────────────────────────────────
                    cortex = self.env['trading.cortex'].get_singleton()
                    verdict, cortex_reason = cortex.evaluate_trade(
                        instrument=instrument,
                        direction=pos.direction,
                        score=pos.ai_score or 5,
                        confidence=pos.ai_confidence or 'MEDIUM',
                        session='auto',
                    )
                    if verdict == 'VETO':
                        pos.write({'state': 'cancelled',
                                   'validity_notes': f'Cortex VETO: {cortex_reason[:200]}'})
                        log_lines.append(f"🧠 VETO {instrument}: {cortex_reason}")
                        _logger.info("Cortex vetoed %s: %s", instrument, cortex_reason)
                        continue

                    # ── Pre-trade re-validation ────────────────────────────
                    if pos.result_id:
                        still_valid, valid_reason = self._revalidate_signal(pos.result_id)
                        if not still_valid:
                            pos.write({'state': 'cancelled',
                                       'validity_notes': f'Invalid at entry time: {valid_reason[:200]}'})
                            log_lines.append(f"🔄 INVALID {instrument}: {valid_reason}")
                            _logger.info("Revalidation blocked %s: %s", instrument, valid_reason)
                            continue
                        pos.write({'validity_notes': f"Valid: {valid_reason[:200]}"})

                    if verdict == 'WARN':
                        log_lines.append(f"⚠ CORTEX WARN {instrument}: {cortex_reason}")

                    # ── Open the position ──────────────────────────────────
                    pos.action_open_pending()
                    opened_this_run += 1
                    sched_str = pos.scheduled_open_time.strftime('%H:%M UTC') if pos.scheduled_open_time else '?'
                    msg = (
                        f"📈 {instrument} {pos.direction} OPENED at {now_nl.strftime('%H:%M')} NL "
                        f"(sched {sched_str}) | Score {pos.ai_score}/10 | {pos.ai_confidence}"
                    )
                    if verdict == 'WARN':
                        msg += f" | ⚠ {cortex_reason[:60]}"
                    log_lines.append(msg)
                    _logger.info(msg)

                except Exception as e:
                    log_lines.append(f"⚠ {instrument}: {e}")
                    _logger.warning("Failed to open pending position %s: %s", instrument, e)

            if log_lines or opened_this_run:
                existing_log = config.last_run_log or ''
                new_section  = (
                    f"\n\n⏰ ENTRY CHECK {now_nl.strftime('%H:%M')} NL"
                    + (f" — {opened_this_run} position(s) opened" if opened_this_run else " — no entries yet")
                    + '\n' + '\n'.join(log_lines)
                )
                config.write({
                    'last_run_log':           existing_log + new_section,
                    'last_entry_check':       fields.Datetime.now(),
                    'positions_opened_today': (config.positions_opened_today or 0) + opened_this_run,
                    'total_auto_trades':      (config.total_auto_trades or 0) + opened_this_run,
                })
            else:
                config.write({'last_entry_check': fields.Datetime.now()})

        except Exception as e:
            _logger.error("Timed entry check failed: %s", e)

    # ─────────────────────────────────────────────────────────────────────────
    # Pre-trade Re-validation
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def _revalidate_signal(self, result):
        """
        Before opening a queued position, re-fetch live price data and ask
        Claude whether the original setup is still valid.

        Returns: (still_valid: bool, reason: str)
        Falls back to (True, reason) on any fetch/API error so the trade
        still opens rather than silently dying.
        """
        try:
            cfg     = self.env['trading.config'].get_config()
            api_key = cfg.get('anthropic_api_key', '')
            if not api_key:
                return True, "No API key — revalidation skipped"

            instrument = result.instrument
            inst_type  = result.inst_type if hasattr(result, 'inst_type') and result.inst_type else (
                'crypto' if any(x in instrument for x in
                                ('USDT', 'BTC', 'ETH', 'SOL', 'XRP', 'BNB'))
                else 'index' if instrument in ('DIA', 'SPY', 'QQQ', 'EWG')
                else 'forex'
            )

            from .daily_analysis import (
                _fetch_forex_bars, _fetch_crypto_bars, _compute_indicators, _claude_post
            )

            rows = []
            if inst_type == 'crypto':
                rows = _fetch_crypto_bars(instrument)
            else:
                td_key = cfg.get('twelve_data_api_key', '')
                if not td_key:
                    return True, "No TD key — revalidation skipped"
                rows = _fetch_forex_bars(instrument, td_key)

            if len(rows) < 15:
                return True, f"Insufficient data ({len(rows)} bars) — proceeding"

            indicators = _compute_indicators(rows)
            ind_str    = '\n'.join(f"  {k}: {v}" for k, v in indicators.items())

            current_price = indicators.get('current_price', 0)
            entry_price   = result.entry_price or 0
            stop_loss     = result.stop_loss   or 0

            # Quick sanity: if price already blew through the stop loss, reject immediately
            if stop_loss and entry_price and current_price:
                direction = 'BUY' if 'BUY' in (result.signal or '') else 'SELL'
                if direction == 'BUY'  and current_price < stop_loss * 0.998:
                    return False, (
                        f"Price {current_price:.5f} already below SL {stop_loss:.5f} — "
                        f"setup invalidated")
                if direction == 'SELL' and current_price > stop_loss * 1.002:
                    return False, (
                        f"Price {current_price:.5f} already above SL {stop_loss:.5f} — "
                        f"setup invalidated")

            payload = {
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 100,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"Quick re-validation: Planning to open {result.signal} on {instrument}.\n"
                        f"Original: score {result.score}/10, entry {entry_price:.5f}, "
                        f"SL {stop_loss:.5f}, TP {result.take_profit or 0:.5f}.\n\n"
                        f"CURRENT MARKET DATA:\n{ind_str}\n\n"
                        f"Reply with VALID or INVALID and one short reason (max 15 words).\n"
                        f"Mark INVALID only if: price exceeded SL, trend reversed strongly, "
                        f"or RSI directly contradicts the direction."
                    )
                }]
            }

            resp   = _claude_post(api_key, payload, timeout=20)
            text   = (resp.get('content', [{}])[0].get('text', 'VALID')).strip()
            valid  = 'INVALID' not in text.upper()
            return valid, text

        except Exception as e:
            _logger.warning("Revalidation error for %s (proceeding): %s", result.instrument, e)
            return True, f"Revalidation error (proceeding): {e}"

    # ─────────────────────────────────────────────────────────────────────────
    # JOB 2 — Position Check (16:00 NL)
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def cron_check_positions(self):
        """16:00 NL — close positions that hit SL or TP."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return

        try:
            simulator = self.env['trading.simulator'].search([('state', '=', 'active')], limit=1)
            if not simulator:
                return
            open_pos = simulator.position_ids.filtered(lambda p: p.state == 'open')
            if not open_pos:
                config.write({'last_position_check': fields.Datetime.now()})
                return
            simulator.action_check_positions()

            # ── AUTO OVERNIGHT DECISION ────────────────────────────────────────
            # For positions still marked 'pending' at EOD: ask Claude automatically
            # No manual review needed — system decides and acts
            pending_overnight = simulator.position_ids.filtered(
                lambda p: p.state == 'open' and p.hold_overnight == 'pending')
            if pending_overnight:
                _logger.info("Auto overnight review for %d positions", len(pending_overnight))
                for pos in pending_overnight:
                    try:
                        pos.action_review_overnight()  # Claude decides HOLD or CLOSE
                    except Exception as e:
                        _logger.warning("Auto overnight review failed for %s: %s", pos.instrument, e)
                        # Safe fallback — close if AI review fails
                        try:
                            pos.write({'hold_overnight': 'close_eod'})
                        except Exception:
                            pass

            # Close positions marked close_eod (by user OR just decided by AI above)
            eod_positions = simulator.position_ids.filtered(
                lambda p: p.state == 'open' and p.hold_overnight == 'close_eod')
            for pos in eod_positions:
                try:
                    pos.action_close_manual()
                    _logger.info("EOD auto-close: %s %s", pos.instrument, pos.direction)
                except Exception as e:
                    _logger.warning("EOD close failed for %s: %s", pos.instrument, e)

            config.write({'last_position_check': fields.Datetime.now()})
        except Exception as e:
            _logger.error("Position check failed: %s", e)

    # ─────────────────────────────────────────────────────────────────────────
    # JOB 3 — Post-Session Learning (20:00 NL)
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def cron_post_session_learning(self):
        """20:00 NL — analyse losses, update rulebook, refresh AI review."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return

        log = [f"🧠 POST-SESSION LEARNING — {_nl_now().strftime('%H:%M')} NL"]
        try:
            cfg     = self.env['trading.config'].get_config()
            api_key = cfg.get('anthropic_api_key', '')
            if not api_key:
                log.append("⚠ No Anthropic key"); config.write({'last_run_log': '\n'.join(log)}); return

            from .daily_analysis import _update_rulebook_from_losses
            today_losses = self.env['trading.trade_log'].search([
                ('outcome', '=', 'LOSS'),
                ('trade_date', '=', fields.Date.today()),
                '|',
                ('what_went_wrong', '=', False),
                ('mistake_category', '=', 'other'),
            ])

            for loss in today_losses:
                try:
                    loss.action_analyse_loss()
                    log.append(f"  ✓ {loss.instrument}: {loss.mistake_category}")
                except Exception as e:
                    log.append(f"  ⚠ {loss.instrument}: {e}")

            result = _update_rulebook_from_losses(self.env, api_key)
            log.append(f"🧠 Rulebook: {result.get('message', 'done')}")

            # ── Cortex learning from today's closed trades ────────────────────
            try:
                cortex = self.env['trading.cortex'].get_singleton()
                today_closed = self.env['trading.trade_log'].search([
                    ('trade_date', '=', fields.Date.today()),
                ], order='id asc')
                for trade in today_closed:
                    if trade.outcome in ('WIN', 'LOSS', 'BREAKEVEN'):
                        cortex.learn_from_outcome(
                            instrument=trade.instrument,
                            outcome=trade.outcome,
                            session='post-session',
                            confidence='MEDIUM',
                        )
                if today_closed:
                    log.append(
                        f"🧠 Cortex updated from {len(today_closed)} trade(s) today. "
                        f"State: {cortex.state}"
                    )
            except Exception as e:
                log.append(f"   Cortex update skipped: {e}")

            simulator = self.env['trading.simulator'].search([('state', '=', 'active')], limit=1)
            if simulator:
                try:
                    simulator.action_get_ai_review()
                    log.append("✅ AI Performance Review updated")
                except Exception as e:
                    log.append(f"   Review skipped: {e}")

        except Exception as e:
            _logger.error("Post-session learning failed: %s", e)
            log.append(f"❌ {e}")

        config.write({'last_learning_run': fields.Datetime.now(), 'last_run_log': '\n'.join(log)})

    # ─────────────────────────────────────────────────────────────────────────
    # Manual triggers
    # ─────────────────────────────────────────────────────────────────────────

    def action_reset_stuck_analysis(self):
        """Reset any analysis stuck in 'running' state so the pipeline can continue."""
        self.ensure_one()
        stuck = self.env['trading.daily_analysis'].search([('state', '=', 'running')])
        if not stuck:
            return self._notify('✅ No Stuck Analysis', 'All analysis records are in a clean state.', 'info')
        count = len(stuck)
        for rec in stuck:
            rec.write({
                'state':   'error',
                'run_log': (rec.run_log or '') + '\n\n⚠ Manually reset from stuck running state.',
            })
        self.env['trading.system_log'].log(
            'warning', 'analysis',
            f"⚠ {count} stuck analysis record(s) manually reset",
            detail=', '.join(stuck.mapped('name') or ['unknown'])
        )
        return self._notify(
            '🔧 Stuck Analysis Reset',
            f'{count} analysis record(s) reset. You can now run a fresh analysis.',
            'warning'
        )

    @api.model
    def cron_execute_manual_run(self):
        """
        Executed by the one-shot cron created by action_run_now.
        Runs NY Open analysis, bypassing enabled/skip_weekends checks.
        Does NOT touch the cron record itself — Odoo holds a row-lock on it
        during execution, so writing to it causes an immediate deadlock.
        The cron has interval_number=999 days so it won't auto-fire again.
        """
        _logger.info("Manual Run Now: starting NY Open analysis")
        self._run_session_analysis('NY Open')
        _logger.info("Manual Run Now: NY Open analysis complete")

    def action_run_now(self):
        """
        Queue a NY Open analysis to run within ~1 minute via a one-shot ir.cron.
        Returns immediately — the cron scheduler picks it up on its next tick.
        Background threads don't work in Odoo 19 (ORM logger requires HTTP
        thread-local context). One-shot cron is the correct Odoo pattern.
        """
        self.ensure_one()
        # Remove any stale one-shot from a previous click
        old = self.env['ir.cron'].sudo().search(
            [('name', '=', 'Trading AI: Manual Run Now (one-shot)')])
        old.unlink()

        model_id = self.env['ir.model']._get_id('trading.automation')
        self.env['ir.cron'].sudo().create({
            'name':            'Trading AI: Manual Run Now (one-shot)',
            'model_id':        model_id,
            'state':           'code',
            'code':            'model.cron_execute_manual_run()',
            'interval_number': 999,
            'interval_type':   'days',
            'active':          True,
            'nextcall':        fields.Datetime.now(),
            'priority':        1,
        })
        _logger.info("Manual Run Now: one-shot cron created, fires at next scheduler tick")
        return self._notify(
            '🚀 Analysis Queued',
            'NY Open analysis will start within ~1 minute (next scheduler tick). '
            'Check the Analysis Sessions list in ~5 minutes for results.'
        )

    def action_open_now(self):
        """Trigger entry check right now."""
        self.ensure_one()
        self.cron_timed_entry()
        return self._notify('⏰ Entry Check Done', 'Check Last Run Log — positions opened if entry window matched.')

    def action_check_now(self):
        """Check open positions right now."""
        self.ensure_one()
        self.cron_check_positions()
        return self._notify('🔄 Position Check Done', 'Positions checked and closed if SL/TP hit.')

    def action_learn_now(self):
        """Trigger post-session learning right now."""
        self.ensure_one()
        self.cron_post_session_learning()
        return self._notify('🧠 Learning Complete', 'Losses analysed and rulebook updated.')

    def action_view_log(self):
        """Open the system log filtered to today."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'System Log',
            'res_model': 'trading.system_log',
            'view_mode': 'list,form',
            'target': 'current',
        }

    def action_purge_log(self):
        """Delete log entries older than 30 days."""
        self.ensure_one()
        self.env['trading.system_log'].purge_old(days=30)
        return self._notify('🗑 Logs Purged', 'Removed entries older than 30 days.', 'info')

    def _notify(self, title, message, ntype='success'):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'sticky': False, 'type': ntype},
        }
