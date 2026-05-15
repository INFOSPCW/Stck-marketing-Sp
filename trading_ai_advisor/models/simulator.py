# -*- coding: utf-8 -*-
"""
simulator.py
============
Paper Trading Simulator — trade the AI signals with virtual money.

Workflow:
  1. Create a Simulator account (virtual balance, e.g. $10,000)
  2. Run Daily Analysis → click "Simulate Trade" on any signal
     → a SimPosition opens at the AI's entry price (live price fetched)
  3. Click "Check Positions" on the simulator to fetch current prices
     and automatically close positions that hit SL or TP
  4. Closed positions feed into the Trade Journal (TradeLog) automatically
  5. Claude AI reviews your performance and identifies patterns in your losses

No real money. No broker connection. Pure simulation.
"""

import json
import time
import logging
import urllib.request
import datetime as dt

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Live price fetch  (reuses Binance for crypto, Twelve Data for forex/indices)
# ─────────────────────────────────────────────────────────────────────────────

# Instrument symbol remap — handles stale symbols from older versions
_INSTRUMENT_REMAP = {
    'DAX': 'EWG', 'GER40': 'EWG',
    'DJI': 'DIA', 'US30': 'DIA',
    'SPX': 'SPY', 'SPX500': 'SPY',
    'NDX': 'QQQ', 'NAS100': 'QQQ',
    'XAG/USD': 'XAU/USD', 'WTI/USD': 'XAU/USD', 'XNG/USD': 'XAU/USD',
    'EUR/CHF': 'USD/NOK', 'NZD/JPY': 'USD/ZAR', 'AUD/CAD': 'EUR/CAD',
}

def _remap_instrument(instrument):
    """Return the current valid symbol for any legacy instrument name."""
    return _INSTRUMENT_REMAP.get(instrument, instrument)


# Expected price ranges per instrument — used to detect wrong/stale API data
_PRICE_SANITY = {
    # Forex majors
    'EUR/USD': (0.80, 1.80), 'GBP/USD': (0.80, 2.00), 'USD/JPY': (80,  200),
    'AUD/USD': (0.40, 1.20), 'USD/CAD': (0.80, 2.00), 'USD/CHF': (0.60, 1.60),
    'NZD/USD': (0.40, 1.00), 'USD/SGD': (1.00, 2.00),
    # Crosses
    'GBP/JPY': (100, 280),   'EUR/JPY': (80,  200),   'AUD/JPY': (60,  150),
    'CAD/JPY': (80,  140),   'EUR/GBP': (0.60, 1.20), 'GBP/CHF': (0.80, 2.00),
    'EUR/CAD': (1.20, 2.20), 'USD/NOK': (6.0, 16.0),  'USD/ZAR': (10,  30),
    'USD/MXN': (10,  30),    'USD/HKD': (7.5,  8.5),  'USD/SEK': (8.0, 14.0),
    # Commodities / Precious metals
    'XAU/USD': (2000, 9000),   # Gold — tightened: was 1500 low, now 2000 (gold hasn't been <$2k since 2023)
    'XAG/USD': (15,  100),
    # Index ETFs
    'SPY': (300, 1200), 'QQQ': (200, 1100), 'DIA': (300, 800), 'EWG': (15, 100),
    # Crypto
    'BTC/USDT':  (10000, 200000), 'ETH/USDT': (200, 20000),
    'SOL/USDT':  (5,     2000),   'XRP/USDT': (0.1,  50),
    'BNB/USDT':  (50,    5000),
    # US Stocks — wide enough for 5-year price history swings
    'AAPL':  (50,   1000),   # Apple    ~$150-240 range in 2025-26
    'TSLA':  (15,   2000),   # Tesla    volatile, wide range
    'NVDA':  (10,   2000),   # NVIDIA   post-split, volatile
    'MSFT':  (50,   1500),   # Microsoft ~$380-450
    'AMZN':  (50,   1000),   # Amazon   ~$180-240
    'META':  (50,   1500),   # Meta     ~$500-700
    'GOOGL': (50,   500),    # Alphabet ~$160-210
    # Commodity futures (Yahoo Finance =F tickers)
    # Energy
    'CL=F':  (20,   250),    # WTI Crude Oil $/barrel
    'BZ=F':  (20,   250),    # Brent Crude Oil $/barrel
    'NG=F':  (0.5,  30),     # Natural Gas $/MMBtu
    # Precious metals
    'SI=F':  (10,   200),    # Silver $/troy oz (currently ~$80)
    'GC=F':  (1500, 9000),   # Gold futures $/troy oz
    'HG=F':  (1.0,  20),     # Copper $/lb (currently ~$4-5)
    'PL=F':  (300,  2500),   # Platinum $/troy oz
    # Agriculturals (prices in cents/bushel or $/lb)
    'ZW=F':  (100,  2500),   # Wheat cents/bushel
    'ZC=F':  (100,  1500),   # Corn cents/bushel
    'KC=F':  (50,   700),    # Coffee cents/lb
}

def _price_in_sanity(instrument, price):
    """Return (ok, lo, hi) — True if price is within expected range for instrument."""
    r = _PRICE_SANITY.get(instrument)
    if not r:
        return True, None, None   # unknown instrument — allow through
    lo, hi = r
    return (lo <= price <= hi), lo, hi


