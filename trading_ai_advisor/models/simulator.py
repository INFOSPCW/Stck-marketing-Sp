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

def _get_live_price(instrument, inst_type, td_key=''):
    """Fetch the current market price for an instrument."""
    if inst_type == 'crypto':
        symbol = instrument.replace('/', '')
        url    = (f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
        req    = urllib.request.Request(url, headers={"User-Agent": "TradingAI/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return float(data['price'])

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
    return float(data['price'])


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
            open_pos  = pos.filtered(lambda p: p.state == 'open')
            closed    = pos.filtered(lambda p: p.state == 'closed')
            wins      = closed.filtered(lambda p: p.pnl_usd > 0)
            losses    = closed.filtered(lambda p: p.pnl_usd < 0)
            total_pnl = sum(closed.mapped('pnl_usd'))
            win_pnl   = sum(wins.mapped('pnl_usd'))
            loss_pnl  = abs(sum(losses.mapped('pnl_usd'))) or 0.001

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
            try:
                live_price = _get_live_price(pos.instrument,
                                              pos.inst_type, td_key)
            except Exception as e:
                log.append(f"⚠ {pos.instrument}: price fetch failed — {e}")
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
                    'instrument':   pos.instrument,
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
                log.append(
                    f"{'✅' if outcome == 'WIN' else '❌'} {pos.instrument} "
                    f"{direction} CLOSED — {reason} @ {exit_price:.5g} "
                    f"| P&L: {pnl_str}"
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
        [('forex','Forex'),('crypto','Crypto'),('index','Index')],
        string='Type')
    direction  = fields.Selection(
        [('BUY','⬆ BUY'),('SELL','⬇ SELL')], string='Direction', required=True)
    state      = fields.Selection([
        ('open',   '🟢 Open'),
        ('closed', '⬜ Closed'),
        ('cancelled', '❌ Cancelled'),
    ], default='open', string='State')

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
        ('WIN','✅ Win'), ('LOSS','❌ Loss'), ('BREAKEVEN','➡ Breakeven')
    ], string='Outcome')
    pnl_usd = fields.Float(string='P&L ($)',  digits=(10, 2))
    pnl_pct = fields.Float(string='P&L (%)', digits=(8,  3))
    risk_reward_actual = fields.Float(
        string='Actual R/R', compute='_compute_actual_rr', store=True)

    color = fields.Integer(compute='_compute_color', store=False)

    @api.depends('instrument', 'direction', 'open_time')
    def _compute_name(self):
        for rec in self:
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
            if rec.state == 'open':
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
