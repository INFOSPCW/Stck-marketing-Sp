# -*- coding: utf-8 -*-
"""
cortex.py — Prefrontal Cortex Learning System
===============================================
Inspired by the human prefrontal cortex:
  • Executive decision-making (APPROVE / WARN / VETO trades)
  • Learning from past experiences (win/loss patterns per instrument, session, confidence)
  • Inhibiting impulsive / bad trades (losing streaks → temporary block)
  • Pattern recognition (identifies what works and what doesn't)
  • Adaptive thresholds (auto-raises min_score for underperforming instruments)
  • Weekly deep review via Claude (distils new lessons from recent trades)

The cortex is a singleton that:
  1. Is consulted BEFORE every trade opens  → evaluate_trade()
  2. Is updated AFTER every trade closes    → learn_from_outcome()
  3. Injects lessons INTO every AI prompt  → get_cortex_context()
  4. Runs weekly deep reviews              → action_run_weekly_review()
"""

import json
import time
import logging
import urllib.request
import datetime as dt

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _claude_post_cortex(api_key, payload, timeout=60, max_retries=4):
    """Resilient Claude API call with exponential backoff."""
    from urllib.error import HTTPError
    delay = 10
    body = json.dumps(payload).encode()
    for attempt in range(1, max_retries + 1):
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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code in (529, 503, 429) and attempt < max_retries:
                retry_after = e.headers.get('Retry-After')
                wait = int(retry_after) if retry_after and retry_after.isdigit() else delay
                _logger.warning("Cortex Claude %s (attempt %d/%d) waiting %ds…",
                                e.code, attempt, max_retries, wait)
                time.sleep(wait)
                delay = min(delay * 2, 120)
            else:
                raise
        except Exception:
            raise