def _get_live_price(instrument, inst_type, td_key='', env=None):
    """
    Fetch the current market price for an instrument.
    Validates the returned price is within the expected range for the
    instrument — prevents wrong/stale Twelve Data responses from
    silently opening positions at garbage prices.
    """
    if inst_type == 'crypto':
        symbol = instrument.replace('/', '')
        url    = (f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
        req    = urllib.request.Request(url, headers={"User-Agent": "TradingAI/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        price = float(data['price'])
    elif inst_type in ('stock', 'commodity'):
        # US stocks & commodity futures — yfinance (free, no key required)
        from .daily_analysis import _get_stock_live_price
        price = _get_stock_live_price(instrument)
    else:
        # forex / index — Twelve Data quote endpoint (1 call, instant)
        if not td_key:
            raise RuntimeError("Twelve Data key required for forex/index price.")
        from .daily_analysis import _TD_SYMBOL_MAP
        symbol = _TD_SYMBOL_MAP.get(instrument, instrument)
        url = (
            f"https://api.twelvedata.com/price"
            f"?symbol={symbol}&apikey={td_key}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "TradingAI/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if 'price' not in data:
            msg = data.get('message', str(data))
            raise RuntimeError(f"Twelve Data price error for {instrument}: {msg}")
        price = float(data['price'])

    # ── Sanity check: validate price is in expected range ──────────────────
    sanity = _PRICE_SANITY.get(instrument)
    ok, lo, hi = _price_in_sanity(instrument, price)
    if not ok:
        msg = (
            f"Price sanity check FAILED for {instrument}: "
            f"received {price:.6g} but expected range is {lo}–{hi}. "
            f"Twelve Data returned a wrong or stale value — position NOT opened."
        )
        if env is not None:
            env['trading.system_log'].log(
                'error', 'price', f"❌ Price sanity FAILED: {instrument} @ {price:.6g}",
                detail=msg, instrument=instrument
            )
        raise RuntimeError(msg)

    # Log successful price fetch
    if env is not None:
        env['trading.system_log'].log(
            'info', 'price', f"💰 Price fetched: {instrument} @ {price:.5g}",
            instrument=instrument
        )

    return price


def _claude_post_sim(api_key, payload, timeout=60):
    """Minimal Claude call for simulator review."""
    delay = 10
    body  = json.dumps(payload).encode()
    for attempt in range(1, 5):
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=body,
                headers={
                    "Content-Type":      "application/json",
                    "x-api-key":         api_key,
                    "anthropic-version": "2023-06-01",
                }, method="POST"
            )
            from urllib.error import HTTPError
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt < 4:
                time.sleep(delay); delay = min(delay * 2, 60)
            else:
                raise


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class TradingSimulator(models.Model):
    """
    A virtual trading account. One per user is typical.
    Tracks balance, equity, win rate across all simulated positions.
    """
    _name        = 'trading.simulator'
    _description = 'Paper Trading Simulator'
    _order       = 'create_date desc'
    _inherit     = ['mail.thread']

    name             = fields.Char(string='Account Name', required=True,
                                   default='My Paper Trading Account')
    starting_balance = fields.Float(string='Starting Balance ($)',
                                    default=10000.0, required=True)
    current_balance  = fields.Float(string='Current Balance ($)',
                                    default=10000.0)
    risk_per_trade   = fields.Float(string='Risk per Trade (%)',
                                    default=1.0,
                                    help='% of balance risked on each trade. '
                                         '1% is conservative, 2% moderate.')
    state = fields.Selection([
        ('active',   'Active'),
        ('paused',   'Paused'),
        ('archived', 'Archived'),
    ], default='active', string='Status', tracking=True)

    position_ids = fields.One2many(
        'trading.sim_position', 'simulator_id', string='Positions')

    # Stats — computed
    pending_positions = fields.Integer(compute='_compute_stats', store=False,
                                       string='Pending')
    open_positions   = fields.Integer(compute='_compute_stats', store=False)
    total_trades     = fields.Integer(compute='_compute_stats', store=False)
    winning_trades   = fields.Integer(compute='_compute_stats', store=False)
    losing_trades    = fields.Integer(compute='_compute_stats', store=False)
    win_rate         = fields.Float(compute='_compute_stats',   store=False,
                                    string='Win Rate (%)')
    total_pnl        = fields.Float(compute='_compute_stats',   store=False,
                                    string='Total P&L ($)')
    avg_win          = fields.Float(compute='_compute_stats',   store=False,
                                    string='Avg Win ($)')
    avg_loss         = fields.Float(compute='_compute_stats',   store=False,
                                    string='Avg Loss ($)')
    profit_factor    = fields.Float(compute='_compute_stats',   store=False,
                                    string='Profit Factor')
    equity_pct       = fields.Float(compute='_compute_stats',   store=False,
                                    string='Return (%)')
    ai_review        = fields.Text(string='AI Performance Review', readonly=True)
    last_check       = fields.Datetime(string='Last Position Check', readonly=True)

    @api.depends('position_ids.state', 'position_ids.pnl_usd',
                 'starting_balance', 'current_balance')
    def _compute_stats(self):
        for rec in self:
            pos       = rec.position_ids
            pending   = pos.filtered(lambda p: p.state == 'pending')
            open_pos  = pos.filtered(lambda p: p.state == 'open')
            closed    = pos.filtered(lambda p: p.state == 'closed')
            wins      = closed.filtered(lambda p: p.pnl_usd > 0)
            losses    = closed.filtered(lambda p: p.pnl_usd < 0)
            total_pnl = sum(closed.mapped('pnl_usd'))
            win_pnl   = sum(wins.mapped('pnl_usd'))
            loss_pnl  = abs(sum(losses.mapped('pnl_usd'))) or 0.001

            rec.pending_positions = len(pending)
            rec.open_positions = len(open_pos)
            rec.total_trades   = len(closed)
            rec.winning_trades = len(wins)
            rec.losing_trades  = len(losses)
            rec.win_rate       = round(len(wins) / len(closed) * 100, 1) if closed else 0
            rec.total_pnl      = round(total_pnl, 2)
            rec.avg_win        = round(win_pnl / len(wins),   2) if wins   else 0
            rec.avg_loss       = round(-abs(sum(losses.mapped('pnl_usd'))) / len(losses), 2) if losses else 0
            rec.profit_factor  = round(win_pnl / loss_pnl, 2)
            rec.equity_pct     = round((rec.current_balance - rec.starting_balance)
                                       / rec.starting_balance * 100, 2)

    def action_check_positions(self):
        """
        Fetch current live prices for all open positions and close any that
        hit their Stop Loss or Take Profit. Auto-creates TradeLog records.
        Called manually or can be triggered via scheduled action.
        """
        self.ensure_one()
        cfg    = self.env['trading.config'].get_config()
        td_key = cfg.get('twelve_data_api_key', '')

        open_pos = self.position_ids.filtered(lambda p: p.state == 'open')
        if not open_pos:
            return self._notify("No Open Positions",
                                "No open positions to check.", 'warning')

        log     = []
        closed  = 0
        balance = self.current_balance

        for pos in open_pos:
            pos_instrument = _remap_instrument(pos.instrument)
            if pos_instrument != pos.instrument:
                pos.write({'instrument': pos_instrument})
            try:
                live_price = _get_live_price(pos_instrument,
                                              pos.inst_type, td_key, env=self.env)
            except Exception as e:
                err_msg = f"⚠ {pos.instrument}: price fetch failed — {e}"
                log.append(err_msg)
                self.env['trading.system_log'].log(
                    'error', 'price', err_msg,
                    detail=str(e), instrument=pos.instrument
                )
                continue

            pos.write({'current_price': live_price})

            # Check TP / SL hit
            direction = pos.direction
            hit_tp = hit_sl = False

            if direction == 'BUY':
                if live_price >= pos.take_profit > 0:
                    hit_tp = True
                elif live_price <= pos.stop_loss > 0:
                    hit_sl = True
            else:  # SELL
                if live_price <= pos.take_profit > 0:
                    hit_tp = True
                elif live_price >= pos.stop_loss > 0:
                    hit_sl = True

            # Compute P&L in USD (simplified: price diff × position size)
            if hit_tp or hit_sl:
                exit_price = pos.take_profit if hit_tp else pos.stop_loss
                pips       = (exit_price - pos.entry_price) if direction == 'BUY' \
                             else (pos.entry_price - exit_price)
                pnl_pct    = pips / pos.entry_price * 100
                pnl_usd    = round(pos.position_size_usd * pnl_pct / 100, 2)
                outcome    = 'WIN' if pnl_usd > 0 else 'LOSS'
                reason     = 'TP hit' if hit_tp else 'SL hit'

                pos.write({
                    'state':       'closed',
                    'exit_price':  exit_price,
                    'exit_time':   fields.Datetime.now(),
                    'pnl_usd':     pnl_usd,
                    'pnl_pct':     round(pnl_pct, 3),
                    'outcome':     outcome,
                    'close_reason': reason,
                })
                balance += pnl_usd

                # Auto-create TradeLog record
                self.env['trading.trade_log'].create({
                    'trade_date':   fields.Date.today(),
                    'instrument':   _remap_instrument(pos.instrument or 'UNKNOWN')[:50],
                    'direction':    direction,
                    'outcome':      outcome,
                    'entry_price':  pos.entry_price,
                    'exit_price':   exit_price,
                    'stop_loss':    pos.stop_loss,
                    'take_profit':  pos.take_profit,
                    'lot_size':     pos.position_size_usd,
                    'pnl':          pnl_pct,
                    'result_id':    pos.result_id.id if pos.result_id else False,
                    'mistake_category': '' if outcome == 'WIN' else 'other',
                })

                closed += 1
                pnl_str = f"+${pnl_usd:.2f}" if pnl_usd > 0 else f"-${abs(pnl_usd):.2f}"
                close_msg = (
                    f"{'✅' if outcome == 'WIN' else '❌'} {pos.instrument} "
                    f"{direction} CLOSED — {reason} @ {exit_price:.5g} "
                    f"| P&L: {pnl_str}"
                )
                log.append(close_msg)
                self.env['trading.system_log'].log(
                    'success' if outcome == 'WIN' else 'error',
                    'position', close_msg,
                    detail=f"Entry: {pos.entry_price:.5g} | SL: {pos.stop_loss:.5g} | TP: {pos.take_profit:.5g} | Size: ${pos.position_size_usd:,.0f}",
                    instrument=pos.instrument
                )
            else:
                unrealised = (live_price - pos.entry_price) if direction == 'BUY' \
                             else (pos.entry_price - live_price)
                ur_pct  = round(unrealised / pos.entry_price * 100, 3)
                ur_usd  = round(pos.position_size_usd * ur_pct / 100, 2)
                sign    = '+' if ur_usd >= 0 else ''
                log.append(
                    f"📊 {pos.instrument} {direction} OPEN @ {pos.entry_price:.5g} "
                    f"| Now: {live_price:.5g} | Unrealised: {sign}${ur_usd:.2f}"
                )

        self.write({
            'current_balance': round(balance, 2),
            'last_check':      fields.Datetime.now(),
        })
        self.message_post(body="\n".join(log))
        self.write({'last_check': fields.Datetime.now()})

        msg = f"Checked {len(open_pos)} positions. "
        if closed:
            msg += f"{closed} closed. New balance: ${balance:,.2f}"
        else:
            msg += "None hit SL/TP yet."

        return self._notify("✅ Position Check Complete", msg, 'success')

    def action_get_ai_review(self):
        """Ask Claude to review all closed positions and find patterns."""
        self.ensure_one()
        cfg     = self.env['trading.config'].get_config()
        api_key = cfg.get('anthropic_api_key', '')
        if not api_key:
            raise UserError("Anthropic API key required.")

        # Gather closed sim positions
        closed_pos = self.position_ids.filtered(lambda p: p.state == 'closed')

        # Also pull from Trade Journal as fallback / supplement
        journal_logs = self.env['trading.trade_log'].sudo().search(
            [('outcome', 'in', ('WIN', 'LOSS', 'BREAKEVEN'))],
            order='trade_date desc', limit=50
        )

        # Build unified trade list from both sources
        trade_lines = []
        for p in closed_pos.sorted(key=lambda x: x.exit_time or x.open_time):
            trade_lines.append(
                f"{(p.exit_time or p.open_time).strftime('%Y-%m-%d') if (p.exit_time or p.open_time) else '?'} | "
                f"{p.instrument} | {p.direction} | "
                f"Entry {p.entry_price:.5g} Exit {p.exit_price:.5g} | "
                f"P&L ${p.pnl_usd:+.2f} ({p.pnl_pct:+.3f}%) | "
                f"{p.outcome} ({p.close_reason or 'N/A'}) | "
                f"AI score: {p.ai_score}/10"
            )

        for lg in journal_logs:
            # Skip if already covered by sim position (same instrument+date)
            trade_lines.append(
                f"{lg.trade_date} | {lg.instrument} | {lg.direction} | "
                f"Entry {lg.entry_price:.5g} Exit {lg.exit_price:.5g} | "
                f"P&L {lg.pnl:+.2f}% | {lg.outcome} | "
                f"Mistake: {lg.mistake_category or 'N/A'}"
            )

        if not trade_lines:
            raise UserError(
                "No trade data found anywhere.\n\n"
                "You need at least one closed trade to get a review. "
                "Run a Daily Analysis, click 'Simulate Trade' on a signal, "
                "then click '🔄 Check Positions' to let the simulator close it when SL/TP is hit.\n\n"
                "Or use 'Close at Market' on any open position to close it manually right now."
            )

        summary = "\n".join(trade_lines)

        # Stats from what we have
        all_wins    = len(closed_pos.filtered(lambda p: p.pnl_usd > 0)) + \
                      len(journal_logs.filtered(lambda l: l.outcome == 'WIN'))
        all_losses  = len(closed_pos.filtered(lambda p: p.pnl_usd < 0)) + \
                      len(journal_logs.filtered(lambda l: l.outcome == 'LOSS'))
        all_trades  = all_wins + all_losses
        wr          = round(all_wins / all_trades * 100, 1) if all_trades else 0
        total_pnl   = sum(closed_pos.mapped('pnl_usd'))

        prompt = f"""You are a trading coach reviewing a student's paper trading account.

ACCOUNT STATS:
- Starting balance: ${self.starting_balance:,.2f}
- Current balance:  ${self.current_balance:,.2f}
- Total P&L:        ${total_pnl:+,.2f}
- Win rate:         {wr}% ({all_wins} wins, {all_losses} losses, {all_trades} total)

TRADE HISTORY:
{summary}

Please provide a constructive performance review:
1. Overall assessment (2-3 sentences on results so far)
2. What's working (patterns in winning trades, even if only 1-2)
3. What needs improvement (patterns in losing trades)
4. One specific actionable recommendation for the next session
5. Risk management score out of 10 with brief reason

If there are very few trades, acknowledge that and focus on the available data.
Keep it concise and practical. Max 300 words."""

        try:
            data   = _claude_post_sim(api_key, {
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "messages":   [{"role": "user", "content": prompt}]
            })
            review = data['content'][0]['text']
            self.write({'ai_review': review})
            self.message_post(body=f"🤖 AI Review:\n{review}")
            return self._notify("🤖 AI Review Ready",
                                "Review saved to the account record.", 'success')
        except Exception as e:
            raise UserError(f"AI review failed: {e}")

    def action_reset_account(self):
        """Reset balance to starting amount and clear all positions."""
        self.ensure_one()
        self.position_ids.unlink()
        self.write({
            'current_balance': self.starting_balance,
            'ai_review':       '',
        })
        return self._notify("Account Reset",
                            f"Balance reset to ${self.starting_balance:,.2f}.", 'info')

    def action_verify_and_fix_positions(self):
        """
        Full sanity audit of all open AND closed positions.

        For each position:
        1. Check if entry_price / exit_price pass the _PRICE_SANITY range test
        2. If entry_price is clearly wrong (e.g. EUR/CAD at 0.97, XAU at 4878):
           - For OPEN positions: fetch real live price, correct entry + recalc SL/TP
           - For CLOSED positions: flag as INVALID, set outcome to 'DATA_ERROR',
             reverse the P&L impact from account balance
        3. Log every fix to the System Log
        """
        self.ensure_one()
        cfg    = self.env['trading.config'].get_config()
        td_key = cfg.get('twelve_data_api_key', '')

        report    = []
        fixed_open  = 0
        fixed_closed = 0
        balance_adj  = 0.0

        for pos in self.position_ids:
            entry  = pos.entry_price
            exit_p = pos.exit_price or 0
            inst   = pos.instrument

            entry_ok, lo, hi = _price_in_sanity(inst, entry)
            exit_ok          = True
            if exit_p and exit_p > 0:
                exit_ok, _, _ = _price_in_sanity(inst, exit_p)

            if entry_ok and exit_ok:
                continue   # position looks fine

            # ── OPEN position with bad entry price ────────────────────────
            if pos.state == 'open' and not entry_ok:
                try:
                    live = _get_live_price(inst, pos.inst_type or 'forex', td_key, self.env)
                except Exception as e:
                    report.append(f"⚠ {inst} OPEN: bad entry {entry:.5g} "
                                  f"(expected {lo}–{hi}) — could not fetch live price: {e}")
                    self.env['trading.system_log'].log(
                        'error', 'price',
                        f"❌ Verify failed: {inst} bad entry {entry:.5g}, price fetch error",
                        detail=str(e), instrument=inst)
                    continue

                # Recalculate SL/TP keeping same % distances from new live price
                direction = pos.direction
                old_sl    = pos.stop_loss
                old_tp    = pos.take_profit
                old_entry = entry

                if old_sl and old_entry:
                    sl_pct = abs(old_entry - old_sl) / old_entry
                    new_sl = round(live * (1 - sl_pct) if direction == 'BUY'
                                   else live * (1 + sl_pct), 6)
                else:
                    new_sl = round(live * 0.99 if direction == 'BUY'
                                   else live * 1.01, 6)

                if old_tp and old_entry:
                    tp_pct = abs(old_tp - old_entry) / old_entry
                    new_tp = round(live * (1 + tp_pct) if direction == 'BUY'
                                   else live * (1 - tp_pct), 6)
                else:
                    new_tp = round(live * 1.02 if direction == 'BUY'
                                   else live * 0.98, 6)

                pos.write({
                    'entry_price':   live,
                    'current_price': live,
                    'stop_loss':     new_sl,
                    'take_profit':   new_tp,
                })
                fixed_open += 1
                msg = (f"🔧 FIXED OPEN {inst} {direction}: "
                       f"entry corrected {old_entry:.5g} → {live:.5g} "
                       f"| SL: {old_sl:.5g}→{new_sl:.5g} "
                       f"| TP: {old_tp:.5g}→{new_tp:.5g}")
                report.append(msg)
                self.env['trading.system_log'].log(
                    'warning', 'position', msg, instrument=inst,
                    detail=f"Old entry: {old_entry} | Real price: {live} | "
                           f"Expected range: {lo}–{hi}")

            # ── CLOSED position with bad entry or exit price ───────────────
            elif pos.state == 'closed' and (not entry_ok or not exit_ok):
                bad_price = entry if not entry_ok else exit_p
                bad_field = 'entry' if not entry_ok else 'exit'
                old_pnl   = pos.pnl_usd or 0

                # Reverse the erroneous P&L from account balance
                balance_adj -= old_pnl

                pos.write({
                    'outcome':       'DATA_ERROR',
                    'close_reason':  f'Invalidated: {bad_field} price {bad_price:.5g} '
                                     f'outside expected range {lo}–{hi}',
                    'pnl_usd':       0,
                    'pnl_pct':       0,
                })
                fixed_closed += 1
                msg = (f"❌ INVALIDATED CLOSED {inst}: "
                       f"{bad_field} {bad_price:.5g} outside range {lo}–{hi} "
                       f"| P&L reversed: {old_pnl:+.2f}")
                report.append(msg)
                self.env['trading.system_log'].log(
                    'error', 'position', msg, instrument=inst,
                    detail=f"Bad {bad_field} price: {bad_price} | Valid range: {lo}–{hi} | "
                           f"P&L was {old_pnl:+.2f} (reversed from balance)")

        # Apply balance correction for invalidated closed positions
        if balance_adj != 0:
            new_balance = round(self.current_balance + balance_adj, 2)
            self.write({'current_balance': new_balance})
            report.append(f"💰 Balance corrected by {balance_adj:+.2f} "
                          f"(removed invalid P&L) → new balance: ${new_balance:,.2f}")

        # Also fix Trade Journal records with invalid prices
        tl_fixed = 0
        for log_rec in self.env['trading.trade_log'].sudo().search([]):
            entry_ok, lo, hi = _price_in_sanity(log_rec.instrument, log_rec.entry_price or 0)
            if not entry_ok and lo and hi:
                log_rec.write({
                    'outcome':           'INVALID',
                    'what_went_wrong':  (f"Entry price {log_rec.entry_price:.5g} is outside "
                                        f"the valid range for {log_rec.instrument} "
                                        f"({lo}–{hi}). This was a module price feed error "
                                        f"(Twelve Data returned wrong value). Trade did not "
                                        f"occur at this price."),
                    'mistake_category': 'other',
                })
                tl_fixed += 1
                self.env['trading.system_log'].log(
                    'error', 'system',
                    f"❌ Trade Journal INVALIDATED: {log_rec.instrument} entry {log_rec.entry_price:.5g}",
                    detail=f"Valid range: {lo}–{hi}. Module price feed error.",
                    instrument=log_rec.instrument)

        if not report and tl_fixed == 0:
            return self._notify(
                '✅ All positions valid',
                'Every position passed the price sanity check. No corrections needed.',
                'success'
            )

        summary = (f"Fixed {fixed_open} open position(s), "
                   f"invalidated {fixed_closed} closed position(s), "
                   f"{tl_fixed} Trade Journal record(s) flagged.")
        report_body = "\U0001f527 Sanity Check Report:\n" + "\n".join(report)
        self.message_post(body=report_body)

        return self._notify(
            '🔧 Sanity Check Complete',
            summary,
            'warning' if (fixed_closed or tl_fixed) else 'success'
        )

    def action_fix_stale_instruments(self):
        """
        Emergency fix: remaps all stale instrument symbols in sim positions.
        Run this BEFORE upgrading if you see 'Wrong value for instrument' errors.
        """
        self.ensure_one()
        fixed = []
        for pos in self.position_ids.filtered(lambda p: p.state == 'open'):
            new_inst = _remap_instrument(pos.instrument)
            if new_inst != pos.instrument:
                pos.write({'instrument': new_inst})
                fixed.append(f"{pos.instrument} → {new_inst}")

        # Also fix any closed positions
        for pos in self.position_ids.filtered(lambda p: p.state == 'closed'):
            new_inst = _remap_instrument(pos.instrument)
            if new_inst != pos.instrument:
                pos.write({'instrument': new_inst})
                fixed.append(f"(closed) {pos.instrument} → {new_inst}")

        msg = f"Fixed {len(fixed)} position(s): {', '.join(fixed)}" if fixed               else "No stale instruments found — all positions look clean."
        return self._notify("🔧 Stale Instruments Fixed", msg,
                            'success' if fixed else 'info')

    def _notify(self, title, message, ntype='info'):
        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   title,
                'message': message,
                'sticky':  ntype in ('success', 'warning'),
                'type':    ntype,
            },
        }


class SimPosition(models.Model):
    """
    A single simulated trade position — opened from a Daily Analysis signal,
    tracked in real time, closed automatically when SL/TP is hit.
    """
    _name        = 'trading.sim_position'
    _description = 'Simulated Trade Position'
    _order       = 'open_time desc'

    simulator_id = fields.Many2one(
        'trading.simulator', string='Account',
        ondelete='cascade', required=True)
    result_id    = fields.Many2one(
        'trading.daily_result', string='Signal Source')
    analysis_id  = fields.Many2one(
        'trading.daily_analysis', string='Analysis Session',
        related='result_id.analysis_id', store=True)

    name       = fields.Char(compute='_compute_name', store=True)
    instrument = fields.Char(string='Instrument', required=True)
    inst_type  = fields.Selection(
        [('forex','Forex'),('crypto','Crypto'),('index','Index'),('stock','Stock'),('commodity','Commodity')],
        string='Type')
    direction  = fields.Selection(
        [('BUY','⬆ BUY'),('SELL','⬇ SELL')], string='Direction', required=True)
    state      = fields.Selection([
        ('pending',   '⏳ Pending'),
        ('open',      '🟢 Open'),
        ('closed',    '⬜ Closed'),
        ('cancelled', '❌ Cancelled'),
    ], default='open', string='State')

    # Scheduled entry (pending state)
    scheduled_open_time = fields.Datetime(string='Scheduled Open At')
    validity_notes      = fields.Char(string='Validity Check', readonly=True)

    # Entry
    open_time         = fields.Datetime(string='Opened At', default=fields.Datetime.now)
    entry_price       = fields.Float(string='Entry Price',  digits=(16, 6))
    stop_loss         = fields.Float(string='Stop Loss',    digits=(16, 6))
    take_profit       = fields.Float(string='Take Profit',  digits=(16, 6))
    position_size_usd = fields.Float(string='Position Size ($)',
                                     help='Dollar value risked on this trade')
    ai_score          = fields.Integer(string='AI Score')
    ai_confidence     = fields.Char(string='AI Confidence')
    ai_reasoning      = fields.Text(string='AI Reasoning')

    # Live tracking
    current_price = fields.Float(string='Current Price', digits=(16, 6))
    unrealised_pnl = fields.Float(
        string='Unrealised P&L ($)', compute='_compute_unrealised', store=False)

    # Exit
    exit_time    = fields.Datetime(string='Closed At')
    exit_price   = fields.Float(string='Exit Price',  digits=(16, 6))
    close_reason = fields.Char(string='Close Reason')
    outcome      = fields.Selection([
        ('WIN','✅ Win'), ('LOSS','❌ Loss'), ('BREAKEVEN','➡ Breakeven'),
        ('DATA_ERROR','⛔ Invalid — Price Feed Error'),
    ], string='Outcome')
    pnl_usd = fields.Float(string='P&L ($)',  digits=(10, 2))
    pnl_pct = fields.Float(string='P&L (%)', digits=(8,  3))
    risk_reward_actual = fields.Float(
        string='Actual R/R', compute='_compute_actual_rr', store=True)

    color = fields.Integer(compute='_compute_color', store=False)

    @api.depends('instrument', 'direction', 'open_time', 'scheduled_open_time', 'state')
    def _compute_name(self):
        for rec in self:
            if rec.state == 'pending' and rec.scheduled_open_time:
                ts = rec.scheduled_open_time.strftime('%m/%d %H:%M')
                rec.name = f"{rec.instrument or '?'} {rec.direction or '?'} PENDING @ {ts}"
            else:
                ts = rec.open_time.strftime('%m/%d %H:%M') if rec.open_time else '?'
                rec.name = f"{rec.instrument or '?'} {rec.direction or '?'} @ {ts}"

    @api.depends('current_price', 'entry_price', 'direction', 'position_size_usd')
    def _compute_unrealised(self):
        for rec in self:
            if rec.state != 'open' or not rec.current_price or not rec.entry_price:
                rec.unrealised_pnl = 0
                continue
            diff = (rec.current_price - rec.entry_price) if rec.direction == 'BUY' \
                   else (rec.entry_price - rec.current_price)
            rec.unrealised_pnl = round(
                rec.position_size_usd * diff / rec.entry_price, 2)

    @api.depends('entry_price', 'exit_price', 'stop_loss')
    def _compute_actual_rr(self):
        for rec in self:
            if rec.entry_price and rec.exit_price and rec.stop_loss:
                risk   = abs(rec.entry_price - rec.stop_loss)
                reward = abs(rec.exit_price  - rec.entry_price)
                rec.risk_reward_actual = round(reward / risk, 2) if risk > 0 else 0
            else:
                rec.risk_reward_actual = 0

    @api.depends('outcome', 'state')
    def _compute_color(self):
        for rec in self:
            if rec.state == 'pending':
                rec.color = 2   # yellow/orange
            elif rec.state == 'open':
                rec.color = 3   # blue-ish
            elif rec.outcome == 'WIN':
                rec.color = 10  # green
            elif rec.outcome == 'LOSS':
                rec.color = 1   # red
            else:
                rec.color = 0

    def action_close_manual(self):
        """Manually close a position at current market price."""
        self.ensure_one()
        if self.state != 'open':
            raise UserError("Position is already closed.")
        cfg    = self.env['trading.config'].get_config()
        td_key = cfg.get('twelve_data_api_key', '')
        try:
            live = _get_live_price(self.instrument, self.inst_type, td_key)
        except Exception as e:
            raise UserError(f"Could not fetch live price: {e}")

        pips    = (live - self.entry_price) if self.direction == 'BUY' \
                  else (self.entry_price - live)
        pnl_pct = pips / self.entry_price * 100
        pnl_usd = round(self.position_size_usd * pnl_pct / 100, 2)
        outcome = 'WIN' if pnl_usd > 0 else ('BREAKEVEN' if pnl_usd == 0 else 'LOSS')

        self.write({
            'state':       'closed',
            'exit_price':  live,
            'exit_time':   fields.Datetime.now(),
            'pnl_usd':     pnl_usd,
            'pnl_pct':     round(pnl_pct, 3),
            'outcome':     outcome,
            'close_reason': 'Manual close',
        })

        bal = self.simulator_id.current_balance + pnl_usd
        self.simulator_id.write({'current_balance': round(bal, 2)})

        self.env['trading.trade_log'].create({
            'trade_date':  fields.Date.today(),
            'instrument':  self.instrument,
            'direction':   self.direction,
            'outcome':     outcome,
            'entry_price': self.entry_price,
            'exit_price':  live,
            'stop_loss':   self.stop_loss,
            'take_profit': self.take_profit,
            'lot_size':    self.position_size_usd,
            'pnl':         pnl_pct,
            'result_id':   self.result_id.id if self.result_id else False,
        })

        sign = '+' if pnl_usd >= 0 else ''
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   f"{'✅ WIN' if outcome=='WIN' else '❌ LOSS'} — {self.instrument}",
                'message': f"Closed at {live:.5g} | P&L: {sign}${pnl_usd:.2f}",
                'sticky':  True,
                'type':    'success' if outcome == 'WIN' else 'danger',
            },
        }

    def action_open_pending(self):
        """Open a pending position at current market price."""
        self.ensure_one()
        if self.state != 'pending':
            raise UserError("Only pending positions can be opened this way.")

        cfg    = self.env['trading.config'].get_config()
        td_key = cfg.get('twelve_data_api_key', '')

        try:
            live = _get_live_price(self.instrument, self.inst_type or 'forex', td_key, self.env)
        except Exception as e:
            raise UserError(f"Could not fetch live price for {self.instrument}: {e}")

        direction = self.direction
        ai_entry  = self.entry_price or live
        ai_sl     = self.stop_loss  or 0
        ai_tp     = self.take_profit or 0

        # Preserve AI's % distances from its suggested entry, applied to live price
        if ai_sl > 0 and ai_entry > 0:
            sl_pct = abs(ai_entry - ai_sl) / ai_entry
            sl = round(live * (1 - sl_pct) if direction == 'BUY' else live * (1 + sl_pct), 6)
        else:
            sl = round(live * 0.99 if direction == 'BUY' else live * 1.01, 6)

        if ai_tp > 0 and ai_entry > 0:
            tp_pct = abs(ai_tp - ai_entry) / ai_entry
            tp = round(live * (1 + tp_pct) if direction == 'BUY' else live * (1 - tp_pct), 6)
        else:
            tp = round(live * 1.02 if direction == 'BUY' else live * 0.98, 6)

        # Ensure SL/TP on correct sides
        if direction == 'BUY':
            if sl >= live: sl = round(live * 0.99, 6)
            if tp <= live: tp = round(live * 1.02, 6)
        else:
            if sl <= live: sl = round(live * 1.01, 6)
            if tp >= live: tp = round(live * 0.98, 6)

        # Size from live price
        simulator = self.simulator_id
        risk_pct  = simulator.risk_per_trade / 100
        risk_usd  = simulator.current_balance * risk_pct
        sl_dist   = abs(live - sl)
        pos_size  = round((risk_usd / sl_dist) * live, 2) if sl_dist > 0 else risk_usd * 10

        slippage  = round(abs(live - ai_entry) / ai_entry * 100, 2) if ai_entry else 0
        self.write({
            'state':             'open',
            'entry_price':       live,
            'current_price':     live,
            'stop_loss':         sl,
            'take_profit':       tp,
            'position_size_usd': pos_size,
            'open_time':         fields.Datetime.now(),
            'validity_notes':    f"Opened {live:.5g} (AI {ai_entry:.5g}, slip {slippage:.2f}%)",
        })

        self.env['trading.system_log'].log(
            'success', 'position',
            f"📈 Pending→Open: {self.instrument} {direction} @ {live:.5g}",
            detail=(f"AI entry: {ai_entry:.5g} | Live: {live:.5g} | Slip: {slippage:.2f}% | "
                    f"SL: {sl:.5g} | TP: {tp:.5g} | Size: ${pos_size:,.0f}"),
            instrument=self.instrument
        )

        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   f'📈 Position Opened — {self.instrument}',
                'message': f"{direction} @ {live:.5g} | SL: {sl:.5g} | TP: {tp:.5g}",
                'sticky': True, 'type': 'success',
            },
        }

    def action_cancel_pending(self):
        """Cancel a pending position before it opens."""
        self.ensure_one()
        if self.state != 'pending':
            raise UserError("Only pending positions can be cancelled here.")
        self.write({
            'state':         'cancelled',
            'validity_notes': 'Cancelled manually before opening.',
        })
        self.env['trading.system_log'].log(
            'warning', 'position',
            f"❌ Pending cancelled: {self.instrument} {self.direction}",
            instrument=self.instrument
        )
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   'Position Cancelled',
                'message': f'{self.instrument} pending position cancelled.',
                'sticky':  False, 'type': 'warning',
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Overnight Decision Review — added in v22
# ─────────────────────────────────────────────────────────────────────────────

