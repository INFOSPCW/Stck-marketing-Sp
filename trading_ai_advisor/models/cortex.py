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

        for trade in all_trades:
            instrument = trade.instrument
            outcome    = trade.outcome
            confidence = getattr(trade, 'confidence', 'MEDIUM') or 'MEDIUM'
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
        })

        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   '🧠 Stats Rebuilt',
                'message': f'Rebuilt from {total} trade log entries.',
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
