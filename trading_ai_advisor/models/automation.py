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
        # If another analysis is already in state='running', skip this trigger.
        # Prevents the cron loop and PostgreSQL serialization failures seen in logs.
        running = self.env['trading.daily_analysis'].search(
            [('state', '=', 'running')], limit=1)
        if running:
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
            VALID_INSTRUMENTS = [
                'EUR/USD','GBP/USD','USD/JPY','AUD/USD','USD/CAD','USD/CHF',
                'NZD/USD','USD/SGD','GBP/JPY','EUR/JPY','AUD/JPY','EUR/GBP',
                'USD/NOK','GBP/CHF','USD/ZAR','USD/MXN','XAU/USD','EUR/CAD',
                'DIA','SPY','QQQ','EWG',
                'BTC/USDT','ETH/USDT','SOL/USDT','XRP/USDT','BNB/USDT',
                # US Stocks (yfinance)
                'AAPL','TSLA','NVDA','MSFT','AMZN','META','GOOGL',
                # Commodities (yfinance futures)
                'CL=F','BZ=F','NG=F','SI=F','GC=F','HG=F','PL=F','ZW=F','ZC=F','KC=F',
            ]
            instruments = self.env['trading.daily_instrument'].search([
                ('active', '=', True),
                ('instrument_key', 'in', VALID_INSTRUMENTS),
            ])
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
                log.append(f"\n⏰ {len(qualifying)} signal(s) queued — will auto-open at their entry time")
            else:
                log.append(f"\n📊 No signals met criteria (score ≥ {config.min_score}, non-LOW conf)")

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

    # ─────────────────────────────────────────────────────────────────────────
    # JOB 4 — Timed Entry Check (every 30 min)
    # Checks ALL today's sessions for signals whose entry window is now open
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def cron_timed_entry(self):
        """Every 30 min: open positions whose AI entry time window has arrived."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return

        now_nl = _nl_now()
        _logger.info("Timed entry check at %s NL", now_nl.strftime('%H:%M'))

        try:
            today     = fields.Date.today()
            simulator = self.env['trading.simulator'].search([('state', '=', 'active')], limit=1)
            if not simulator:
                return

            open_count = len(simulator.position_ids.filtered(lambda p: p.state == 'open'))
            if open_count >= config.max_positions:
                return

            # Collect ALL qualifying results from ALL today's sessions (best score wins)
            all_sessions = self.env['trading.daily_analysis'].search([
                ('analysis_date', '=', today),
                ('state', '=', 'done'),
            ])
            if not all_sessions:
                return

            # Build a map: instrument → best result (highest score) across all sessions
            best_per_instrument = {}
            for session in all_sessions:
                for r in session.result_ids:
                    if (r.score >= config.min_score
                            and r.signal in ('BUY', 'STRONG BUY', 'SELL', 'STRONG SELL')
                            and (config.trade_low_confidence or r.confidence != 'LOW')):
                        existing = best_per_instrument.get(r.instrument)
                        if not existing or r.score > existing.score:
                            best_per_instrument[r.instrument] = r

            opened_this_run = 0
            log_lines       = []

            for instrument, result in sorted(
                    best_per_instrument.items(),
                    key=lambda x: x[1].score, reverse=True):

                if open_count + opened_this_run >= config.max_positions:
                    break

                # Skip if already have open position for this instrument
                if simulator.position_ids.filtered(
                        lambda p: p.state == 'open' and p.instrument == instrument):
                    continue

                # Parse entry time
                entry_time_str = result.best_open_time_nl or result.best_open_time or ''
                entry_nl = _parse_nl_time(entry_time_str, now_nl)
                if entry_nl is None:
                    continue

                diff_min = (now_nl - entry_nl).total_seconds() / 60
                window   = config.open_window_minutes

                if -window <= diff_min <= window:
                    try:
                        direction = 'BUY' if 'BUY' in result.signal else 'SELL'

                        # ── Cortex evaluation ──────────────────────────────
                        cortex = self.env['trading.cortex'].get_singleton()
                        verdict, cortex_reason = cortex.evaluate_trade(
                            instrument=instrument,
                            direction=direction,
                            score=result.score,
                            confidence=result.confidence,
                            session=session_label if 'session_label' in dir() else 'unknown',
                        )
                        if verdict == 'VETO':
                            log_lines.append(f"🧠 VETO {instrument}: {cortex_reason}")
                            _logger.info("Cortex vetoed %s: %s", instrument, cortex_reason)
                            continue

                        # ── Pre-trade Re-validation ────────────────────────
                        still_valid, valid_reason = self._revalidate_signal(result)
                        if not still_valid:
                            log_lines.append(
                                f"🔄 REVALIDATION FAILED {instrument}: {valid_reason}")
                            _logger.info("Revalidation blocked %s: %s", instrument, valid_reason)
                            continue

                        if verdict == 'WARN':
                            log_lines.append(f"⚠ CORTEX WARN {instrument}: {cortex_reason}")

                        result.action_open_sim_position()
                        opened_this_run += 1
                        msg = (
                            f"📈 {instrument} {direction} OPENED at {now_nl.strftime('%H:%M')} NL "
                            f"(target {entry_time_str}) | Score {result.score}/10 | {result.confidence}"
                        )
                        if verdict == 'WARN':
                            msg += f" | ⚠ {cortex_reason[:60]}"
                        log_lines.append(msg)
                        _logger.info(msg)
                    except Exception as e:
                        log_lines.append(f"⚠ {instrument}: {e}")

            if log_lines or opened_this_run:
                existing_log = config.last_run_log or ''
                new_section  = (
                    f"\n\n⏰ ENTRY CHECK {now_nl.strftime('%H:%M')} NL"
                    + (f" — {opened_this_run} position(s) opened" if opened_this_run else " — no entries yet")
                    + '\n' + '\n'.join(log_lines)
                )
                config.write({
                    'last_run_log':      existing_log + new_section,
                    'last_entry_check':  fields.Datetime.now(),
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

    def action_run_now(self):
        """Run NY Open analysis right now (best quality)."""
        self.ensure_one()
        self._run_session_analysis("NY Open")
        return self._notify('🤖 Analysis Done', 'NY Open session complete. Entry checker will open positions at the right time.')

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