class SimPositionOvernight(models.Model):
    """Extends SimPosition with overnight hold/close decision tooling."""
    _inherit = 'trading.sim_position'

    # Overnight fields
    hold_overnight       = fields.Selection([
        ('pending',  '⏳ Decision Pending'),
        ('hold',     '🌙 Hold Overnight'),
        ('close_eod','📅 Close End of Day'),
    ], string='Overnight Decision', default='pending',
       help='Your decision: hold the position overnight or close before end of day.')

    hold_overnight_ai    = fields.Boolean(string='AI Recommends Overnight',
        help='The AI flagged this signal as suitable for holding overnight.')
    overnight_ai_advice  = fields.Text(string='AI Overnight Analysis',
        help='Claude\'s assessment of whether to hold or close this position overnight.')
    overnight_reviewed   = fields.Boolean(string='Overnight Review Done', default=False)

    def action_review_overnight(self):
        """
        Ask Claude whether to hold this position overnight or close it now.
        Considers: current P&L, distance to SL/TP, time of day, instrument volatility,
        upcoming sessions, and the original signal reasoning.
        """
        self.ensure_one()
        if self.state != 'open':
            raise UserError("Position is already closed.")

        cfg     = self.env['trading.config'].get_config()
        api_key = cfg.get('anthropic_api_key', '')
        td_key  = cfg.get('twelve_data_api_key', '')
        if not api_key:
            raise UserError("Anthropic API key missing — check Configuration.")

        from .simulator import _get_live_price, _claude_post_sim
        import datetime as dt, calendar

        # Fetch current live price
        try:
            live = _get_live_price(self.instrument, self.inst_type or 'forex', td_key)
            self.write({'current_price': live})
        except Exception as e:
            live = self.current_price or self.entry_price

        # Compute current unrealised P&L
        diff = (live - self.entry_price) if self.direction == 'BUY' \
               else (self.entry_price - live)
        pnl_pct = round(diff / self.entry_price * 100, 3) if self.entry_price else 0
        pnl_usd = round(self.position_size_usd * pnl_pct / 100, 2)

        # Distance to SL and TP as %
        sl_dist = round(abs(live - self.stop_loss) / live * 100, 3) if self.stop_loss else 0
        tp_dist = round(abs(self.take_profit - live) / live * 100, 3) if self.take_profit else 0

        # Current NL time
        now_utc = dt.datetime.utcnow()
        def last_sun(yr, mo):
            ld = calendar.monthrange(yr, mo)[1]
            d  = dt.date(yr, mo, ld)
            return d - dt.timedelta(days=d.weekday() + 1 if d.weekday() != 6 else 0)
        offset  = 2 if last_sun(now_utc.year,3) <= now_utc.date() < last_sun(now_utc.year,10) else 1
        tz_lbl  = 'CEST' if offset == 2 else 'CET'
        nl_time = (now_utc + dt.timedelta(hours=offset)).strftime('%H:%M')

        # Original signal context
        signal_ctx = ''
        if self.result_id:
            r = self.result_id
            signal_ctx = (
                f"\nORIGINAL AI SIGNAL:\n"
                f"  Signal: {r.signal} | Score: {r.score}/10 | Confidence: {r.confidence}\n"
                f"  Entry: {r.entry_price} | SL: {r.stop_loss} | TP: {r.take_profit}\n"
                f"  AI originally said hold_overnight: {getattr(r, 'hold_overnight_ai', 'N/A')}\n"
                f"  Reasoning: {r.reasoning}\n"
                f"  Session advice: {r.session_advice}"
            )

        prompt = f"""You are a trading coach reviewing an open paper trade position.
The trader needs to decide: HOLD overnight into tomorrow's session, or CLOSE now.

POSITION DATA:
  Instrument:      {self.instrument} ({self.inst_type})
  Direction:       {self.direction}
  Entry price:     {self.entry_price:.6g}
  Current price:   {live:.6g}
  Stop Loss:       {self.stop_loss:.6g}  ({sl_dist:.2f}% away)
  Take Profit:     {self.take_profit:.6g}  ({tp_dist:.2f}% away)
  Unrealised P&L:  {'+' if pnl_usd >= 0 else ''}{pnl_usd:.2f} USD ({pnl_pct:+.3f}%)
  Position size:   ${self.position_size_usd:,.0f}
  Current time:    {nl_time} {tz_lbl} / {now_utc.strftime('%H:%M')} GMT
  Opened at:       {self.open_time.strftime('%Y-%m-%d %H:%M') if self.open_time else 'N/A'}
{signal_ctx}

OVERNIGHT CONSIDERATIONS FOR {self.instrument}:
  - Forex: spreads widen overnight, swap costs apply, but strong trends often continue
  - Crypto: trades 24/7, no overnight gap risk, but volatile during Asian session
  - US Indices (SPY/QQQ/DIA): gap risk on open — earnings/news can cause large gaps
  - Commodities (XAU/USD): relatively safe overnight with tight SL
  - Tomorrow's market open: London at 09:00 NL, NY at 15:00 NL

Analyse and return ONLY valid JSON:
{{
  "decision":        "HOLD" | "CLOSE",
  "confidence":      "HIGH" | "MEDIUM" | "LOW",
  "reasoning":       "<3-4 sentences explaining the decision with specific reference to price levels and current P&L>",
  "risk_if_hold":    "<one sentence — what could go wrong overnight>",
  "opportunity_if_hold": "<one sentence — what could go right overnight>",
  "suggested_action": "<one specific actionable sentence, e.g. 'Move SL to breakeven at {entry} before holding'>",
  "revised_sl":      <float|null — if holding, suggest moving SL to lock in gains or reduce risk>,
  "revised_tp":      <float|null — if holding, suggest adjusting TP for overnight move>
}}"""

        try:
            import json, re
            data  = _claude_post_sim(api_key, {
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "messages":   [{"role": "user", "content": prompt}]
            })
            raw   = data['content'][0]['text']
            clean = re.sub(r'^```[a-z]*\s*|\s*```$', '', raw.strip())
            r     = json.loads(clean)

            decision    = r.get('decision', 'HOLD')
            reasoning   = r.get('reasoning', '')
            risk        = r.get('risk_if_hold', '')
            opportunity = r.get('opportunity_if_hold', '')
            action      = r.get('suggested_action', '')
            revised_sl  = r.get('revised_sl')
            revised_tp  = r.get('revised_tp')
            confidence  = r.get('confidence', 'MEDIUM')

            advice = (
                f"🤖 OVERNIGHT REVIEW — {nl_time} {tz_lbl}\n"
                f"Decision: {'🌙 HOLD' if decision == 'HOLD' else '📅 CLOSE'} ({confidence} confidence)\n\n"
                f"Reasoning: {reasoning}\n\n"
                f"Risk if held: {risk}\n"
                f"Opportunity if held: {opportunity}\n\n"
                f"Suggested action: {action}"
            )
            if revised_sl:
                advice += f"\nRevised SL suggestion: {revised_sl:.6g}"
            if revised_tp:
                advice += f"\nRevised TP suggestion: {revised_tp:.6g}"

            vals = {
                'hold_overnight':      'hold' if decision == 'HOLD' else 'close_eod',
                'overnight_ai_advice': advice,
                'overnight_reviewed':  True,
            }
            # Apply revised SL/TP if provided
            if revised_sl and decision == 'HOLD':
                vals['stop_loss'] = float(revised_sl)
            if revised_tp and decision == 'HOLD':
                vals['take_profit'] = float(revised_tp)

            self.write(vals)

            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {
                    'title':   f"{'🌙 HOLD Overnight' if decision == 'HOLD' else '📅 CLOSE End of Day'} — {self.instrument}",
                    'message': f"{confidence} confidence | {reasoning[:120]}...",
                    'sticky':  True,
                    'type':    'info' if decision == 'HOLD' else 'warning',
                },
            }
        except Exception as e:
            raise UserError(f"Overnight review failed: {e}")

    def action_set_hold(self):
        """Manually mark position as Hold Overnight."""
        self.ensure_one()
        self.write({'hold_overnight': 'hold'})
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': '🌙 Holding Overnight', 'message': f'{self.instrument} will be kept open.', 'sticky': False, 'type': 'info'}}

    def action_set_close_eod(self):
        """Manually mark position to Close End of Day — triggers auto-close at 16:30."""
        self.ensure_one()
        self.write({'hold_overnight': 'close_eod'})
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': '📅 Will Close Today', 'message': f'{self.instrument} will be closed at end of session.', 'sticky': False, 'type': 'warning'}}


