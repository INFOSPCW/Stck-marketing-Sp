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


# ─────────────────────────────────────────────────────────────────────────────
# News-Surprise engine — second, backtest-validated edge.
# Trades only high-impact economic releases where actual beats/misses forecast
# by more than a configurable threshold, in the surprise direction, on the
# pairs where the edge validated (PF ~1.1-1.4 over 10y at FTMO costs).
# ─────────────────────────────────────────────────────────────────────────────

# Finnhub 'country' code on an event -> ISO currency we trade.
_NEWS_COUNTRY_CCY = {
    'US': 'USD', 'EU': 'EUR', 'GB': 'GBP', 'UK': 'GBP', 'JP': 'JPY',
    'AU': 'AUD', 'CA': 'CAD', 'NZ': 'NZD', 'CH': 'CHF',
}

# Currency -> the pairs we express a surprise through, with which leg the
# currency sits on. Only the validated, surprise-rich pairs are listed; the
# over-efficient set (EUR/USD majors) and noise (EUR/JPY) are deliberately out.
# 'base' => currency strong -> BUY pair ; 'quote' => currency strong -> SELL pair.
_NEWS_CCY_PAIRS = {
    'USD': [('USD/CAD', 'base'), ('NZD/USD', 'quote'), ('USD/ZAR', 'base'), ('USD/MXN', 'base')],
    'CAD': [('USD/CAD', 'quote'), ('EUR/CAD', 'quote')],
    'NZD': [('NZD/USD', 'base')],
    'GBP': [('EUR/GBP', 'quote'), ('GBP/JPY', 'base')],
    'EUR': [('EUR/CAD', 'base'), ('EUR/GBP', 'base')],
    'AUD': [('AUD/JPY', 'base')],
    'JPY': [('GBP/JPY', 'quote'), ('AUD/JPY', 'quote')],
}

# Default tradable whitelist (the FTMO-compliant clean+exotic winners).
_NEWS_DEFAULT_PAIRS = {'USD/CAD', 'NZD/USD', 'EUR/CAD', 'EUR/GBP', 'GBP/JPY', 'AUD/JPY',
                       'USD/ZAR', 'USD/MXN'}


def _news_is_inverted(event_name):
    """Events where a LOWER actual means a STRONGER currency (unemployment, claims)."""
    e = (event_name or '').lower()
    return ('unemployment' in e) or ('jobless' in e) or ('claims' in e)


def _news_surprise_pct(actual, forecast):
    """Signed fractional surprise (actual-forecast)/|forecast|, robust to junk.
    Returns None if not computable or the forecast is too small to be meaningful."""
    def _num(x):
        try:
            return float(str(x).replace(',', '').replace('%', '').strip())
        except (ValueError, AttributeError):
            return None
    a, f = _num(actual), _num(forecast)
    if a is None or f is None:
        return None
    if abs(f) < 1e-6:                       # near-zero forecast -> % explodes, skip
        return None
    return (a - f) / abs(f)


