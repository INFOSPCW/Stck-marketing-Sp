# -*- coding: utf-8 -*-
"""
data_export_wizard.py — Full Data Export Wizard (Extended)
===========================================================
Exports ALL trading AI data to a self-contained ZIP file.
The ZIP can be used to fully restore a fresh Odoo database
via the companion Import Wizard — no data loss between databases.

Exported datasets:
  manifest.json           — Export metadata + record counts
  instruments.json        — All 44 instrument configurations
  trade_log.json          — Full trade journal
  daily_results.json      — All analysis results (with Fibonacci data)
  daily_analyses.json     — Analysis session headers
  rulebook.json           — AI rulebook (all rules + stats)
  cortex.json             — Cortex stats + all lessons
  simulator.json          — Simulator account + all positions + trade log
  system_logs.json        — TradingSystemLog entries
  library_summaries.json  — Knowledge library summaries
  config.json             — Automation config (NO API keys)
  README.txt              — Import instructions
"""

import io
import json
import base64
import zipfile
import logging
import datetime as dt

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TradingDataExportWizard(models.TransientModel):
    _name        = 'trading.data.export.wizard'
    _description = 'Trading AI — Full Data Export Wizard'

    # ── Date range ────────────────────────────────────────────────────────────
    date_from = fields.Date(
        string='From Date',
        default=lambda self: fields.Date.today() - dt.timedelta(days=90))
    date_to = fields.Date(
        string='To Date',
        default=fields.Date.today)
    export_all_time = fields.Boolean(
        string='Export ALL Data (Full Backup)',
        default=True,
        help='Export everything — recommended for database migration.')

    # ── What to export ────────────────────────────────────────────────────────
    export_instruments   = fields.Boolean('Instruments Config',        default=True)
    export_trade_log     = fields.Boolean('Trade Journal',             default=True)
    export_daily         = fields.Boolean('Daily Analysis Results',    default=True)
    export_analyses      = fields.Boolean('Analysis Sessions',         default=True)
    export_rulebook      = fields.Boolean('AI Rulebook',               default=True)
    export_cortex        = fields.Boolean('Cortex Stats & Lessons',    default=True)
    export_simulator     = fields.Boolean('Simulator & Positions',     default=True)
    export_system_logs   = fields.Boolean('System Logs',               default=True)
    export_library       = fields.Boolean('Knowledge Library',         default=True)
    export_config        = fields.Boolean('Automation Config',         default=True,
                                          help='Settings only — API keys are never exported.')

    # ── Result ────────────────────────────────────────────────────────────────
    result_attachment_id = fields.Many2one(
        'ir.attachment', string='Download', readonly=True)
    state = fields.Selection([
        ('draft', 'Ready'),
        ('done',  'Done'),
    ], default='draft')
    export_summary = fields.Text(string='Export Summary', readonly=True)

    # ─────────────────────────────────────────────────────────────────────────
    def action_export(self):
        """Build complete ZIP and attach it for download."""
        self.ensure_one()

        date_from = None if self.export_all_time else self.date_from
        date_to   = None if self.export_all_time else self.date_to

        buf = io.BytesIO()
        summary_lines = []
        manifest = {
            'export_date':   fields.Datetime.now().isoformat(),
            'date_from':     str(date_from) if date_from else 'ALL',
            'date_to':       str(date_to)   if date_to   else 'ALL',
            'module':        'trading_ai_advisor',
            'version':       '18.0.25.28.0',
            'full_backup':   self.export_all_time,
            'records':       {},
        }

        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:

            # ── README ────────────────────────────────────────────────────────
            readme = self._build_readme(manifest)
            zf.writestr('README.txt', readme)

            # ── Instruments ───────────────────────────────────────────────────
            if self.export_instruments:
                data = self._export_instruments()
                zf.writestr('instruments.json', json.dumps(data, indent=2, default=str))
                manifest['records']['instruments'] = len(data)
                summary_lines.append(f"✅ instruments.json — {len(data)} instruments")

            # ── Trade Journal ─────────────────────────────────────────────────
            if self.export_trade_log:
                data = self._export_trade_log(date_from, date_to)
                zf.writestr('trade_log.json', json.dumps(data, indent=2, default=str))
                manifest['records']['trade_log'] = len(data)
                summary_lines.append(f"✅ trade_log.json — {len(data)} trades")

            # ── Daily Results ─────────────────────────────────────────────────
            if self.export_daily:
                data = self._export_daily_results(date_from, date_to)
                zf.writestr('daily_results.json', json.dumps(data, indent=2, default=str))
                manifest['records']['daily_results'] = len(data)
                summary_lines.append(f"✅ daily_results.json — {len(data)} results")

            # ── Analysis Sessions ─────────────────────────────────────────────
            if self.export_analyses:
                data = self._export_analyses(date_from, date_to)
                zf.writestr('daily_analyses.json', json.dumps(data, indent=2, default=str))
                manifest['records']['daily_analyses'] = len(data)
                summary_lines.append(f"✅ daily_analyses.json — {len(data)} sessions")

            # ── AI Rulebook ───────────────────────────────────────────────────
            if self.export_rulebook:
                data = self._export_rulebook()
                zf.writestr('rulebook.json', json.dumps(data, indent=2, default=str))
                manifest['records']['rulebook'] = len(data)
                summary_lines.append(f"✅ rulebook.json — {len(data)} rules")

            # ── Cortex ────────────────────────────────────────────────────────
            if self.export_cortex:
                data = self._export_cortex()
                zf.writestr('cortex.json', json.dumps(data, indent=2, default=str))
                manifest['records']['cortex_lessons'] = data.get('lesson_count', 0)
                summary_lines.append(
                    f"✅ cortex.json — {data.get('lesson_count', 0)} lessons, "
                    f"{data.get('total_trades_analysed', 0)} trades analysed")

            # ── Simulator ─────────────────────────────────────────────────────
            if self.export_simulator:
                data = self._export_simulator(date_from, date_to)
                zf.writestr('simulator.json', json.dumps(data, indent=2, default=str))
                manifest['records']['positions']  = data.get('position_count', 0)
                manifest['records']['trade_logs'] = data.get('trade_log_count', 0)
                summary_lines.append(
                    f"✅ simulator.json — {data.get('position_count', 0)} positions, "
                    f"{data.get('trade_log_count', 0)} trade log entries")

            # ── System Logs ───────────────────────────────────────────────────
            if self.export_system_logs:
                data = self._export_system_logs(date_from, date_to)
                zf.writestr('system_logs.json', json.dumps(data, indent=2, default=str))
                manifest['records']['system_logs'] = len(data)
                summary_lines.append(f"✅ system_logs.json — {len(data)} log entries")

            # ── Knowledge Library ─────────────────────────────────────────────
            if self.export_library:
                data = self._export_library()
                zf.writestr('library_summaries.json', json.dumps(data, indent=2, default=str))
                manifest['records']['libraries'] = len(data)
                summary_lines.append(f"✅ library_summaries.json — {len(data)} libraries")

            # ── Config ────────────────────────────────────────────────────────
            if self.export_config:
                data = self._export_config()
                zf.writestr('config.json', json.dumps(data, indent=2, default=str))
                summary_lines.append("✅ config.json — automation settings (no API keys)")

            # ── Manifest last ─────────────────────────────────────────────────
            zf.writestr('manifest.json', json.dumps(manifest, indent=2, default=str))

        # Create attachment
        zip_bytes = buf.getvalue()
        today_str = str(fields.Datetime.now()).replace('-','').replace(' ','_').replace(':','')[:15]
        filename  = f"trading_ai_full_export_{today_str}.zip"

        attachment = self.env['ir.attachment'].create({
            'name':      filename,
            'type':      'binary',
            'datas':     base64.b64encode(zip_bytes),
            'mimetype':  'application/zip',
            'res_model': 'trading.data.export.wizard',
            'res_id':    self.id,
        })

        summary = '\n'.join(summary_lines) + f"\n\n📦 File: {filename} ({len(zip_bytes)/1024:.1f} KB)"
        self.write({
            'state':                'done',
            'result_attachment_id': attachment.id,
            'export_summary':       summary,
        })

        _logger.info("Trading AI full export: %s (%d bytes)", filename, len(zip_bytes))

        return {
            'type':      'ir.actions.act_window',
            'res_model': 'trading.data.export.wizard',
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
        }

    def action_download(self):
        self.ensure_one()
        if not self.result_attachment_id:
            raise UserError("No export file. Run export first.")
        return {
            'type':   'ir.actions.act_url',
            'url':    f"/web/content/{self.result_attachment_id.id}?download=true",
            'target': 'self',
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Cron daily export
    # ─────────────────────────────────────────────────────────────────────────
    @api.model
    def cron_daily_export(self):
        wizard = self.create({'export_all_time': True})
        wizard.action_export()
        cutoff = dt.datetime.utcnow() - dt.timedelta(days=7)
        old = self.env['ir.attachment'].search([
            ('name', 'like', 'trading_ai_full_export_'),
            ('res_model', '=', 'trading.data.export.wizard'),
            ('create_date', '<', cutoff),
        ])
        old.unlink()
        _logger.info("Daily full export complete. Purged %d old exports.", len(old))

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────
    def _date_domain(self, date_field, date_from, date_to):
        domain = []
        if date_from: domain.append((date_field, '>=', date_from))
        if date_to:   domain.append((date_field, '<=', date_to))
        return domain

    def _build_readme(self, manifest):
        return f"""TRADING AI ADVISOR — FULL DATA EXPORT
======================================
Export date:  {manifest['export_date']}
Date range:   {manifest['date_from']} → {manifest['date_to']}
Module:       {manifest['module']} v{manifest['version']}
Full backup:  {manifest['full_backup']}

CONTENTS
--------
README.txt              — This file
manifest.json           — Export metadata and record counts
instruments.json        — 44 instrument configurations
trade_log.json          — Complete trade journal
daily_results.json      — All analysis results with Fibonacci data
daily_analyses.json     — Analysis session headers
rulebook.json           — AI self-learned rulebook
cortex.json             — Cortex meta-brain stats and lessons
simulator.json          — Paper trading account, positions, trade log
system_logs.json        — System event log
library_summaries.json  — Knowledge library (book summaries)
config.json             — Automation settings (NO API keys)

HOW TO IMPORT INTO A NEW DATABASE
----------------------------------
1. Install the trading_ai_advisor module on the new database
2. Go to Daily → Export / Import → Import Data
3. Upload this ZIP file
4. Click Import — data is merged non-destructively
5. Set your API keys in Configuration (they are never exported)

NOTE: API keys (Anthropic, Twelve Data, Serper, Alpha Vantage,
Binance) are NOT included in this export for security.
You must re-enter them manually after importing.
"""

    # ─────────────────────────────────────────────────────────────────────────
    # Dataset serialisers
    # ─────────────────────────────────────────────────────────────────────────

    def _export_instruments(self):
        records = self.env['trading.daily_instrument'].sudo().search(
            [], order='sequence asc')
        return [
            {
                'id':             r.id,
                'instrument_key': r.instrument_key,
                'active':         r.active,
                'sequence':       r.sequence,
            }
            for r in records
        ]

    def _export_trade_log(self, date_from, date_to):
        domain  = self._date_domain('trade_date', date_from, date_to)
        records = self.env['trading.trade_log'].sudo().search(
            domain, order='trade_date asc')
        return [
            {
                'id':                r.id,
                'trade_date':        str(r.trade_date),
                'instrument':        r.instrument,
                'direction':         r.direction,
                'entry_price':       r.entry_price,
                'exit_price':        r.exit_price,
                'stop_loss':         r.stop_loss,
                'take_profit':       r.take_profit,
                'lot_size':          r.lot_size,
                'outcome':           r.outcome,
                'pnl':               r.pnl,
                'risk_reward_actual':r.risk_reward_actual,
                'mistake_category':  r.mistake_category,
                'what_went_wrong':   r.what_went_wrong,
                'lesson_learned':    r.lesson_learned,
                'analysis_id':       r.analysis_id.id if r.analysis_id else None,
            }
            for r in records
        ]

    def _export_daily_results(self, date_from, date_to):
        domain  = self._date_domain('analysis_date', date_from, date_to)
        records = self.env['trading.daily_result'].sudo().search(
            domain, order='analysis_date asc')
        result = []
        for r in records:
            result.append({
                'id':                r.id,
                'analysis_date':     str(r.analysis_date),
                'instrument':        r.instrument,
                'inst_type':         r.inst_type,
                'signal':            r.signal,
                'score':             r.score,
                'confidence':        r.confidence,
                'current_price':     r.current_price,
                'entry_price':       r.entry_price,
                'stop_loss':         r.stop_loss,
                'take_profit':       r.take_profit,
                'r_r_ratio':         r.r_r_ratio,
                'hold_overnight_ai': r.hold_overnight_ai,
                'rsi':               r.rsi,
                'macd':              r.macd,
                'ema_20':            r.ema_20,
                'ema_50':            r.ema_50,
                'ema_200':           r.ema_200,
                'best_open_time':    r.best_open_time,
                'best_open_time_nl': r.best_open_time_nl,
                'best_close_time':   r.best_close_time,
                'best_close_time_nl':r.best_close_time_nl,
                'session_advice':    r.session_advice,
                'reasoning':         r.reasoning,
                'risk_warning':      r.risk_warning,
                'raw_response':      r.raw_response,
                # Fibonacci fields
                'fib_signal':        r.fib_signal,
                'fib_strength':      r.fib_strength,
                'fib_up_1618':       r.fib_up_1618,
                'fib_dn_1618':       r.fib_dn_1618,
                'fib_up_2618':       r.fib_up_2618,
                'fib_dn_2618':       r.fib_dn_2618,
                'fib_range_bound':   r.fib_range_bound,
                'fib_setup':         r.fib_setup,
            })
        return result

    def _export_analyses(self, date_from, date_to):
        domain  = self._date_domain('analysis_date', date_from, date_to)
        records = self.env['trading.daily_analysis'].sudo().search(
            domain, order='analysis_date asc')
        return [
            {
                'id':              r.id,
                'name':            r.name,
                'analysis_date':   str(r.analysis_date),
                'session_label':   getattr(r, 'session_label', ''),
                'state':           r.state,
                'run_log':         r.run_log,
                'top_opportunity': getattr(r, 'top_opportunity', ''),
            }
            for r in records
        ]

    def _export_rulebook(self):
        records = self.env['trading.ai_rulebook'].sudo().search(
            [], order='confidence desc, times_triggered desc')
        return [
            {
                'id':              r.id,
                'name':            r.name,
                'rule_type':       r.rule_type,
                'instrument':      r.instrument,
                'category':        r.category,
                'rule_text':       r.rule_text,
                'confidence':      r.confidence,
                'times_triggered': r.times_triggered,
                'win_rate':        getattr(r, 'win_rate', 0),
                'active':          r.active,
                'created_date':    str(getattr(r, 'create_date', '')),
            }
            for r in records
        ]

    def _export_cortex(self):
        try:
            cortex = self.env['trading.cortex'].sudo().get_singleton()
        except Exception:
            return {'error': 'Cortex not found', 'lesson_count': 0}
        lessons = [
            {
                'lesson_type':   l.lesson_type,
                'instrument':    l.instrument,
                'session':       l.session,
                'lesson_text':   l.lesson_text,
                'evidence':      l.evidence,
                'confidence':    l.confidence,
                'active':        l.active,
                'created_date':  str(getattr(l, 'create_date', '')),
            }
            for l in cortex.lesson_ids
        ]
        return {
            'state':                  cortex.state,
            'total_trades_analysed':  cortex.total_trades_analysed,
            'total_vetoes':           cortex.total_vetoes,
            'total_warnings':         cortex.total_warnings,
            'instrument_stats':       cortex.instrument_stats,
            'session_stats':          cortex.session_stats,
            'confidence_stats':       cortex.confidence_stats,
            'min_score_overrides':    cortex.min_score_overrides,
            'blocked_instruments':    cortex.blocked_instruments,
            'last_review_date':       str(cortex.last_review_date or ''),
            'last_review_summary':    cortex.last_review_summary,
            'lesson_count':           len(lessons),
            'lessons':                lessons,
        }

    def _export_simulator(self, date_from, date_to):
        sim = self.env['trading.simulator'].sudo().search(
            [('state', '=', 'active')], limit=1)
        if not sim:
            return {'error': 'No active simulator', 'position_count': 0, 'trade_log_count': 0}

        # All positions — no date filter (positions have open_time)
        positions = []
        for p in sim.position_ids:
            if date_from and str(p.open_time)[:10] < str(date_from): continue
            if date_to   and str(p.open_time)[:10] > str(date_to):   continue
            positions.append({
                'instrument':         p.instrument,
                'inst_type':          p.inst_type,
                'direction':          p.direction,
                'entry_price':        p.entry_price,
                'current_price':      p.current_price,
                'exit_price':         p.exit_price,
                'stop_loss':          p.stop_loss,
                'take_profit':        p.take_profit,
                'position_size_usd':  p.position_size_usd,
                'open_time':          str(p.open_time),
                'exit_time':          str(p.exit_time or ''),
                'state':              p.state,
                'outcome':            p.outcome,
                'pnl_usd':            p.pnl_usd,
                'pnl_pct':            p.pnl_pct,
                'risk_reward_actual': p.risk_reward_actual,
                'close_reason':       p.close_reason,
                'ai_score':           p.ai_score,
                'ai_confidence':      p.ai_confidence,
                'ai_reasoning':       p.ai_reasoning,
            })

        # Trade log entries linked to this simulator
        trade_logs = []
        for tl in self.env['trading.trade_log'].sudo().search([]):
            trade_logs.append({
                'instrument':        tl.instrument,
                'direction':         tl.direction,
                'trade_date':        str(tl.trade_date),
                'entry_price':       tl.entry_price,
                'exit_price':        tl.exit_price,
                'stop_loss':         tl.stop_loss,
                'take_profit':       tl.take_profit,
                'outcome':           tl.outcome,
                'pnl':               tl.pnl,
                'risk_reward_actual':tl.risk_reward_actual,
                'mistake_category':  tl.mistake_category,
                'what_went_wrong':   tl.what_went_wrong,
                'lesson_learned':    tl.lesson_learned,
            })

        return {
            'simulator_name':   sim.name,
            'starting_balance': sim.starting_balance,
            'current_balance':  sim.current_balance,
            'risk_per_trade':   sim.risk_per_trade,
            'total_trades':     sim.total_trades,
            'winning_trades':   sim.winning_trades,
            'losing_trades':    sim.losing_trades,
            'win_rate':         sim.win_rate,
            'total_pnl':        sim.total_pnl,
            'avg_win':          sim.avg_win,
            'avg_loss':         sim.avg_loss,
            'profit_factor':    sim.profit_factor,
            'equity_pct':       sim.equity_pct,
            'state':            sim.state,
            'position_count':   len(positions),
            'trade_log_count':  len(trade_logs),
            'positions':        positions,
            'trade_log':        trade_logs,
        }

    def _export_system_logs(self, date_from, date_to):
        try:
            domain  = self._date_domain('create_date', date_from, date_to)
            records = self.env['trading.system_log'].sudo().search(
                domain, order='create_date desc', limit=5000)
            return [
                {
                    'create_date': str(r.create_date),
                    'level':       r.level,
                    'category':    r.category,
                    'message':     r.message,
                    'detail':      r.detail,
                    'instrument':  r.instrument,
                }
                for r in records
            ]
        except Exception:
            return []   # System log model may not exist in older installs

    def _export_library(self):
        try:
            libraries = self.env['trading.knowledge.library'].sudo().search([])
            result = []
            for lib in libraries:
                books = [
                    {
                        'name':            b.name,
                        'summary':         b.summary,
                        'key_concepts':    b.key_concepts,
                        'applicable_to':   b.applicable_to,
                        'indexed':         b.indexed,
                        'pages_processed': b.pages_processed,
                    }
                    for b in lib.book_ids
                ]
                result.append({
                    'name':             lib.name,
                    'category':         lib.category,
                    'description':      lib.description,
                    'combined_summary': lib.combined_summary,
                    'book_count':       lib.book_count,
                    'books':            books,
                })
            return result
        except Exception:
            return []

    def _export_config(self):
        """Export automation settings — API keys are deliberately excluded."""
        try:
            config = self.env['trading.automation'].sudo().search([], limit=1)
            if not config:
                return {}
            return {
                'name':             config.name,
                'skip_weekends':    getattr(config, 'skip_weekends', True),
                'min_score_to_trade': getattr(config, 'min_score_to_trade', 6),
                'risk_per_trade':   getattr(config, 'risk_per_trade', 1.0),
                'note':             'API keys are not exported for security. Re-enter them in Configuration.',
            }
        except Exception:
            return {'note': 'Config export unavailable'}