# ─────────────────────────────────────────────────────────────────────────────
# Trading System Log — front-end visible event log (added v23)
# ─────────────────────────────────────────────────────────────────────────────

class TradingSystemLog(models.Model):
    """
    Persistent log of all key system events visible from the front-end.
    Captures: price sanity failures, slippage warnings, position opens/closes,
    analysis sessions, API errors, automation runs.
    """
    _name        = 'trading.system_log'
    _description = 'Trading System Event Log'
    _order       = 'create_date desc'
    _rec_name    = 'message'

    level = fields.Selection([
        ('info',    'ℹ Info'),
        ('success', '✅ Success'),
        ('warning', '⚠ Warning'),
        ('error',   '❌ Error'),
    ], string='Level', default='info', required=True)

    category = fields.Selection([
        ('price',    '💰 Price / Data'),
        ('position', '📈 Position'),
        ('analysis', '🤖 Analysis'),
        ('learning', '🧠 Learning'),
        ('automation','⚙ Automation'),
        ('system',   '🔧 System'),
    ], string='Category', default='system', required=True)

    instrument  = fields.Char(string='Instrument')
    message     = fields.Char(string='Event', required=True)
    detail      = fields.Text(string='Detail')
    create_date = fields.Datetime(string='Time', readonly=True)

    color = fields.Integer(compute='_compute_color', store=False)

    @api.depends('level')
    def _compute_color(self):
        mapping = {'error': 1, 'warning': 3, 'success': 10, 'info': 0}
        for rec in self:
            rec.color = mapping.get(rec.level, 0)

    @api.model
    def log(self, level, category, message, detail='', instrument=''):
        """Create a log entry. Call this from anywhere in the module."""
        try:
            self.sudo().create({
                'level':      level,
                'category':   category,
                'instrument': instrument[:50] if instrument else '',
                'message':    message[:250],
                'detail':     detail[:2000] if detail else '',
            })
        except Exception:
            pass  # Never let logging crash the main flow

    @api.model
    def purge_old(self, days=30):
        """Remove log entries older than N days."""
        import datetime as dt
        cutoff = fields.Datetime.now() - dt.timedelta(days=days)
        self.sudo().search([('create_date', '<', cutoff)]).unlink()