def _news_direction(currency, surprise, event_name, leg):
    """Resolve trade direction for a pair given the surprising currency.
    Positive 'strength' => the surprising currency strengthened."""
    strength = surprise if not _news_is_inverted(event_name) else -surprise
    if strength == 0:
        return None
    ccy_up = strength > 0                    # did the surprising currency strengthen?
    if leg == 'base':                        # currency is the pair's base
        return 'BUY' if ccy_up else 'SELL'
    return 'SELL' if ccy_up else 'BUY'       # currency is the quote




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
        string='Minimum Score to Trade', default=7,
        help='Only open positions for signals scoring this or higher.\n'
             '7 = recommended (validated edge), 8 = conservative, 6 = aggressive (more noise).')

    max_positions = fields.Integer(
        string='Max Open Positions at Once', default=3,
        help='Safety cap — never open more than this many positions simultaneously.')

    trade_low_confidence = fields.Boolean(
        string='Trade LOW Confidence Signals', default=True,
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
            stuck_threshold = dt.timedelta(minutes=5)  # cron worker timeout is ~5min
            age = dt.datetime.utcnow() - (running.write_date or dt.datetime.utcnow())
            if age > stuck_threshold:
                _logger.warning(
                    "⚠ Auto-resetting stuck analysis '%s' (running for %.0f min). "
                    "Was likely killed by HTTP timeout mid-run.",
                    running.name, age.total_seconds() / 60
                )
                running.write({
                    'state':   'done',
                    'run_log': (running.run_log or '') +
                               f'\n\n⚠ Auto-reset after {age.total_seconds()/60:.0f} min stuck. '
                               f'New session starting.',
                })
                self.env.cr.commit()   # commit the reset before proceeding
                self.env['trading.system_log'].log(
                    'warning', 'analysis',
                    f"⚠ Stuck analysis auto-reset: {running.name}",
                    detail=f"Was in state=running for {age.total_seconds()/60:.0f} min. Proceeding with {session_label}."
                )
                # Fall through — do NOT return, proceed with new session
            else:
                age_min = age.total_seconds() / 60
                _logger.info(
                    "⏭ Skipping %s — analysis '%s' already running "
                    "(since %s, %.0f min ago). Auto-reset after 5 min if stuck.",
                    session_label, running.name, running.write_date, age_min
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
                # Full Scan — all 44 instruments, used by the every-3-hours cron
                'Full Scan': (
                    _ALL_FOREX + _ALL_CRYPTO + _ALL_INDICES +
                    _ALL_STOCKS + _ALL_COMMOD
                ),
            }

            # Get session-specific list, deduplicated, fall back to all 44
            _raw = _SESSION_INSTRUMENTS.get(session_label, (
                _ALL_FOREX + _ALL_CRYPTO + _ALL_INDICES + _ALL_STOCKS + _ALL_COMMOD
            ))
            VALID_INSTRUMENTS = list(dict.fromkeys(_raw))  # preserve order, dedupe

            # NOTE: The scan/analysis always covers the FULL instrument universe so
            # that every paper-trading account has signals available. Whether an
            # account actually TRADES a given instrument is decided per-account by
            # its own `focus_pairs` field (see trading.simulator). This lets one
            # account trade everything while another trades only USD/CAD + USD/JPY.
            # A global optional override still exists via ir.config_parameter
            # 'trading_ai.focus_pairs' (set to a list to force-restrict the scan,
            # default 'off' = scan everything).
            _gfocus = (self.env['ir.config_parameter'].sudo()
                       .get_param('trading_ai.focus_pairs', 'off') or '').strip()
            if _gfocus.lower() not in ('off', 'all', ''):
                _gset = [p.strip() for p in _gfocus.split(',') if p.strip()]
                _gfilt = [i for i in VALID_INSTRUMENTS if i in _gset]
                VALID_INSTRUMENTS = _gfilt if _gfilt else _gset
                log.append(f"🎯 Global scan focus: {', '.join(VALID_INSTRUMENTS)}")

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

            # Each run creates a NEW analysis record — never overwrites previous runs.
            # This preserves history so you can compare sessions across the day.
            existing_today = self.env['trading.daily_analysis'].search([
                ('analysis_date', '=', today),
                ('name', 'like', session_label),
            ], order='id desc')

            # Create fresh session — always preserve previous results
            run_num = len(existing_today) + 1
            label   = f"{session_label} — {today}" if run_num == 1 \
                      else f"{session_label} — {today} (run {run_num})"
            analysis = self.env['trading.daily_analysis'].create({
                'analysis_date':  today,
                'instrument_ids': [(6, 0, instruments.ids)],
            })
            analysis.write({'name': label})
            log.append(f"📋 New {session_label} session #{run_num} ({len(instruments)} instruments)")

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

            # ── Queue pending positions ──────────────────────────────────────
            # Invalidate ORM cache — action_run_analysis commits internally
            analysis.invalidate_recordset(['state'])
            if analysis.state == 'done':
                queued = self._queue_pending_positions(analysis, config)
                if queued:
                    log.append(f"⏳ {queued} pending position(s) created — will open at entry time")
            else:
                log.append(f"⏳ Batch in progress — positions will be queued after final batch")

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

    @api.model
    def cron_full_scan(self):
        """
        Runs hourly but only executes a full scan at HIGH-VALUE trading windows
        to save API cost. The best times to trade (highest liquidity/volatility):
          • 07:00 UTC — London open (forex prime time)
          • 12:00 UTC — London/NY overlap begins (highest forex volume)
          • 13:30 UTC — NY stock market open
          • 18:00 UTC — NY afternoon / pre-close positioning
          • 00:00 UTC — Asian session open (JPY/AUD pairs + crypto)
        Outside these windows the cron returns immediately (near-zero cost).
        """
        config = self.get_singleton()
        if not config.enabled:
            return
        if config.skip_weekends and dt.date.today().weekday() >= 5:
            # Weekend: only crypto trades. Run a lighter scan only at 12:00 UTC.
            if dt.datetime.utcnow().hour != 12:
                return

        # High-value scan windows (UTC hours)
        SCAN_HOURS = self._get_scan_hours()
        current_hour = dt.datetime.utcnow().hour
        if current_hour not in SCAN_HOURS:
            return  # not a prime window — skip to save cost

        self._run_session_analysis("Full Scan")

    def _get_scan_hours(self):
        """
        Returns the set of UTC hours at which a full scan runs.
        Configurable via the 'trading_ai.scan_hours' param (comma-separated),
        defaults to the 5 highest-value session windows.
        """
        icp = self.env['ir.config_parameter'].sudo()
        raw = icp.get_param('trading_ai.scan_hours', '0,7,12,13,18')
        try:
            return {int(h.strip()) for h in raw.split(',') if h.strip() != ''}
        except Exception:
            return {0, 7, 12, 13, 18}

    # ─────────────────────────────────────────────────────────────────────────
    # Queue pending positions immediately after analysis
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def _queue_pending_positions(self, analysis, config):
        """
        Called right after analysis completes. For each qualifying signal,
        create a pending SimPosition with scheduled_open_time set to the AI's
        recommended entry time, for EVERY active account whose focus permits the
        instrument. Returns the total count of positions queued (across accounts).
        """
        accounts = self.env['trading.simulator'].search([('state', '=', 'active')])
        if not accounts:
            return 0

        now_nl  = _nl_now()
        now_utc = dt.datetime.utcnow()
        offset  = _nl_offset_now()
        queued  = 0
        per_account_details = {a.id: [] for a in accounts}

        for result in analysis.result_ids.sorted('score', reverse=True):
            if not (result.score >= config.min_score
                    and result.signal in ('BUY', 'STRONG BUY', 'SELL', 'STRONG SELL')
                    and (config.trade_low_confidence or result.confidence != 'LOW')):
                continue

            instrument = result.instrument
            direction  = 'BUY' if 'BUY' in result.signal else 'SELL'

            # ── 4 QUALITY RULES ───────────────────────────────────────────────
            # Rule 1: R/R ≥ 1.5 (risk less to make more)
            rr = float(result.r_r_ratio or result.risk_reward or 0)
            if rr < 1.5:
                _logger.info("Quality gate R/R: skipping %s %s (R/R %.2f < 1.5)",
                             instrument, direction, rr)
                continue

            # Rule 2: SL must be big enough to survive noise / spread
            entry = float(result.entry_price or 0)
            sl    = float(result.stop_loss   or 0)
            if entry > 0 and sl > 0:
                sl_pct = abs(entry - sl) / entry * 100
                inst_type_str = (result.inst_type
                                 if hasattr(result, 'inst_type') and result.inst_type
                                 else 'crypto' if any(x in instrument for x in
                                     ('USDT','BTC','ETH','SOL','XRP','BNB'))
                                 else 'index' if instrument in ('DIA','SPY','QQQ','EWG')
                                 else 'stock' if instrument in
                                     ('AAPL','TSLA','NVDA','MSFT','AMZN','META','GOOGL')
                                 else 'commodity' if instrument.endswith('=F')
                                 else 'forex')
                _min_sl = {
                    'forex':     0.50,
                    'crypto':    1.20,
                    'stock':     1.50,
                    'commodity': 1.50,
                    'index':     0.80,
                }.get(inst_type_str, 0.50)
                if sl_pct < _min_sl:
                    _logger.info("Quality gate SL: skipping %s %s (SL %.3f%% < min %.2f%%)",
                                 instrument, direction, sl_pct, _min_sl)
                    continue

            # Rule 3: Don't SELL into extreme oversold (RSI < 25)
            rsi = float(result.rsi or 50)
            if direction == 'SELL' and rsi < 25:
                _logger.info("Quality gate RSI: skipping %s SELL (RSI %.1f < 25, extreme oversold)",
                             instrument, rsi)
                continue

            # Rule 4: Don't BUY into extreme overbought (RSI > 78)
            if direction == 'BUY' and rsi > 78:
                _logger.info("Quality gate RSI: skipping %s BUY (RSI %.1f > 78, extreme overbought)",
                             instrument, rsi)
                continue

            # Rule 5: Counter-trend gate — only trade WITH the 5-day trend.
            # Validated 4 ways (USDCAD '21/'23, GBPUSD '24, full 17pr/10yr mech):
            # counter-trend trades lose at PF ~0.12-0.40 at ANY magnitude, so the
            # default blocks every counter-trend trade (threshold 0.0). Raise the
            # param to only block stronger counter-trend moves if ever desired.
            ct_thresh = float(self.env['ir.config_parameter'].sudo().get_param(
                'trading_ai.counter_trend_pct', 0.0))
            trend_5d = float(result.daily_trend_5d_pct or 0.0)
            if direction == 'BUY' and trend_5d < -ct_thresh:
                _logger.info("Quality gate TREND: skipping %s BUY (5d trend %.2f%% — counter-trend)",
                             instrument, trend_5d)
                continue
            if direction == 'SELL' and trend_5d > ct_thresh:
                _logger.info("Quality gate TREND: skipping %s SELL (5d trend %.2f%% — counter-trend)",
                             instrument, trend_5d)
                continue
            # ── END QUALITY RULES ─────────────────────────────────────────────

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

            # Cortex pre-check before queueing (account-independent)
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

            inst_type = (result.inst_type
                         if hasattr(result, 'inst_type') and result.inst_type
                         else 'crypto' if any(x in instrument for x in
                                              ('USDT','BTC','ETH','SOL','XRP','BNB'))
                         else 'index' if instrument in ('DIA','SPY','QQQ','EWG')
                         else 'forex')

            # ── Queue for EACH active account whose focus allows this instrument ──
            for account in accounts:
                if not account.allows_instrument(instrument):
                    continue  # this account is focused on other instruments

                # Skip if this account already has open/pending for the instrument
                existing = account.position_ids.filtered(
                    lambda p: p.instrument == instrument and p.state in ('open', 'pending'))
                if existing:
                    continue

                try:
                    with self.env.cr.savepoint():
                        self.env['trading.sim_position'].create({
                            'simulator_id':       account.id,
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
                        per_account_details[account.id].append(
                            f"  ⏳ {instrument} {direction} — entry @ {entry_time_str} | Score {result.score}/10")
                except Exception as e:
                    _logger.warning("Could not queue %s for account %s: %s",
                                    instrument, account.name, e)

        for account in accounts:
            details = per_account_details.get(account.id, [])
            if details:
                account.message_post(
                    body=(f"⏰ {len(details)} position(s) queued from {analysis.name}.<br/>"
                          f"They will open automatically at their entry time after validation.<br/>"
                          + "<br/>".join(details))
                )

        if queued:
            self.env['trading.system_log'].log(
                'info', 'automation',
                f"⏳ {queued} pending position(s) queued from {analysis.name} "
                f"across {len(accounts)} account(s)",
                detail='\n'.join(
                    f"[{a.name}] focus={a.focus_pairs or 'all'}\n" +
                    '\n'.join(per_account_details.get(a.id, []) or ['  (none)'])
                    for a in accounts)
            )

        return queued

    # ─────────────────────────────────────────────────────────────────────────
    # JOB 4 — Timed Entry Check (every 30 min)
    # Opens pending positions whose scheduled entry time window has arrived
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def cron_queue_positions(self):
        """
        Every 15 min: scan for any 'done' analysis sessions that haven't had
        positions queued yet, and queue them. This is independent of the batch
        system so positions always get queued even if the batch signal fails.
        """
        config = self.get_singleton()
        if not config.enabled:
            return

        import datetime as _dt2
        today     = _dt2.date.today()
        # Find today's done analyses
        analyses = self.env['trading.daily_analysis'].search([
            ('analysis_date', '=', today),
            ('state', '=', 'done'),
        ], order='id desc')

        if not analyses:
            return

        # At least one active account must exist (per-account dedup/focus is
        # handled inside _queue_pending_positions, which loops all accounts)
        if not self.env['trading.simulator'].search_count([('state', '=', 'active')]):
            return

        # Queue from the LATEST analysis only (most current signals)
        latest = analyses[0]
        _logger.info("Queue check: latest analysis '%s' has %d results",
                     latest.name, len(latest.result_ids))

        queued = self._queue_pending_positions(latest, config)
        if queued:
            _logger.info("Queue check: %d new position(s) queued from '%s'", queued, latest.name)
        else:
            _logger.info("Queue check: no new positions to queue")

    @api.model
    def cron_news_surprise(self):
        """
        News-Surprise engine (second edge). Every ~15 min: look at high-impact
        economic releases in the recent past, and when actual beats/misses the
        forecast by more than the configured threshold, open a position in the
        surprise direction on the validated pairs — sized at risk %, SL ~1%,
        R/R 1.5, managed by the existing lifecycle. Runs alongside the AI signals.
        """
        config = self.get_singleton()
        if not config.enabled:
            return

        icp = self.env['ir.config_parameter'].sudo()
        if icp.get_param('trading_ai.news_surprise_enabled', 'true').lower() not in ('true', '1', 'yes'):
            return

        cfg = self.env['trading.config'].get_config()
        finnhub_key = cfg.get('finnhub_api_key', '')
        if not finnhub_key:
            return

        thresh   = float(icp.get_param('trading_ai.news_surprise_pct', 0.20))   # 20% default
        sl_floor = float(icp.get_param('trading_ai.news_sl_pct', 1.0)) / 100.0   # 1% SL floor
        rr       = float(icp.get_param('trading_ai.news_rr', 1.5))
        lookback_min = int(icp.get_param('trading_ai.news_lookback_min', 90))
        # tradable whitelist (comma-separated override, else the validated default set)
        wl_param = icp.get_param('trading_ai.news_pairs', '')
        whitelist = set(p.strip() for p in wl_param.split(',') if p.strip()) or _NEWS_DEFAULT_PAIRS

        accounts = self.env['trading.simulator'].search([('state', '=', 'active')])
        if not accounts:
            return

        from .daily_analysis import _fetch_finnhub_calendar
        events = _fetch_finnhub_calendar(finnhub_key, hours_ahead=1)   # endpoint returns today's events
        if not events:
            return

        now = fields.Datetime.now()
        opened = 0
        for sim in accounts:
          for ev in events:
            # only events that have already been released (actual present) and are recent
            actual, forecast = ev.get('actual', ''), ev.get('forecast', '')
            if actual in ('', None) or forecast in ('', None):
                continue
            ev_time = ev.get('time', '')
            try:
                ev_dt = dt.datetime.strptime(ev_time, '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                continue
            age_min = (now - ev_dt).total_seconds() / 60.0
            if age_min < 0 or age_min > lookback_min:        # not yet out, or too stale
                continue

            ccy = _NEWS_COUNTRY_CCY.get((ev.get('country') or '').upper())
            if not ccy or ccy not in _NEWS_CCY_PAIRS:
                continue

            surprise = _news_surprise_pct(actual, forecast)
            if surprise is None or abs(surprise) < thresh:
                continue

            # per-currency exposure cap: only one live news position per surprising
            # currency at a time, so correlated USD releases can't stack (FTMO daily).
            ccy_open = self.env['trading.sim_position'].search_count([
                ('simulator_id', '=', sim.id),
                ('signal_source', '=', 'news'),
                ('state', 'in', ('pending', 'open')),
                ('news_currency', '=', ccy),
            ])
            if ccy_open:
                _logger.info("News-surprise: %s already has a live position, skipping %s",
                             ccy, ev.get('event'))
                continue

            # dedupe this exact release (avoid re-firing on the same event each run)
            ev_hash = f"{ccy}:{ev.get('event','')}:{ev_time}"
            dup = self.env['trading.sim_position'].search_count([
                ('simulator_id', '=', sim.id),
                ('news_event_hash', '=', ev_hash),
            ])
            if dup:
                continue

            for pair, leg in _NEWS_CCY_PAIRS[ccy]:
                if pair not in whitelist:
                    continue
                # respect this account's instrument focus
                if not sim.allows_instrument(pair):
                    continue
                direction = _news_direction(ccy, surprise, ev.get('event', ''), leg)
                if not direction:
                    continue
                # skip if we already hold this instrument (any source)
                if self.env['trading.sim_position'].search_count([
                    ('simulator_id', '=', sim.id),
                    ('instrument', '=', pair),
                    ('state', 'in', ('pending', 'open')),
                ]):
                    continue
                # honour the global max-positions safety cap
                open_n = self.env['trading.sim_position'].search_count([
                    ('simulator_id', '=', sim.id), ('state', 'in', ('pending', 'open'))])
                if open_n >= max(config.max_positions, 1):
                    _logger.info("News-surprise: max positions reached, skipping %s", pair)
                    break

                self._open_news_position(sim, pair, direction, sl_floor, rr, ccy,
                                         ev.get('event', ''), surprise, ev_hash)
                opened += 1

        if opened:
            _logger.info("News-surprise: opened %d position(s)", opened)

    @api.model
    def _open_news_position(self, sim, pair, direction, sl_pct, rr, ccy, event, surprise, ev_hash):
        """Create a news position and open it immediately via the existing flow.
        Builds a pending position carrying the intended SL/TP % distances, then
        calls action_open_pending() which fetches live price, sizes from risk %,
        and reuses the breakeven/trailing/time-stop lifecycle."""
        # nominal entry/SL/TP so action_open_pending preserves our % distances
        ref = 100.0
        if direction == 'BUY':
            sl = ref * (1 - sl_pct);  tp = ref * (1 + rr * sl_pct)
        else:
            sl = ref * (1 + sl_pct);  tp = ref * (1 - rr * sl_pct)
        pos = self.env['trading.sim_position'].create({
            'simulator_id':    sim.id,
            'instrument':      pair,
            'inst_type':       'forex',
            'direction':       direction,
            'signal_source':   'news',
            'news_currency':   ccy,
            'news_event_hash': ev_hash,
            'state':           'pending',
            'scheduled_open_time': fields.Datetime.now(),
            'entry_price':     ref,
            'stop_loss':       round(sl, 6),
            'take_profit':     round(tp, 6),
            'ai_reasoning':    f"News-surprise: {ccy} {event} surprise {surprise:+.0%} -> {direction} {pair}",
        })
        self.env['trading.system_log'].log(
            'info', 'signal',
            f"📰 News-surprise {pair} {direction} ({ccy} {event} {surprise:+.0%})",
            instrument=pair)
        try:
            pos.action_open_pending()                 # opens at live price, sizes, sets 8h-ish hold
            # news trades are intraday — tighten the hold to ~8h
            pos.write({'max_hold_until': fields.Datetime.now() + dt.timedelta(hours=8)})
        except Exception as e:
            _logger.warning("News-surprise: could not open %s: %s", pair, e)
            pos.write({'state': 'cancelled', 'validity_notes': f'open failed: {e}'})

    def cron_timed_entry(self):
        """Every 30 min: open pending positions whose scheduled entry time has arrived."""
        config = self.get_singleton()
        if config.skip_weekends and dt.date.today().weekday() >= 5: return
        if not config.enabled: return

        now_nl  = _nl_now()
        now_utc = dt.datetime.utcnow()
        _logger.info("Timed entry check at %s NL", now_nl.strftime('%H:%M'))

        try:
            accounts = self.env['trading.simulator'].search([('state', '=', 'active')])
            if not accounts:
                return

            opened_total = 0
            all_log_lines = []
            for simulator in accounts:
                open_count = len(simulator.position_ids.filtered(lambda p: p.state == 'open'))
                if open_count >= config.max_positions:
                    continue

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

                        # ── Pre-flight checks (deterministic, learned rules) ───
                        if pos.result_id:
                            pf_ok, pf_reason = self._preflight_checks(pos, pos.result_id)
                            if not pf_ok:
                                pos.write({'state': 'cancelled',
                                           'validity_notes': f'Preflight blocked: {pf_reason[:250]}'})
                                log_lines.append(f"🚫 PREFLIGHT {instrument}: {pf_reason}")
                                _logger.info("Preflight blocked %s: %s", instrument, pf_reason)
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
                            f"📈 [{simulator.name}] {instrument} {pos.direction} OPENED at {now_nl.strftime('%H:%M')} NL "
                            f"(sched {sched_str}) | Score {pos.ai_score}/10 | {pos.ai_confidence}"
                        )
                        if verdict == 'WARN':
                            msg += f" | ⚠ {cortex_reason[:60]}"
                        log_lines.append(msg)
                        _logger.info(msg)

                    except Exception as e:
                        log_lines.append(f"⚠ {instrument}: {e}")
                        _logger.warning("Failed to open pending position %s: %s", instrument, e)

                opened_total += opened_this_run
                all_log_lines.extend(log_lines)

            if all_log_lines or opened_total:
                existing_log = config.last_run_log or ''
                new_section  = (
                    f"\n\n⏰ ENTRY CHECK {now_nl.strftime('%H:%M')} NL"
                    + (f" — {opened_total} position(s) opened" if opened_total else " — no entries yet")
                    + '\n' + '\n'.join(all_log_lines)
                )
                config.write({
                    'last_run_log':           existing_log + new_section,
                    'last_entry_check':       fields.Datetime.now(),
                    'positions_opened_today': (config.positions_opened_today or 0) + opened_total,
                    'total_auto_trades':      (config.total_auto_trades or 0) + opened_total,
                })
            else:
                config.write({'last_entry_check': fields.Datetime.now()})

        except Exception as e:
            _logger.error("Timed entry check failed: %s", e)

    # ─────────────────────────────────────────────────────────────────────────
    # Pre-trade Re-validation
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def _preflight_checks(self, pos, result):
        """
        Deterministic pre-flight gate — enforces the execution rules learned
        from the system's own logged mistakes. Runs BEFORE the AI revalidation.

        Closes these self-inflicted loss loops:
          1. Late entry / FOMO chase — live price drifted too far from signal entry
          2. News event — high-impact economic release within ±30 min
          3. Session — opening in low-liquidity hours for forex/crypto
          4. SL too tight — entry drift compressed the SL below the minimum

        Returns: (ok: bool, reason: str)
        Fails OPEN (returns True) on any data-fetch error so a transient
        outage doesn't silently kill all trades — the AI revalidation is the
        second layer of defence.
        """
        try:
            from .daily_analysis import (
                _fetch_forex_bars, _fetch_crypto_bars, _compute_indicators,
                _fetch_finnhub_calendar,
            )
            cfg = self.env['trading.config'].get_config()

            instrument = result.instrument
            inst_type  = (
                'crypto' if any(x in instrument for x in
                                ('USDT', 'BTC', 'ETH', 'SOL', 'XRP', 'BNB'))
                else 'index' if instrument in ('DIA', 'SPY', 'QQQ', 'EWG')
                else 'stock' if instrument in ('AAPL','TSLA','NVDA','MSFT','AMZN','META','GOOGL')
                else 'commodity' if '=F' in instrument
                else 'forex'
            )
            direction = 'BUY' if 'BUY' in (result.signal or '') else 'SELL'

            # ── Cortex-learned adjustments for this instrument ──────────
            try:
                cortex = self.env['trading.cortex'].get_singleton()
                cortex_adj = cortex.get_preflight_adjustments(instrument)
            except Exception:
                cortex_adj = {}
            sl_floor_mult  = cortex_adj.get('sl_floor_mult', 1.0)
            max_chase_mult = cortex_adj.get('max_chase_mult', 1.0)

            # ── Fetch live price ────────────────────────────────────────
            rows = []
            if inst_type == 'crypto':
                rows = _fetch_crypto_bars(instrument)
            elif inst_type in ('forex',):
                td_key = cfg.get('twelve_data_api_key', '')
                if td_key:
                    rows = _fetch_forex_bars(instrument, td_key)
            # stocks/commodities/indices: yfinance via _fetch_forex_bars fallback handled in revalidation
            if not rows or len(rows) < 5:
                return True, "preflight: insufficient live data — deferring to AI revalidation"

            indicators    = _compute_indicators(rows)
            current_price = indicators.get('current_price', 0)
            entry_price   = result.entry_price or 0
            stop_loss     = result.stop_loss or 0

            if not (current_price and entry_price):
                return True, "preflight: missing price — deferring"

            # ── RULE 1: Entry drift (late entry / FOMO chase) ───────────
            # The #1 self-inflicted loss: entering after price ran past the signal.
            # Max allowed drift in the SIGNAL DIRECTION before the edge is gone.
            drift_pct = (current_price - entry_price) / entry_price * 100
            # For a BUY, positive drift = price rose above entry (chasing).
            # For a SELL, negative drift = price fell below entry (chasing).
            chase_drift = drift_pct if direction == 'BUY' else -drift_pct
            MAX_CHASE = {
                'forex': 0.30, 'crypto': 0.60, 'stock': 0.50,
                'commodity': 0.50, 'index': 0.40,
            }.get(inst_type, 0.40) * max_chase_mult
            if chase_drift > MAX_CHASE:
                src = cortex_adj.get('chase_source', 'cortex')
                return False, (
                    f"CHASE BLOCK: price {current_price:.5g} drifted "
                    f"{chase_drift:+.2f}% past entry {entry_price:.5g} "
                    f"(max {MAX_CHASE:.2f}% for {inst_type}"
                    f"{f', tightened by {src}' if max_chase_mult < 1.0 else ''}) "
                    f"— edge gone, would be a FOMO entry"
                )

            # ── RULE 2: SL too tight after drift ────────────────────────
            # If price drifted toward entry such that the remaining SL distance
            # is below the minimum, the trade has no breathing room.
            MIN_SL = {
                'forex': 0.50, 'crypto': 1.20, 'stock': 1.50,
                'commodity': 1.50, 'index': 0.80,
            }.get(inst_type, 0.50) * sl_floor_mult
            if stop_loss:
                live_sl_dist = abs(current_price - stop_loss) / current_price * 100
                if live_sl_dist < MIN_SL * 0.6:  # 60% of min = dangerously tight
                    src = cortex_adj.get('sl_source', 'cortex')
                    return False, (
                        f"SL TOO TIGHT: live SL distance {live_sl_dist:.2f}% "
                        f"< {MIN_SL*0.6:.2f}% floor for {inst_type}"
                        f"{f', widened by {src}' if sl_floor_mult > 1.0 else ''} "
                        f"— would be stopped by normal noise"
                    )

            # ── RULE 3: High-impact news within ±30 min ─────────────────
            finnhub_key = cfg.get('finnhub_api_key', '')
            if finnhub_key:
                events = _fetch_finnhub_calendar(finnhub_key, hours_ahead=1)
                now = dt.datetime.utcnow()
                # Map instrument to relevant currency/country
                ccy_map = {
                    'forex': [c for c in instrument.replace('/', ' ').split() if len(c) == 3],
                    'commodity': ['US'],  # most commodity reports are US (EIA, etc.)
                    'stock': ['US'], 'index': ['US'],
                }
                relevant_ccy = ccy_map.get(inst_type, [])
                for e in events:
                    etime_str = e.get('time', '')
                    ecountry  = (e.get('country', '') or '').upper()
                    if not etime_str:
                        continue
                    # Match if event country/currency is relevant to this instrument
                    is_relevant = any(
                        c.upper() in ecountry or ecountry in c.upper()
                        for c in relevant_ccy
                    ) if relevant_ccy else False
                    if not is_relevant:
                        continue
                    try:
                        etime = dt.datetime.strptime(etime_str, '%Y-%m-%d %H:%M:%S')
                        mins_away = abs((etime - now).total_seconds()) / 60
                        if mins_away <= 30:
                            return False, (
                                f"NEWS BLOCK: high-impact '{e.get('event','?')}' "
                                f"({ecountry}) in {mins_away:.0f} min — "
                                f"scheduled volatility, skip entry"
                            )
                    except (ValueError, TypeError):
                        continue

            # ── RULE 4: Session check for forex (low-liquidity hours) ───
            # Asian session (low liquidity) for non-JPY/AUD/NZD forex pairs = risky
            if inst_type == 'forex':
                hour_utc = dt.datetime.utcnow().hour
                # London 07-16 UTC, NY 12-21 UTC. Dead zone: 21-07 UTC
                in_dead_zone = hour_utc >= 21 or hour_utc < 6
                is_asia_pair = any(c in instrument for c in ('JPY', 'AUD', 'NZD', 'SGD'))
                if in_dead_zone and not is_asia_pair:
                    return False, (
                        f"SESSION BLOCK: {hour_utc:02d}:00 UTC is low-liquidity for "
                        f"{instrument} — wait for London open (07:00 UTC)"
                    )

            return True, "preflight: all checks passed"

        except Exception as e:
            _logger.warning("Preflight error for %s (proceeding): %s",
                            getattr(result, 'instrument', '?'), e)
            return True, f"preflight error (proceeding): {e}"

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
        """Hourly — close positions that hit SL/TP or their time-stop deadline.
        Runs on weekends too: crypto trades 24/7, and deadline extensions for
        closed markets must still be processed."""
        config = self.get_singleton()
        if not config.enabled: return

        try:
            accounts = self.env['trading.simulator'].search([('state', '=', 'active')])
            if not accounts:
                return
            for simulator in accounts:
                open_pos = simulator.position_ids.filtered(lambda p: p.state == 'open')
                if not open_pos:
                    continue
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
                        # Derive real session from the linked signal if available
                        sess = 'unknown'
                        conf = trade.ai_confidence or 'MEDIUM'
                        cortex.learn_from_outcome(
                            instrument=trade.instrument,
                            outcome=trade.outcome,
                            session=sess,
                            confidence=conf,
                        )
                        # Feed mistake categories into the closed learning loop
                        if trade.outcome == 'LOSS' and trade.mistake_category:
                            cortex.learn_from_mistake(
                                instrument=trade.instrument,
                                mistake_category=trade.mistake_category,
                            )
                if today_closed:
                    log.append(
                        f"🧠 Cortex updated from {len(today_closed)} trade(s) today. "
                        f"State: {cortex.state}"
                    )
            except Exception as e:
                log.append(f"   Cortex update skipped: {e}")

            for simulator in self.env['trading.simulator'].search([('state', '=', 'active')]):
                try:
                    simulator.action_get_ai_review()
                    log.append(f"✅ AI Performance Review updated ({simulator.name})")
                except Exception as e:
                    log.append(f"   Review skipped ({simulator.name}): {e}")

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
    def cron_continue_batch(self):
        """
        Called by the batch-continuation one-shot cron.
        Resumes analysis of the next batch of instruments for the current session.
        After the FINAL batch completes (state=done), queues pending positions.
        """
        _logger.info("Batch Continue: resuming instrument analysis")
        icp = self.env['ir.config_parameter'].sudo()
        analysis_id = int(icp.get_param('trading_ai.batch_analysis_id', '0') or '0')
        if not analysis_id:
            _logger.warning("Batch Continue: no analysis_id found in config — aborting")
            return
        analysis = self.env['trading.daily_analysis'].sudo().browse(analysis_id)
        if not analysis.exists():
            _logger.warning("Batch Continue: analysis %d not found — aborting", analysis_id)
            return
        _logger.info("Batch Continue: resuming analysis '%s'", analysis.name)
        analysis.action_run_analysis()

        # Invalidate ORM cache so we read fresh state from DB
        # (action_run_analysis commits internally; calling cursor cache is stale)
        analysis.invalidate_recordset(['state'])
        _logger.info("Batch Continue: analysis state after run = '%s'", analysis.state)

        # After final batch: queue pending positions
        if analysis.state == 'done':
            _logger.info("Batch Continue: all batches done — queuing pending positions")
            config = self.get_singleton()
            queued = self._queue_pending_positions(analysis, config)
            _logger.info("Batch Continue: %d pending position(s) queued", queued)
            config.write({
                'last_run_log': (config.last_run_log or '') +
                    f"\n⏳ {queued} pending position(s) queued after all batches complete"
            })
        _logger.info("Batch Continue: done")

    @api.model
    def _disable_legacy_crons(self):
        """Called by XML <function> on every module upgrade to deactivate the
        old session-specific analysis crons, bypassing noupdate=True flags."""
        legacy_ext_ids = [
            'trading_ai_advisor.cron_daily_analysis',
            'trading_ai_advisor.cron_london_open',
            'trading_ai_advisor.cron_london_midmorning',
            'trading_ai_advisor.cron_pre_ny',
            'trading_ai_advisor.cron_ny_open',
            'trading_ai_advisor.cron_us_market_open',
            'trading_ai_advisor.cron_ny_midsession',
            'trading_ai_advisor.cron_ny_close_approach',
        ]
        for ext_id in legacy_ext_ids:
            cron = self.env.ref(ext_id, raise_if_not_found=False)
            if cron and cron.active:
                cron.sudo().write({'active': False})
                _logger.info("Disabled legacy analysis cron: %s", ext_id)

    def cron_execute_manual_run(self):
        """
        Executed by the one-shot cron created by action_run_now.
        Runs NY Open analysis, bypassing enabled/skip_weekends checks.

        Notes:
        - Odoo holds a row-lock on ir.cron during execution, so we CANNOT
          write to the cron record from here (causes deadlock → ERROR).
        - interval_number=999 days prevents normal re-firing.
        - If the worker is killed mid-run, the cron nextcall stays in the past
          and fires again after restart. The concurrent-run guard (_run_session_analysis)
          correctly skips that second firing as long as the first run is still
          in state=running. Once it times out (5 min), it auto-resets and the
          second firing proceeds as a fresh run — which is the correct behavior.
        """
        # Delete the one-shot cron record BEFORE running the analysis.
        # This prevents double-firing after worker restart because:
        # - We can't write to the currently-executing cron (row-locked by Odoo)
        # - But we CAN delete OTHER cron records in a savepoint
        # - Deleting it means a restarted worker won't find it in the scheduler
        # Note: Odoo row-locks the cron by ID during execution, but unlink of
        # OTHER cron records (or using a fresh cursor) works fine.
        try:
            # Use a fresh savepoint to unlink — this runs in same transaction
            # but Odoo only locks the specific row being executed, not all crons
            stale = self.env['ir.cron'].sudo().search(
                [('name', '=', 'Trading AI: Manual Run Now (one-shot)'),
                 ('id', '!=', self.env.context.get('_cron_id', -1))],
                limit=5)
            if stale:
                stale.unlink()
        except Exception:
            pass  # best-effort cleanup

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
        # Remove any stale one-shot AND batch-continue crons from previous runs
        # Safe to do here — this runs from an HTTP request, not from a cron
        stale = self.env['ir.cron'].sudo().search([
            '|',
            ('name', '=',    'Trading AI: Manual Run Now (one-shot)'),
            ('name', 'like', 'Trading AI: Batch Continue'),
        ])
        stale.unlink()
        # Also reset any leftover batch progress
        icp = self.env['ir.config_parameter'].sudo()
        icp.set_param('trading_ai.batch_analysis_id', '0')

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