class TradingCortex(models.Model):
    """
    The Prefrontal Cortex — master learning brain of the Trading AI.

    Architecture:
    ─────────────
    • Stats are stored as JSON text fields for flexibility and schema-less evolution.
    • instrument_stats: {EUR/USD: {wins, losses, streak, last_outcomes[]}}
    • session_stats:    {london: {wins, losses}}
    • confidence_stats: {HIGH: {wins, losses}}
    • min_score_overrides: {EUR/USD: 8}  — raised when instrument performs poorly
    • blocked_instruments: [EUR/USD]     — paused during losing streaks ≥ 4
    """
    _name        = 'trading.cortex'
    _description = 'Trading AI — Prefrontal Cortex Learning System'
    _inherit     = ['mail.thread']

    name = fields.Char(default='Prefrontal Cortex', readonly=True)

    # ── Learning State ────────────────────────────────────────────────────────
    state = fields.Selection([
        ('learning',  'Learning (< 20 trades)'),
        ('adapting',  'Adapting (20–50 trades)'),
        ('mature',    'Mature (50+ trades)'),
    ], default='learning', readonly=True,
       help='Learning stage determines how aggressively the cortex vetoes trades.')

    total_trades_analysed = fields.Integer(
        default=0, readonly=True,
        help='Total trade outcomes the cortex has learned from.')
    total_vetoes = fields.Integer(
        default=0, readonly=True,
        help='Total number of trades the cortex vetoed.')
    total_warnings = fields.Integer(
        default=0, readonly=True,
        help='Total number of trades the cortex warned about (but allowed).')

    # ── JSON Stats Stores ─────────────────────────────────────────────────────
    instrument_stats = fields.Text(
        default='{}', readonly=True,
        help='JSON: {EUR/USD: {wins, losses, streak, last_outcomes[]}}')
    session_stats = fields.Text(
        default='{}', readonly=True,
        help='JSON: {london: {wins, losses}}')
    confidence_stats = fields.Text(
        default='{}', readonly=True,
        help='JSON: {HIGH: {wins, losses}}')
    min_score_overrides = fields.Text(
        default='{}', readonly=True,
        help='JSON: {EUR/USD: 8} — raised min score for underperforming instruments.')
    blocked_instruments = fields.Text(
        default='[]', readonly=True,
        help='JSON list of instruments temporarily paused due to losing streaks.')
    mistake_stats = fields.Text(
        default='{}', readonly=True,
        help='JSON: {sl_too_tight: {count, instruments[]}, chased_trade: {...}} '
             '— tracks recurring execution mistakes to auto-tighten preflight rules.')
    asset_class_mistakes = fields.Text(
        default='{}', readonly=True,
        help='JSON: per-asset-class mistake rates, e.g. '
             '{forex: {sl_too_tight: 5, total_losses: 12}, crypto: {...}} '
             '— drives GLOBAL per-class rule adjustments (different markets '
             'behave differently, so each class is tuned separately).')

    # ── Lessons ───────────────────────────────────────────────────────────────
    lesson_ids   = fields.One2many('trading.cortex.lesson', 'cortex_id', string='Learned Lessons')
    lesson_count = fields.Integer(compute='_compute_lesson_count', store=True)

    # ── Review ────────────────────────────────────────────────────────────────
    last_review_date    = fields.Datetime(readonly=True)
    last_review_summary = fields.Text(readonly=True)
    cortex_summary      = fields.Text(readonly=True,
                                      help='Auto-generated human-readable performance summary.')

    @api.depends('lesson_ids')
    def _compute_lesson_count(self):
        for rec in self:
            rec.lesson_count = len(rec.lesson_ids)

    # ─────────────────────────────────────────────────────────────────────────
    # Singleton
    # ─────────────────────────────────────────────────────────────────────────

    @api.model
    def get_singleton(self):
        rec = self.sudo().search([], limit=1)
        if not rec:
            rec = self.sudo().create({'name': 'Prefrontal Cortex'})
        return rec

    @api.model
    def action_open_singleton(self):
        """Always opens the existing cortex record, never a blank create form."""
        rec = self.get_singleton()
        return {
            'type':      'ir.actions.act_window',
            'name':      'Prefrontal Cortex',
            'res_model': 'trading.cortex',
            'view_mode': 'form',
            'res_id':    rec.id,
            'target':    'current',
            'views':     [(False, 'form')],
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _load_stats(self):
        """Load all JSON stats, returning safe defaults on corruption."""
        def _safe(text, default):
            try:
                return json.loads(text or default)
            except Exception:
                return json.loads(default)

        return (
            _safe(self.instrument_stats, '{}'),
            _safe(self.session_stats,    '{}'),
            _safe(self.confidence_stats, '{}'),
            _safe(self.min_score_overrides, '{}'),
            _safe(self.blocked_instruments, '[]'),
        )

    def _save_stats(self, inst, sess, conf, overrides, blocked, extra=None):
        vals = {
            'instrument_stats':   json.dumps(inst),
            'session_stats':      json.dumps(sess),
            'confidence_stats':   json.dumps(conf),
            'min_score_overrides': json.dumps(overrides),
            'blocked_instruments': json.dumps(blocked),
        }
        if extra:
            vals.update(extra)
        self.write(vals)

    # ─────────────────────────────────────────────────────────────────────────
    # Core API
    # ─────────────────────────────────────────────────────────────────────────

    def evaluate_trade(self, instrument, direction, score, confidence, session='unknown'):
        """
        Called BEFORE opening any position.

        Returns:
          ('APPROVE', reason) — proceed with trade
          ('WARN',    reason) — allow but log warning in position
          ('VETO',    reason) — block trade entirely

        Decision logic:
          1. Losing streak block (≥ 4 consecutive losses) → VETO
          2. Adaptive min-score override → VETO if score too low
          3. Instrument win rate < 35% (≥ 5 trades) → VETO
          4. Instrument win rate < 45% → WARN
          5. Session win rate < 35% → WARN
          6. Confidence level win rate < 35% → WARN
        """
        self.ensure_one()
        inst_stats, sess_stats, conf_stats, overrides, blocked = self._load_stats()

        # 1. Losing streak block
        if instrument in blocked:
            self.sudo().write({'total_vetoes': (self.total_vetoes or 0) + 1})
            return ('VETO',
                    f"🧠 CORTEX VETO: {instrument} is on a losing streak cooldown. "
                    f"Wait for 2 consecutive wins before trading it again.")

        # 2. Adaptive min-score override
        override_score = overrides.get(instrument)
        if override_score and score < override_score:
            self.sudo().write({'total_vetoes': (self.total_vetoes or 0) + 1})
            return ('VETO',
                    f"🧠 CORTEX VETO: {instrument} currently requires score ≥ {override_score} "
                    f"(recent poor performance). This signal scored {score}.")

        reasons = []
        verdict = 'APPROVE'

        # 3 & 4. Instrument win rate
        idata  = inst_stats.get(instrument, {})
        total_i = idata.get('wins', 0) + idata.get('losses', 0)
        if total_i >= 5:
            wr = idata['wins'] / total_i
            if wr < 0.35:
                verdict = 'VETO'
                reasons.append(f"{instrument} win rate {wr:.0%} after {total_i} trades — below 35% minimum")
            elif wr < 0.45:
                if verdict != 'VETO':
                    verdict = 'WARN'
                reasons.append(f"{instrument} win rate {wr:.0%} — below 45% average")

        # 5. Session win rate
        sdata  = sess_stats.get(session, {})
        total_s = sdata.get('wins', 0) + sdata.get('losses', 0)
        if total_s >= 5 and verdict != 'VETO':
            wr = sdata.get('wins', 0) / total_s
            if wr < 0.35:
                verdict = 'WARN'
                reasons.append(f"{session} session win rate {wr:.0%}")

        # 6. Confidence level win rate
        cdata  = conf_stats.get(confidence, {})
        total_c = cdata.get('wins', 0) + cdata.get('losses', 0)
        if total_c >= 5 and verdict != 'VETO':
            wr = cdata.get('wins', 0) / total_c
            if wr < 0.35:
                verdict = 'WARN'
                reasons.append(f"{confidence} confidence win rate {wr:.0%}")

        if verdict == 'VETO':
            self.sudo().write({'total_vetoes': (self.total_vetoes or 0) + 1})
            return ('VETO', "🧠 CORTEX VETO: " + " | ".join(reasons))
        elif verdict == 'WARN':
            self.sudo().write({'total_warnings': (self.total_warnings or 0) + 1})
            return ('WARN', "🧠 CORTEX WARNING: " + " | ".join(reasons))

        return ('APPROVE', '✅ Cortex approves — setup fits learned patterns')

    def learn_from_outcome(self, instrument, outcome, session='unknown', confidence='MEDIUM'):
        """
        Called AFTER a trade closes. Updates all learning statistics.

        Args:
            instrument: e.g. 'EUR/USD'
            outcome:    'WIN' | 'LOSS' | 'BREAKEVEN'
            session:    'Pre-Market' | 'London Open' | 'NY Open' | 'US Market Open'
            confidence: 'HIGH' | 'MEDIUM' | 'LOW'
        """
        self.ensure_one()
        inst_stats, sess_stats, conf_stats, overrides, blocked = self._load_stats()

        is_win = (outcome == 'WIN')

        # ── Instrument stats ──────────────────────────────────────────────────
        if instrument not in inst_stats:
            inst_stats[instrument] = {
                'wins': 0, 'losses': 0, 'streak': 0, 'last_outcomes': []}

        idata = inst_stats[instrument]
        if is_win:
            idata['wins'] = idata.get('wins', 0) + 1
        elif outcome == 'LOSS':
            idata['losses'] = idata.get('losses', 0) + 1

        # Streak: positive = win streak, negative = loss streak
        streak = idata.get('streak', 0)
        if is_win:
            idata['streak'] = max(streak, 0) + 1
        elif outcome == 'LOSS':
            idata['streak'] = min(streak, 0) - 1
        else:
            idata['streak'] = 0  # reset on breakeven

        # Keep last 10 outcomes for quick visual reference
        hist = idata.get('last_outcomes', [])
        hist.append('W' if is_win else ('L' if outcome == 'LOSS' else 'B'))
        idata['last_outcomes'] = hist[-10:]

        # ── Auto-block on losing streak ≥ 4 ──────────────────────────────────
        if idata['streak'] <= -4 and instrument not in blocked:
            blocked.append(instrument)
            _logger.info("Cortex: Blocking %s (losing streak %d)", instrument, idata['streak'])

        # ── Auto-unblock after 2 consecutive wins ─────────────────────────────
        elif idata['streak'] >= 2 and instrument in blocked:
            blocked.remove(instrument)
            _logger.info("Cortex: Unblocking %s (win streak resumed)", instrument)

        # ── Adaptive min-score: raise if win rate < 40% over ≥ 8 trades ──────
        total_i = idata.get('wins', 0) + idata.get('losses', 0)
        if total_i >= 8:
            wr = idata.get('wins', 0) / total_i
            if wr < 0.40:
                overrides[instrument] = 8   # Require higher confidence
            elif wr > 0.55 and instrument in overrides:
                del overrides[instrument]   # Restore to global default

        # ── Session stats ─────────────────────────────────────────────────────
        if session not in sess_stats:
            sess_stats[session] = {'wins': 0, 'losses': 0}
        if is_win:
            sess_stats[session]['wins'] = sess_stats[session].get('wins', 0) + 1
        elif outcome == 'LOSS':
            sess_stats[session]['losses'] = sess_stats[session].get('losses', 0) + 1

        # ── Confidence stats ──────────────────────────────────────────────────
        if confidence not in conf_stats:
            conf_stats[confidence] = {'wins': 0, 'losses': 0}
        if is_win:
            conf_stats[confidence]['wins'] = conf_stats[confidence].get('wins', 0) + 1
        elif outcome == 'LOSS':
            conf_stats[confidence]['losses'] = conf_stats[confidence].get('losses', 0) + 1

        # ── Update totals & state ─────────────────────────────────────────────
        new_total = (self.total_trades_analysed or 0) + 1
        new_state = ('learning' if new_total < 20
                     else 'adapting' if new_total < 50
                     else 'mature')

        summary = self._build_summary(inst_stats, new_total)
        self._save_stats(inst_stats, sess_stats, conf_stats, overrides, blocked, extra={
            'total_trades_analysed': new_total,
            'state':                 new_state,
            'cortex_summary':        summary,
        })

    def action_backfill_from_history(self):
        """
        One-time: reset learning stats and re-process the ENTIRE trade log.
        Use this after deploying the learning fixes so the Cortex starts
        fully informed by all historical trades instead of learning from scratch.

        Maps the trade_log mistake_category values to the cortex categories
        and feeds every closed trade through learn_from_outcome + learn_from_mistake.
        """
        self.ensure_one()

        # Reset all learning state to start clean
        self.sudo().write({
            'instrument_stats':    '{}',
            'session_stats':       '{}',
            'confidence_stats':    '{}',
            'min_score_overrides': '{}',
            'blocked_instruments': '[]',
            'mistake_stats':       '{}',
            'asset_class_mistakes': '{}',
            'total_trades_analysed': 0,
            'total_vetoes':        0,
            'total_warnings':      0,
        })

        trades = self.env['trading.trade_log'].search([], order='trade_date asc, id asc')
        processed = 0
        for t in trades:
            if t.outcome not in ('WIN', 'LOSS', 'BREAKEVEN'):
                continue  # skip INVALID
            # trade_log has no confidence field
            conf = 'MEDIUM'
            self.learn_from_outcome(
                instrument=t.instrument,
                outcome=t.outcome,
                session='historical',
                confidence=conf,
            )
            if t.outcome == 'LOSS' and t.mistake_category:
                self.learn_from_mistake(
                    instrument=t.instrument,
                    mistake_category=t.mistake_category,
                )
            processed += 1

        _logger.info("Cortex backfill complete: %d trades processed", processed)
        return self._notify(
            '🧠 Cortex Backfill Complete',
            f'Processed {processed} historical trades. The Cortex now has full '
            f'per-instrument win/loss records and mistake patterns.',
            'success'
        ) if hasattr(self, '_notify') else {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': 'Cortex Backfill Complete',
                       'message': f'Processed {processed} historical trades.',
                       'type': 'success', 'sticky': False}
        }

    def learn_from_mistake(self, instrument, mistake_category):
        """
        Called AFTER a loss is categorised. Tracks recurring execution mistakes
        so the system can auto-tighten its own preflight rules.

        This is the closed loop: the system's logged mistakes feed back into
        its execution gates. E.g. if 'sl_too_tight' recurs on USD/CHF, the
        cortex flags it and the preflight gate widens that instrument's SL floor.
        """
        self.ensure_one()
        if not mistake_category or mistake_category == 'other':
            return
        try:
            mistakes = json.loads(self.mistake_stats or '{}')
        except Exception:
            mistakes = {}

        if mistake_category not in mistakes:
            mistakes[mistake_category] = {'count': 0, 'instruments': {}}
        mistakes[mistake_category]['count'] += 1
        inst_map = mistakes[mistake_category]['instruments']
        inst_map[instrument] = inst_map.get(instrument, 0) + 1

        self.sudo().write({'mistake_stats': json.dumps(mistakes)})

        # Also track per-asset-class for global pattern detection
        try:
            class_mistakes = json.loads(self.asset_class_mistakes or '{}')
        except Exception:
            class_mistakes = {}
        ac = self.classify_asset(instrument)
        class_mistakes.setdefault(ac, {'total_losses': 0})
        class_mistakes[ac]['total_losses'] = class_mistakes[ac].get('total_losses', 0) + 1
        class_mistakes[ac][mistake_category] = class_mistakes[ac].get(mistake_category, 0) + 1
        self.sudo().write({'asset_class_mistakes': json.dumps(class_mistakes)})

        _logger.info("Cortex: learned mistake '%s' on %s (%s class, total %d)",
                     mistake_category, instrument, ac, mistakes[mistake_category]['count'])

    @api.model
    def classify_asset(self, instrument):
        """
        Single source of truth for asset-class classification.
        Different markets behave differently, so every per-class rule
        (SL floors, chase tolerance) keys off this.
        """
        inst = instrument or ''
        if any(x in inst for x in ('USDT', 'BTC', 'ETH', 'SOL', 'XRP', 'BNB')):
            return 'crypto'
        if inst in ('DIA', 'SPY', 'QQQ', 'EWG'):
            return 'index'
        if inst in ('AAPL', 'TSLA', 'NVDA', 'MSFT', 'AMZN', 'META', 'GOOGL'):
            return 'stock'
        if '=F' in inst:
            return 'commodity'
        return 'forex'

    def get_preflight_adjustments(self, instrument):
        """
        Returns dict of adjusted preflight thresholds for this instrument.
        Combines TWO layers:
          1. Per-instrument: this specific symbol's repeated mistakes
          2. Per-asset-class GLOBAL: if a mistake is systemic across the whole
             class (e.g. tight stops on forex generally), tighten the class.
        The stricter of the two layers wins for each parameter.

        Returns e.g. {'sl_floor_mult': 1.3, 'max_chase_mult': 0.7}
        """
        self.ensure_one()
        try:
            mistakes = json.loads(self.mistake_stats or '{}')
        except Exception:
            mistakes = {}
        try:
            class_mistakes = json.loads(self.asset_class_mistakes or '{}')
        except Exception:
            class_mistakes = {}

        adj = {}
        asset_class = self.classify_asset(instrument)

        # ── Layer 1: per-instrument repeats ─────────────────────────
        sl_data = mistakes.get('sl_too_tight', {}).get('instruments', {})
        inst_sl_mult = 1.0
        if sl_data.get(instrument, 0) >= 2:
            inst_sl_mult = min(1.0 + 0.15 * sl_data[instrument], 1.5)

        chase_ct = (mistakes.get('chased_trade', {}).get('instruments', {}).get(instrument, 0)
                    + mistakes.get('bad_entry', {}).get('instruments', {}).get(instrument, 0))
        inst_chase_mult = 1.0
        if chase_ct >= 2:
            inst_chase_mult = max(1.0 - 0.15 * chase_ct, 0.5)

        # ── Layer 2: per-asset-class GLOBAL pattern ─────────────────
        # If a mistake type makes up a large share of THIS CLASS's losses,
        # the whole class is mis-tuned — tighten every instrument in it.
        cls = class_mistakes.get(asset_class, {})
        cls_losses = cls.get('total_losses', 0)
        class_sl_mult = 1.0
        class_chase_mult = 1.0
        # Only act with enough class-level data to be meaningful (≥5 losses)
        if cls_losses >= 5:
            sl_rate = cls.get('sl_too_tight', 0) / cls_losses
            # If ≥30% of this class's losses are tight-SL, widen the class floor
            if sl_rate >= 0.30:
                # Scale: 30%→+10%, 50%→+25%, capped +40%
                class_sl_mult = min(1.0 + (sl_rate - 0.20) * 0.8, 1.4)

            chase_rate = (cls.get('chased_trade', 0) + cls.get('bad_entry', 0)) / cls_losses
            if chase_rate >= 0.30:
                class_chase_mult = max(1.0 - (chase_rate - 0.20) * 0.8, 0.6)

        # ── Combine: stricter layer wins ────────────────────────────
        final_sl_mult    = max(inst_sl_mult, class_sl_mult)       # bigger = wider floor
        final_chase_mult = min(inst_chase_mult, class_chase_mult) # smaller = tighter

        if final_sl_mult > 1.0:
            adj['sl_floor_mult'] = round(final_sl_mult, 2)
            adj['sl_source'] = ('instrument' if inst_sl_mult >= class_sl_mult
                                else f'{asset_class}-class')
        if final_chase_mult < 1.0:
            adj['max_chase_mult'] = round(final_chase_mult, 2)
            adj['chase_source'] = ('instrument' if inst_chase_mult <= class_chase_mult
                                   else f'{asset_class}-class')

        return adj

    def get_cortex_context(self, instrument):
        """
        Returns a formatted string injected into every AI trading prompt.
        Includes performance stats, lessons, and adaptive warnings.
        """
        self.ensure_one()
        inst_stats, sess_stats, conf_stats, overrides, blocked = self._load_stats()

        lines = ["=== 🧠 PREFRONTAL CORTEX INTELLIGENCE ==="]
        lines.append(
            f"Learning stage: {dict(self._fields['state'].selection).get(self.state, self.state)} "
            f"({self.total_trades_analysed} trades analysed)"
        )

        # Instrument-specific performance
        idata = inst_stats.get(instrument, {})
        total_i = idata.get('wins', 0) + idata.get('losses', 0)
        if total_i > 0:
            wr = idata.get('wins', 0) / total_i
            streak = idata.get('streak', 0)
            streak_str = (f"↑ win streak of {streak}"   if streak > 0
                          else f"↓ loss streak of {abs(streak)}" if streak < 0
                          else "no streak")
            lines.append(
                f"\n{instrument} history: {wr:.0%} win rate "
                f"({idata.get('wins',0)}W / {idata.get('losses',0)}L) — {streak_str}"
            )
            lines.append(f"  Recent outcomes: {''.join(idata.get('last_outcomes', []))}")

        if instrument in overrides:
            lines.append(
                f"\n⚠ CORTEX OVERRIDE: Require score ≥ {overrides[instrument]} for {instrument} "
                f"(recent poor performance — be extra selective)"
            )
        if instrument in blocked:
            lines.append(f"\n🛑 CORTEX ALERT: {instrument} is on cooldown — do NOT trade it!")

        # Recurring mistake patterns for this instrument
        try:
            mistakes = json.loads(self.mistake_stats or '{}')
            inst_mistakes = []
            for cat, data in mistakes.items():
                ct = data.get('instruments', {}).get(instrument, 0)
                if ct >= 2:
                    inst_mistakes.append(f"{cat} ({ct}×)")
            if inst_mistakes:
                lines.append(
                    f"\n⚠ RECURRING MISTAKES on {instrument}: {', '.join(inst_mistakes)} "
                    f"— preflight rules auto-tightened. Be extra disciplined."
                )
        except Exception:
            pass

        # Relevant lessons (global + instrument-specific)
        lessons = self.lesson_ids.filtered(
            lambda l: l.active and (
                l.lesson_type == 'global' or
                (l.lesson_type == 'instrument' and l.instrument == instrument) or
                l.lesson_type in ('psychology', 'risk')
            )
        ).sorted('confidence', reverse=True)[:6]

        if lessons:
            lines.append(f"\n📚 Cortex Lessons ({len(lessons)}):")
            for les in lessons:
                scope = f"[{les.instrument}] " if les.instrument else "[GLOBAL] "
                lines.append(f"  • {scope}{les.lesson_text}")

        lines.append("=== END CORTEX INTELLIGENCE ===")
        return '\n'.join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Weekly deep review
    # ─────────────────────────────────────────────────────────────────────────

    def action_run_weekly_review(self):
        """
        Deep weekly review via Claude.
        Analyses last 14 days of trades, identifies patterns, creates new lessons.
        """
        self.ensure_one()
        cfg     = self.env['trading.config'].get_config()
        api_key = cfg.get('anthropic_api_key', '')
        if not api_key:
            raise UserError("Anthropic API key required for weekly review.")

        cutoff = fields.Date.today() - dt.timedelta(days=14)
        trades = self.env['trading.trade_log'].sudo().search([
            ('trade_date', '>=', cutoff),
        ], order='trade_date desc', limit=100)

        if len(trades) < 3:
            raise UserError(
                f"Only {len(trades)} trade(s) in last 14 days. "
                f"Need at least 3 to run a meaningful review.")

        trade_lines = []
        for t in trades:
            trade_lines.append(
                f"{t.trade_date} | {t.instrument} | {t.direction} | "
                f"{t.outcome} | PnL {t.pnl:.2f}% | "
                f"Mistake: {t.mistake_category or 'none'} | "
                f"Notes: {(t.what_went_wrong or 'N/A')[:80]}"
            )

        inst_stats, _, _, _, _ = self._load_stats()
        stats_str = json.dumps(
            {k: {kk: vv for kk, vv in v.items() if kk != 'last_outcomes'}
             for k, v in inst_stats.items()},
            indent=2
        )[:3000]

        existing_lessons = '\n'.join(
            f"• {l.lesson_text}" for l in self.lesson_ids.filtered('active')[:10]
        )

        payload = {
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 2500,
            "messages": [{
                "role": "user",
                "content": (
                    f"You are a trading performance coach and behavioural finance expert.\n"
                    f"Analyse these recent trades and produce actionable lessons.\n\n"
                    f"RECENT TRADES ({len(trades)}, last 14 days):\n"
                    + '\n'.join(trade_lines[:60])
                    + f"\n\nCURRENT PERFORMANCE STATS (per instrument):\n{stats_str}"
                    + (f"\n\nEXISTING LESSONS (do not duplicate):\n{existing_lessons}"
                       if existing_lessons else "")
                    + "\n\nProduce 4–8 NEW specific, actionable lessons NOT already in the list above.\n"
                    "Format EACH lesson on its own line as:\n"
                    "SCOPE | CATEGORY | lesson text (max 20 words)\n\n"
                    "SCOPE options: GLOBAL, or instrument key like EUR/USD or BTC/USDT\n"
                    "CATEGORY options: psychology, technical, risk_management, timing, instrument_specific\n\n"
                    "After the lessons, add a short SUMMARY section (3-5 sentences) explaining "
                    "the key patterns you found."
                )
            }]
        }

        resp   = _claude_post_cortex(api_key, payload)
        review = resp['content'][0]['text'] if resp.get('content') else ''

        # Parse lessons
        lessons_created = 0
        lesson_section  = True
        summary_lines   = []

        for line in review.split('\n'):
            line = line.strip()
            if not line:
                continue

            if line.upper().startswith('SUMMARY'):
                lesson_section = False
                continue

            if not lesson_section:
                summary_lines.append(line)
                continue

            if '|' not in line:
                continue

            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 3:
                continue

            scope, category_raw, lesson_text = parts[0], parts[1].lower(), '|'.join(parts[2:]).strip()
            if not lesson_text or len(lesson_text) < 8:
                continue

            cat_map = {
                'psychology':         'psychology',
                'technical':          'pattern',
                'risk_management':    'risk',
                'risk management':    'risk',
                'timing':             'session',
                'instrument_specific':'instrument',
                'instrument specific':'instrument',
            }
            lesson_type = cat_map.get(category_raw, 'global')
            instrument_for_lesson = ''

            scope_upper = scope.upper()
            if scope_upper not in ('GLOBAL', 'ALL', ''):
                lesson_type = 'instrument'
                instrument_for_lesson = scope

            # Skip duplicates (compare first 40 chars)
            existing = self.lesson_ids.filtered(
                lambda l: (l.lesson_text or '')[:40] == lesson_text[:40])
            if existing:
                continue

            self.env['trading.cortex.lesson'].create({
                'cortex_id':    self.id,
                'lesson_type':  lesson_type,
                'instrument':   instrument_for_lesson,
                'lesson_text':  lesson_text[:500],
                'evidence':     f"Weekly review of {len(trades)} trades (last 14 days)",
                'confidence':   6,
                'created_date': fields.Date.today(),
                'last_updated': fields.Date.today(),
            })
            lessons_created += 1

        summary = '\n'.join(summary_lines)
        self.write({
            'last_review_date':    fields.Datetime.now(),
            'last_review_summary': (review[:5000]),
        })

        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   '🧠 Weekly Cortex Review Complete',
                'message': (f"{lessons_created} new lesson(s) learned from "
                            f"{len(trades)} trades in the last 14 days."),
                'sticky': False, 'type': 'success',
            },
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Rebuild stats from trade log (after import or data changes)
    # ─────────────────────────────────────────────────────────────────────────

    def action_rebuild_stats(self):
        """Rebuild ALL stats from scratch from the trade log. Use after data import."""
        self.ensure_one()

        all_trades = self.env['trading.trade_log'].sudo().search(
            [], order='trade_date asc, id asc')

        inst_stats  = {}
        sess_stats  = {}
        conf_stats  = {}
        mistake_stats = {}
        class_mistakes = {}

        for trade in all_trades:
            instrument = trade.instrument
            outcome    = trade.outcome
            # trade_log has no confidence field — default to MEDIUM.
            # Real confidence is learned live via learn_from_outcome going forward.
            confidence = 'MEDIUM'
            is_win     = (outcome == 'WIN')

            if instrument not in inst_stats:
                inst_stats[instrument] = {
                    'wins': 0, 'losses': 0, 'streak': 0, 'last_outcomes': []}

            idata = inst_stats[instrument]
            if is_win:
                idata['wins'] = idata.get('wins', 0) + 1
            elif outcome == 'LOSS':
                idata['losses'] = idata.get('losses', 0) + 1

            streak = idata.get('streak', 0)
            if is_win:
                idata['streak'] = max(streak, 0) + 1
            elif outcome == 'LOSS':
                idata['streak'] = min(streak, 0) - 1

            hist = idata.get('last_outcomes', [])
            hist.append('W' if is_win else ('L' if outcome == 'LOSS' else 'B'))
            idata['last_outcomes'] = hist[-10:]

            # Session (use 'unknown' if not tracked on old records)
            sess_stats.setdefault('unknown', {'wins': 0, 'losses': 0})
            if is_win:
                sess_stats['unknown']['wins'] = sess_stats['unknown'].get('wins', 0) + 1
            elif outcome == 'LOSS':
                sess_stats['unknown']['losses'] = sess_stats['unknown'].get('losses', 0) + 1

            conf_stats.setdefault(confidence, {'wins': 0, 'losses': 0})
            if is_win:
                conf_stats[confidence]['wins'] = conf_stats[confidence].get('wins', 0) + 1
            elif outcome == 'LOSS':
                conf_stats[confidence]['losses'] = conf_stats[confidence].get('losses', 0) + 1

            # Mistake patterns (losses only)
            if outcome == 'LOSS' and trade.mistake_category and trade.mistake_category != 'other':
                cat = trade.mistake_category
                mistake_stats.setdefault(cat, {'count': 0, 'instruments': {}})
                mistake_stats[cat]['count'] += 1
                im = mistake_stats[cat]['instruments']
                im[instrument] = im.get(instrument, 0) + 1
                # Per-asset-class for global pattern detection
                ac = self.classify_asset(instrument)
                class_mistakes.setdefault(ac, {'total_losses': 0})
                class_mistakes[ac]['total_losses'] += 1
                class_mistakes[ac][cat] = class_mistakes[ac].get(cat, 0) + 1

        # Rebuild overrides and blocked list
        overrides = {}
        blocked   = []
        for instrument, idata in inst_stats.items():
            total = idata.get('wins', 0) + idata.get('losses', 0)
            if total >= 8:
                wr = idata.get('wins', 0) / total
                if wr < 0.40:
                    overrides[instrument] = 8
            if idata.get('streak', 0) <= -4:
                blocked.append(instrument)

        total    = len(all_trades)
        state    = ('learning' if total < 20 else 'adapting' if total < 50 else 'mature')
        summary  = self._build_summary(inst_stats, total)

        self._save_stats(inst_stats, sess_stats, conf_stats, overrides, blocked, extra={
            'total_trades_analysed': total,
            'state':                 state,
            'cortex_summary':        summary,
            'mistake_stats':         json.dumps(mistake_stats),
            'asset_class_mistakes':  json.dumps(class_mistakes),
        })

        mistake_summary = ', '.join(
            f"{c}({d['count']})" for c, d in
            sorted(mistake_stats.items(), key=lambda x: -x[1]['count'])
        ) or 'none'

        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   '🧠 Stats Rebuilt',
                'message': f'Rebuilt from {total} trades. Mistake patterns: {mistake_summary}',
                'sticky': False, 'type': 'success',
            },
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _build_summary(self, inst_stats, total):
        lines = [f"Cortex Summary — {total} total trades analysed:"]
        ranked = sorted(
            inst_stats.items(),
            key=lambda x: x[1].get('wins', 0) + x[1].get('losses', 0),
            reverse=True
        )
        for inst, data in ranked[:12]:
            t = data.get('wins', 0) + data.get('losses', 0)
            if t < 1:
                continue
            wr  = data.get('wins', 0) / t
            str_  = data.get('streak', 0)
            tag   = f"↑{str_}" if str_ > 0 else (f"↓{abs(str_)}" if str_ < 0 else "—")
            lines.append(
                f"  {inst:12s}: {wr:5.0%} win  "
                f"({data.get('wins',0)}W/{data.get('losses',0)}L)  {tag}"
            )
        return '\n'.join(lines)

    def action_view_lessons(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Cortex Lessons',
            'res_model': 'trading.cortex.lesson',
            'view_mode': 'list,form',
            'domain': [('cortex_id', '=', self.id)],
            'context': {'default_cortex_id': self.id},
        }


