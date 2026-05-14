# -*- coding: utf-8 -*-
"""
data_import_wizard.py — Data Import Wizard
===========================================
Imports data from a ZIP file previously exported by data_export_wizard.

Supported datasets:
  • trade_log.json        — Trade journal entries (skip existing by date+instrument)
  • rulebook.json         — AI rulebook rules (skip duplicates by rule_text)
  • cortex.json           — Cortex stats & lessons (merge or replace)
  • library_summaries.json — Knowledge library entries (merge or replace)

After import:
  • Cortex auto-rebuilds its stats from the merged trade log
  • User can trigger "Rebuild Stats" manually if needed

Safety:
  • Import is non-destructive by default — existing records are KEPT
  • Duplicates are skipped (matched by natural key, not ID)
  • A full import log is produced for review
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


class TradingDataImportWizard(models.TransientModel):
    _name        = 'trading.data.import.wizard'
    _description = 'Trading AI — Data Import Wizard'

    # ── Upload ────────────────────────────────────────────────────────────────
    zip_file     = fields.Binary(string='Import ZIP File', required=True,
                                 help='Select a ZIP file exported from Trading AI.')
    zip_filename = fields.Char(string='Filename')

    # ── Options ───────────────────────────────────────────────────────────────
    import_trade_log   = fields.Boolean('Import Trade Journal',     default=True)
    import_rulebook    = fields.Boolean('Import AI Rulebook',       default=True)
    import_cortex      = fields.Boolean('Import Cortex Stats & Lessons', default=True)
    import_library     = fields.Boolean('Import Knowledge Library Summaries', default=False,
                                        help='Import book summaries (not the PDFs themselves).')
    overwrite_cortex   = fields.Boolean(
        'Replace Cortex Stats', default=False,
        help='When ON: replaces cortex stats completely.\n'
             'When OFF: merges imported lessons with existing ones.')
    rebuild_cortex     = fields.Boolean(
        'Rebuild Cortex After Import', default=True,
        help='After import, rebuild cortex stats from the full trade log. Recommended.')

    # ── Result ────────────────────────────────────────────────────────────────
    import_log  = fields.Text(readonly=True)
    state       = fields.Selection([
        ('draft', 'Ready'),
        ('done',  'Done'),
    ], default='draft')

    # ─────────────────────────────────────────────────────────────────────────
    # Import
    # ─────────────────────────────────────────────────────────────────────────

    def action_import(self):
        self.ensure_one()
        if not self.zip_file:
            raise UserError("Please upload a ZIP file first.")

        zip_bytes = base64.b64decode(self.zip_file)
        log = [f"📦 Import started — {fields.Datetime.now()}"]

        try:
            buf = io.BytesIO(zip_bytes)
            if not zipfile.is_zipfile(buf):
                raise UserError("The uploaded file is not a valid ZIP archive.")
            buf.seek(0)

            with zipfile.ZipFile(buf, 'r') as zf:
                names = zf.namelist()
                log.append(f"  Files in ZIP: {', '.join(names)}")

                # Read manifest
                if 'manifest.json' in names:
                    manifest = json.loads(zf.read('manifest.json'))
                    log.append(
                        f"  Export date: {manifest.get('export_date', 'unknown')}, "
                        f"date range: {manifest.get('date_from')} → {manifest.get('date_to')}"
                    )

                # ── Trade Journal ─────────────────────────────────────────────
                if self.import_trade_log and 'trade_log.json' in names:
                    data   = json.loads(zf.read('trade_log.json'))
                    result = self._import_trade_log(data)
                    log.append(f"  ✅ Trade Journal: {result}")

                # ── AI Rulebook ───────────────────────────────────────────────
                if self.import_rulebook and 'rulebook.json' in names:
                    data   = json.loads(zf.read('rulebook.json'))
                    result = self._import_rulebook(data)
                    log.append(f"  ✅ AI Rulebook: {result}")

                # ── Cortex ────────────────────────────────────────────────────
                if self.import_cortex and 'cortex.json' in names:
                    data   = json.loads(zf.read('cortex.json'))
                    result = self._import_cortex(data)
                    log.append(f"  ✅ Cortex: {result}")

                # ── Knowledge Library ─────────────────────────────────────────
                if self.import_library and 'library_summaries.json' in names:
                    data   = json.loads(zf.read('library_summaries.json'))
                    result = self._import_library(data)
                    log.append(f"  ✅ Knowledge Library: {result}")

            # Rebuild cortex stats from full trade log
            if self.rebuild_cortex:
                try:
                    cortex = self.env['trading.cortex'].get_singleton()
                    cortex.action_rebuild_stats()
                    log.append("  🧠 Cortex stats rebuilt from trade log.")
                except Exception as e:
                    log.append(f"  ⚠ Cortex rebuild failed: {e}")

            log.append(f"\n✅ Import complete — {fields.Datetime.now()}")

        except zipfile.BadZipFile:
            log.append("❌ Invalid ZIP file.")
        except json.JSONDecodeError as e:
            log.append(f"❌ JSON parse error: {e}")
        except Exception as e:
            _logger.error("Import failed: %s", e, exc_info=True)
            log.append(f"❌ Import error: {e}")

        self.write({
            'import_log': '\n'.join(log),
            'state':      'done',
        })

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

    def _import_trade_log(self, records):
        created = skipped = 0
        # Batch-load existing keys for the dates in this import (avoids N+1 queries)
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
                        'trade_date':       r.get('trade_date'),
                        'instrument':       r.get('instrument', '')[:50],
                        'direction':        r.get('direction', 'BUY'),
                        'entry_price':      float(r.get('entry_price') or 0),
                        'exit_price':       float(r.get('exit_price') or 0),
                        'stop_loss':        float(r.get('stop_loss') or 0),
                        'take_profit':      float(r.get('take_profit') or 0),
                        'lot_size':         float(r.get('lot_size') or 0),
                        'outcome':          r.get('outcome', 'LOSS'),
                        'pnl':              float(r.get('pnl') or 0),
                        'mistake_category': r.get('mistake_category', 'other'),
                        'what_went_wrong':  r.get('what_went_wrong', ''),
                    })
                created += 1
            except Exception as e:
                _logger.warning("Trade log import skipped (error): %s", e)
                skipped += 1
        return f"{created} created, {skipped} skipped (duplicates/errors)"

    def _import_rulebook(self, records):
        created = skipped = 0
        # Batch-load existing rule prefixes (avoids N+1 queries)
        existing_prefixes = {
            (r.rule_text or '')[:60]
            for r in self.env['trading.ai_rulebook'].sudo().search([])
        }
        for r in records:
            rule_text = r.get('rule_text', '')
            if not rule_text:
                skipped += 1
                continue
            if rule_text[:60] in existing_prefixes:
                skipped += 1
                continue
            try:
                with self.env.cr.savepoint():
                    self.env['trading.ai_rulebook'].sudo().create({
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
        msgs   = []

        # Import / merge lessons
        lessons = data.get('lessons', [])
        created = skipped = 0
        for l in lessons:
            lt = l.get('lesson_text', '')
            if not lt:
                skipped += 1
                continue
            existing = cortex.lesson_ids.filtered(
                lambda x: (x.lesson_text or '')[:50] == lt[:50])
            if existing:
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

        # Optionally overwrite cortex JSON stats
        if self.overwrite_cortex:
            cortex.write({
                'instrument_stats':     data.get('instrument_stats', '{}'),
                'session_stats':        data.get('session_stats', '{}'),
                'confidence_stats':     data.get('confidence_stats', '{}'),
                'min_score_overrides':  data.get('min_score_overrides', '{}'),
                'blocked_instruments':  data.get('blocked_instruments', '[]'),
                'total_trades_analysed': int(data.get('total_trades_analysed') or 0),
                'state':                data.get('state', 'learning'),
            })
            msgs.append("stats overwritten from import")

        return "; ".join(msgs)

    def _import_library(self, records):
        created = updated = skipped = 0
        for lib_data in records:
            cat  = lib_data.get('category')
            name = lib_data.get('name', '')
            if not cat or not name:
                skipped += 1
                continue

            # Find or create library
            library = self.env['trading.knowledge.library'].sudo().search([
                ('category', '=', cat),
                ('name', '=', name),
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
                _logger.warning("Library create/update skipped: %s", e)
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
