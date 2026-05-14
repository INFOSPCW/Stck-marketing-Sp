# -*- coding: utf-8 -*-
"""
data_import_wizard.py — Data Import Wizard (large-file capable)
===============================================================
Two upload methods:
  Method 1 (large files): upload ZIP as an ir.attachment via
    Settings → Technical → Attachments, then select it here.
    This bypasses Odoo's HTTP request-body size limit entirely.
  Method 2 (small files <5 MB): upload directly via the Binary field.

Supported datasets (mirrors data_export_wizard.py):
  manifest.json           — metadata check only
  instruments.json        — instrument config (create missing, skip existing)
  trade_log.json          — trade journal entries
  daily_results.json      — daily analysis results
  daily_analyses.json     — analysis session headers
  rulebook.json           — AI rulebook rules
  cortex.json             — cortex stats + lessons
  simulator.json          — simulator account + positions
  system_logs.json        — system event log
  library_summaries.json  — knowledge library summaries
  config.json             — automation config (no API keys)

All imports are non-destructive: existing records are kept and
duplicates are detected by natural key and skipped.
"""

import io
import json
import base64
import zipfile
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class TradingDataImportWizard(models.TransientModel):
    _name        = 'trading.data.import.wizard'
    _description = 'Trading AI — Data Import Wizard'

    # ── Upload — Method 1: attachment (large files) ───────────────────────────
    zip_attachment_id = fields.Many2one(
        'ir.attachment',
        string='Select ZIP Attachment',
        help='Upload your ZIP via Settings → Technical → Attachments, '
             'then select it here. Recommended for files over 5 MB.')

    # ── Upload — Method 2: direct binary (small files) ───────────────────────
    zip_file     = fields.Binary(
        string='Or Upload Directly',
        help='Works for files under ~5 MB. For larger exports use the '
             'attachment method above.')
    zip_filename = fields.Char(string='Filename')

    # ── What to import ────────────────────────────────────────────────────────
    import_instruments  = fields.Boolean('Instrument Config',           default=False,
        help='Create missing instruments. Existing instruments are kept unchanged.')
    import_trade_log    = fields.Boolean('Trade Journal',               default=True)
    import_daily        = fields.Boolean('Daily Analysis Results',      default=True)
    import_analyses     = fields.Boolean('Analysis Sessions',           default=False)
    import_rulebook     = fields.Boolean('AI Rulebook',                 default=True)
    import_cortex       = fields.Boolean('Cortex Stats & Lessons',      default=True)
    import_simulator    = fields.Boolean('Simulator & Positions',       default=False)
    import_system_logs  = fields.Boolean('System Logs',                 default=False)
    import_library      = fields.Boolean('Knowledge Library Summaries', default=False)
    import_config       = fields.Boolean('Automation Config',           default=False,
        help='Updates non-API settings. API keys are never touched.')

    # ── Cortex options ────────────────────────────────────────────────────────
    overwrite_cortex = fields.Boolean(
        'Replace Cortex Stats', default=False,
        help='ON: replaces cortex stats completely.\n'
             'OFF: merges imported lessons with existing ones.')
    rebuild_cortex = fields.Boolean(
        'Rebuild Cortex After Import', default=True,
        help='Rebuild cortex stats from the merged trade log. Recommended.')

    # ── Result ────────────────────────────────────────────────────────────────
    import_log = fields.Text(readonly=True)
    state = fields.Selection([('draft', 'Ready'), ('done', 'Done')], default='draft')

    # ─────────────────────────────────────────────────────────────────────────
    # Main action
    # ─────────────────────────────────────────────────────────────────────────

    def action_import(self):
        self.ensure_one()

        # Resolve ZIP bytes from whichever upload method was used
        if self.zip_attachment_id:
            if not self.zip_attachment_id.datas:
                raise UserError("The selected attachment has no data.")
            zip_bytes = base64.b64decode(self.zip_attachment_id.datas)
            source = self.zip_attachment_id.name
        elif self.zip_file:
            zip_bytes = base64.b64decode(self.zip_file)
            source = self.zip_filename or 'uploaded file'
        else:
            raise UserError(
                "Please either select a ZIP attachment or upload a ZIP file directly.")

        log = [f"Import started — {fields.Datetime.now()}",
               f"  Source: {source} ({len(zip_bytes)/1024:.1f} KB)"]

        try:
            buf = io.BytesIO(zip_bytes)
            if not zipfile.is_zipfile(buf):
                raise UserError("The uploaded file is not a valid ZIP archive.")
            buf.seek(0)

            with zipfile.ZipFile(buf, 'r') as zf:
                names = zf.namelist()
                log.append(f"  Files in ZIP: {', '.join(names)}")

                if 'manifest.json' in names:
                    manifest = json.loads(zf.read('manifest.json'))
                    log.append(
                        f"  Export date: {manifest.get('export_date', 'unknown')}, "
                        f"date range: {manifest.get('date_from')} → {manifest.get('date_to')}")

                def _load(filename):
                    return json.loads(zf.read(filename)) if filename in names else None

                if self.import_instruments:
                    data = _load('instruments.json')
                    if data is not None:
                        log.append(f"  Instruments: {self._import_instruments(data)}")
                    else:
                        log.append("  Instruments: not in ZIP — skipped")

                if self.import_trade_log:
                    data = _load('trade_log.json')
                    if data is not None:
                        log.append(f"  Trade Journal: {self._import_trade_log(data)}")
                    else:
                        log.append("  Trade Journal: not in ZIP — skipped")

                if self.import_daily:
                    data = _load('daily_results.json')
                    if data is not None:
                        log.append(f"  Daily Results: {self._import_daily_results(data)}")
                    else:
                        log.append("  Daily Results: not in ZIP — skipped")

                if self.import_analyses:
                    data = _load('daily_analyses.json')
                    if data is not None:
                        log.append(f"  Analysis Sessions: {self._import_daily_analyses(data)}")
                    else:
                        log.append("  Analysis Sessions: not in ZIP — skipped")

                if self.import_rulebook:
                    data = _load('rulebook.json')
                    if data is not None:
                        log.append(f"  AI Rulebook: {self._import_rulebook(data)}")
                    else:
                        log.append("  AI Rulebook: not in ZIP — skipped")

                if self.import_cortex:
                    data = _load('cortex.json')
                    if data is not None:
                        log.append(f"  Cortex: {self._import_cortex(data)}")
                    else:
                        log.append("  Cortex: not in ZIP — skipped")

                if self.import_simulator:
                    data = _load('simulator.json')
                    if data is not None:
                        log.append(f"  Simulator: {self._import_simulator(data)}")
                    else:
                        log.append("  Simulator: not in ZIP — skipped")

                if self.import_system_logs:
                    data = _load('system_logs.json')
                    if data is not None:
                        log.append(f"  System Logs: {self._import_system_logs(data)}")
                    else:
                        log.append("  System Logs: not in ZIP — skipped")

                if self.import_library:
                    data = _load('library_summaries.json')
                    if data is not None:
                        log.append(f"  Knowledge Library: {self._import_library(data)}")
                    else:
                        log.append("  Knowledge Library: not in ZIP — skipped")

                if self.import_config:
                    data = _load('config.json')
                    if data is not None:
                        log.append(f"  Config: {self._import_config(data)}")
                    else:
                        log.append("  Config: not in ZIP — skipped")

            if self.rebuild_cortex and self.import_cortex:
                try:
                    cortex = self.env['trading.cortex'].get_singleton()
                    cortex.action_rebuild_stats()
                    log.append("  Cortex stats rebuilt from trade log.")
                except Exception as e:
                    log.append(f"  Cortex rebuild failed: {e}")

            log.append(f"\nImport complete — {fields.Datetime.now()}")

        except zipfile.BadZipFile:
            log.append("ERROR: Invalid ZIP file.")
        except json.JSONDecodeError as e:
            log.append(f"ERROR: JSON parse error: {e}")
        except Exception as e:
            _logger.error("Import failed: %s", e, exc_info=True)
            log.append(f"ERROR: {e}")

        self.write({'import_log': '\n'.join(log), 'state': 'done'})
        return {
            'type':      'ir.actions.act_window',
            'res_model': 'trading.data.import.wizard',
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Dataset importers
    # ─────────────────────────────────────────────────────────────────────────

    def _import_instruments(self, records):
        created = skipped = 0
        existing_keys = {
            r.instrument_key
            for r in self.env['trading.daily_instrument'].sudo().search([])
        }
        for r in records:
            key = r.get('instrument_key', '')
            if not key or key in existing_keys:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    self.env['trading.daily_instrument'].sudo().create({
                        'instrument_key': key,
                        'active':         bool(r.get('active', True)),
                        'sequence':       int(r.get('sequence') or 10),
                    })
                created += 1
            except Exception as e:
                _logger.warning("Instrument import skipped: %s", e)
                skipped += 1
        return f"{created} created, {skipped} skipped"

    def _import_trade_log(self, records):
        created = skipped = 0
        import_dates = list({r.get('trade_date') for r in records if r.get('trade_date')})
        existing_keys = {
            (str(t.trade_date), t.instrument, t.direction, float(t.entry_price or 0))
            for t in self.env['trading.trade_log'].sudo().search([
                ('trade_date', 'in', import_dates)
            ])
        }
        for r in records:
            key = (
                str(r.get('trade_date', '')),
                r.get('instrument', ''),
                r.get('direction', ''),
                float(r.get('entry_price') or 0),
            )
            if key in existing_keys:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    self.env['trading.trade_log'].sudo().create({
                        'trade_date':        r.get('trade_date'),
                        'instrument':        r.get('instrument', '')[:50],
                        'direction':         r.get('direction', 'BUY'),
                        'entry_price':       float(r.get('entry_price') or 0),
                        'exit_price':        float(r.get('exit_price') or 0),
                        'stop_loss':         float(r.get('stop_loss') or 0),
                        'take_profit':       float(r.get('take_profit') or 0),
                        'lot_size':          float(r.get('lot_size') or 0),
                        'outcome':           r.get('outcome', 'LOSS'),
                        'pnl':               float(r.get('pnl') or 0),
                        'mistake_category':  r.get('mistake_category', 'other'),
                        'what_went_wrong':   r.get('what_went_wrong', ''),
                        'lesson_learned':    r.get('lesson_learned', ''),
                    })
                created += 1
            except Exception as e:
                _logger.warning("Trade log import skipped: %s", e)
                skipped += 1
        return f"{created} created, {skipped} skipped"

    def _import_daily_results(self, records):
        created = skipped = 0
        existing_keys = {
            (str(r.analysis_date), r.instrument)
            for r in self.env['trading.daily_result'].sudo().search([])
        }
        for r in records:
            key = (str(r.get('analysis_date', '')), r.get('instrument', ''))
            if key in existing_keys:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    vals = {
                        'analysis_date':      r.get('analysis_date'),
                        'instrument':         r.get('instrument', ''),
                        'inst_type':          r.get('inst_type', ''),
                        'signal':             r.get('signal', 'WAIT'),
                        'score':              float(r.get('score') or 0),
                        'confidence':         r.get('confidence', ''),
                        'current_price':      float(r.get('current_price') or 0),
                        'entry_price':        float(r.get('entry_price') or 0),
                        'stop_loss':          float(r.get('stop_loss') or 0),
                        'take_profit':        float(r.get('take_profit') or 0),
                        'r_r_ratio':          float(r.get('r_r_ratio') or 0),
                        'hold_overnight_ai':  bool(r.get('hold_overnight_ai', False)),
                        'rsi':                float(r.get('rsi') or 0),
                        'macd':               float(r.get('macd') or 0),
                        'ema_20':             float(r.get('ema_20') or 0),
                        'ema_50':             float(r.get('ema_50') or 0),
                        'ema_200':            float(r.get('ema_200') or 0),
                        'best_open_time':     r.get('best_open_time', ''),
                        'best_open_time_nl':  r.get('best_open_time_nl', ''),
                        'best_close_time':    r.get('best_close_time', ''),
                        'best_close_time_nl': r.get('best_close_time_nl', ''),
                        'session_advice':     r.get('session_advice', ''),
                        'reasoning':          r.get('reasoning', ''),
                        'risk_warning':       r.get('risk_warning', ''),
                        'raw_response':       r.get('raw_response', ''),
                        'fib_signal':         r.get('fib_signal', ''),
                        'fib_strength':       float(r.get('fib_strength') or 0),
                        'fib_up_1618':        float(r.get('fib_up_1618') or 0),
                        'fib_dn_1618':        float(r.get('fib_dn_1618') or 0),
                        'fib_up_2618':        float(r.get('fib_up_2618') or 0),
                        'fib_dn_2618':        float(r.get('fib_dn_2618') or 0),
                        'fib_range_bound':    bool(r.get('fib_range_bound', False)),
                        'fib_setup':          r.get('fib_setup', ''),
                    }
                    self.env['trading.daily_result'].sudo().create(vals)
                created += 1
            except Exception as e:
                _logger.warning("Daily result import skipped: %s", e)
                skipped += 1
        return f"{created} created, {skipped} skipped"

    def _import_daily_analyses(self, records):
        created = skipped = 0
        existing_keys = {
            (str(r.analysis_date), r.name)
            for r in self.env['trading.daily_analysis'].sudo().search([])
        }
        for r in records:
            key = (str(r.get('analysis_date', '')), r.get('name', ''))
            if key in existing_keys:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    vals = {
                        'name':          r.get('name', ''),
                        'analysis_date': r.get('analysis_date'),
                        'state':         r.get('state', 'done'),
                        'run_log':       r.get('run_log', ''),
                    }
                    for opt in ('session_label', 'top_opportunity'):
                        if r.get(opt):
                            vals[opt] = r[opt]
                    self.env['trading.daily_analysis'].sudo().create(vals)
                created += 1
            except Exception as e:
                _logger.warning("Analysis session import skipped: %s", e)
                skipped += 1
        return f"{created} created, {skipped} skipped"

    def _import_rulebook(self, records):
        created = skipped = 0
        existing_prefixes = {
            (r.rule_text or '')[:60]
            for r in self.env['trading.ai_rulebook'].sudo().search([])
        }
        for r in records:
            rule_text = r.get('rule_text', '')
            if not rule_text or rule_text[:60] in existing_prefixes:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    self.env['trading.ai_rulebook'].sudo().create({
                        'name':            r.get('name', rule_text[:80]),
                        'rule_type':       r.get('rule_type', 'global'),
                        'instrument':      r.get('instrument', ''),
                        'category':        r.get('category', 'technical'),
                        'rule_text':       rule_text,
                        'confidence':      int(r.get('confidence') or 5),
                        'times_triggered': int(r.get('times_triggered') or 0),
                        'active':          bool(r.get('active', True)),
                    })
                created += 1
            except Exception as e:
                _logger.warning("Rulebook import skipped: %s", e)
                skipped += 1
        return f"{created} created, {skipped} skipped"

    def _import_cortex(self, data):
        if not data:
            return "empty file"
        cortex = self.env['trading.cortex'].sudo().get_singleton()
        msgs = []

        created = skipped = 0
        for l in data.get('lessons', []):
            lt = l.get('lesson_text', '')
            if not lt:
                skipped += 1
                continue
            if cortex.lesson_ids.filtered(lambda x: (x.lesson_text or '')[:50] == lt[:50]):
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    self.env['trading.cortex.lesson'].sudo().create({
                        'cortex_id':    cortex.id,
                        'lesson_type':  l.get('lesson_type', 'global'),
                        'instrument':   l.get('instrument', ''),
                        'session':      l.get('session', ''),
                        'lesson_text':  lt[:500],
                        'evidence':     l.get('evidence', 'Imported'),
                        'confidence':   int(l.get('confidence') or 5),
                        'active':       bool(l.get('active', True)),
                        'created_date': l.get('created_date', str(fields.Date.today())),
                        'last_updated': str(fields.Date.today()),
                    })
                created += 1
            except Exception as e:
                _logger.warning("Cortex lesson import skipped: %s", e)
                skipped += 1
        msgs.append(f"{created} lessons imported, {skipped} skipped")

        if self.overwrite_cortex:
            cortex.write({
                'instrument_stats':      data.get('instrument_stats', '{}'),
                'session_stats':         data.get('session_stats', '{}'),
                'confidence_stats':      data.get('confidence_stats', '{}'),
                'min_score_overrides':   data.get('min_score_overrides', '{}'),
                'blocked_instruments':   data.get('blocked_instruments', '[]'),
                'total_trades_analysed': int(data.get('total_trades_analysed') or 0),
                'state':                 data.get('state', 'learning'),
            })
            msgs.append("stats overwritten")
        return "; ".join(msgs)

    def _import_simulator(self, data):
        if not data or data.get('error'):
            return "no simulator data — skipped"

        sim = self.env['trading.simulator'].sudo().search(
            [('state', '=', 'active')], limit=1)
        msgs = []

        if not sim:
            try:
                sim = self.env['trading.simulator'].sudo().create({
                    'name':             data.get('simulator_name', 'Imported Simulator'),
                    'starting_balance': float(data.get('starting_balance') or 10000),
                    'current_balance':  float(data.get('current_balance') or 10000),
                    'risk_per_trade':   float(data.get('risk_per_trade') or 1.0),
                    'state':            'active',
                })
                msgs.append("simulator created")
            except Exception as e:
                return f"simulator create failed: {e}"

        created = skipped = 0
        existing_pos = {
            (p.instrument, p.direction, str(p.open_time)[:16])
            for p in sim.position_ids
        }
        for p in data.get('positions', []):
            key = (p.get('instrument', ''), p.get('direction', ''), str(p.get('open_time', ''))[:16])
            if key in existing_pos:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    self.env['trading.simulator.position'].sudo().create({
                        'simulator_id':       sim.id,
                        'instrument':         p.get('instrument', ''),
                        'inst_type':          p.get('inst_type', ''),
                        'direction':          p.get('direction', 'BUY'),
                        'entry_price':        float(p.get('entry_price') or 0),
                        'current_price':      float(p.get('current_price') or 0),
                        'exit_price':         float(p.get('exit_price') or 0),
                        'stop_loss':          float(p.get('stop_loss') or 0),
                        'take_profit':        float(p.get('take_profit') or 0),
                        'position_size_usd':  float(p.get('position_size_usd') or 0),
                        'open_time':          p.get('open_time'),
                        'exit_time':          p.get('exit_time') or False,
                        'state':              p.get('state', 'closed'),
                        'outcome':            p.get('outcome', ''),
                        'pnl_usd':            float(p.get('pnl_usd') or 0),
                        'pnl_pct':            float(p.get('pnl_pct') or 0),
                        'risk_reward_actual': float(p.get('risk_reward_actual') or 0),
                        'close_reason':       p.get('close_reason', ''),
                        'ai_score':           float(p.get('ai_score') or 0),
                        'ai_confidence':      p.get('ai_confidence', ''),
                        'ai_reasoning':       p.get('ai_reasoning', ''),
                    })
                created += 1
            except Exception as e:
                _logger.warning("Simulator position import skipped: %s", e)
                skipped += 1
        msgs.append(f"{created} positions imported, {skipped} skipped")
        return "; ".join(msgs)

    def _import_system_logs(self, records):
        created = skipped = 0
        for r in records:
            if not r.get('message'):
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    self.env['trading.system_log'].sudo().create({
                        'level':      r.get('level', 'info'),
                        'category':   r.get('category', 'system'),
                        'message':    r.get('message', ''),
                        'detail':     r.get('detail', ''),
                        'instrument': r.get('instrument', ''),
                    })
                created += 1
            except Exception as e:
                _logger.warning("System log import skipped: %s", e)
                skipped += 1
        return f"{created} created, {skipped} skipped"

    def _import_library(self, records):
        created = updated = skipped = 0
        for lib_data in records:
            cat  = lib_data.get('category')
            name = lib_data.get('name', '')
            if not cat or not name:
                skipped += 1
                continue
            library = self.env['trading.knowledge.library'].sudo().search([
                ('category', '=', cat), ('name', '=', name),
            ], limit=1)
            try:
                if not library:
                    with self.env.cr.savepoint():
                        library = self.env['trading.knowledge.library'].sudo().create({
                            'name':        name,
                            'category':    cat,
                            'description': lib_data.get('description', ''),
                        })
                    created += 1
                else:
                    updated += 1
            except Exception as e:
                _logger.warning("Library create skipped: %s", e)
                skipped += 1
                continue

            if lib_data.get('combined_summary'):
                try:
                    with self.env.cr.savepoint():
                        library.write({
                            'combined_summary': lib_data['combined_summary'],
                            'last_indexed':     fields.Datetime.now(),
                        })
                except Exception as e:
                    _logger.warning("Library summary update skipped: %s", e)

            for book_data in lib_data.get('books', []):
                bname = book_data.get('name', '')
                if not bname or not book_data.get('summary'):
                    continue
                exists = self.env['trading.knowledge.book'].sudo().search(
                    [('library_id', '=', library.id), ('name', '=', bname)], limit=1)
                if not exists:
                    try:
                        with self.env.cr.savepoint():
                            self.env['trading.knowledge.book'].sudo().create({
                                'library_id':      library.id,
                                'name':            bname,
                                'summary':         book_data.get('summary', ''),
                                'key_concepts':    book_data.get('key_concepts', ''),
                                'applicable_to':   book_data.get('applicable_to', ''),
                                'indexed':         bool(book_data.get('indexed', False)),
                                'pages_processed': int(book_data.get('pages_processed') or 0),
                            })
                    except Exception as e:
                        _logger.warning("Book import skipped: %s", e)

        return f"{created} libraries created, {updated} updated, {skipped} skipped"

    def _import_config(self, data):
        if not data:
            return "empty"
        try:
            config = self.env['trading.automation'].sudo().search([], limit=1)
            if not config:
                return "no automation config found — skipped"
            vals = {}
            for field in ('skip_weekends', 'min_score_to_trade', 'risk_per_trade'):
                if field in data:
                    vals[field] = data[field]
            if vals:
                config.write(vals)
            return "config updated (API keys preserved)"
        except Exception as e:
            return f"config import failed: {e}"