# ─────────────────────────────────────────────────────────────────────────────
# Lesson records
# ─────────────────────────────────────────────────────────────────────────────

class TradingCortexLesson(models.Model):
    """A single lesson the cortex has learned from trading history."""
    _name        = 'trading.cortex.lesson'
    _description = 'Cortex Learned Lesson'
    _order       = 'confidence desc, id desc'

    cortex_id = fields.Many2one(
        'trading.cortex', ondelete='cascade', required=True, index=True)

    lesson_type = fields.Selection([
        ('global',     'Global Rule'),
        ('instrument', 'Instrument-Specific'),
        ('session',    'Session-Specific'),
        ('pattern',    'Technical Pattern'),
        ('psychology', 'Psychology'),
        ('risk',       'Risk Management'),
    ], required=True, default='global')

    instrument = fields.Char(
        help='Leave empty for global lessons. E.g. EUR/USD, BTC/USDT')
    session    = fields.Char(
        help='london / ny / pre-market / etc. Leave empty for global.')

    lesson_text = fields.Text(required=True)
    evidence    = fields.Text(
        help='Which trades or patterns led to this lesson.')

    confidence = fields.Integer(
        default=5,
        help='1–10: data-backed confidence in this lesson. '
             '≥7 = strong evidence, 5–6 = moderate, ≤4 = weak.')

    trades_supporting    = fields.Integer(default=0)
    win_rate_with_rule   = fields.Float(
        string='Win Rate (with rule %)',
        help='Win rate on trades where this rule was followed.')

    active       = fields.Boolean(default=True)
    created_date = fields.Date(default=fields.Date.today)
    last_updated = fields.Date(default=fields.Date.today)

    color = fields.Integer(compute='_compute_color')

    def _compute_color(self):
        for rec in self:
            if rec.confidence >= 8:
                rec.color = 10   # Green
            elif rec.confidence >= 5:
                rec.color = 3    # Yellow/orange
            else:
                rec.color = 1    # Red
