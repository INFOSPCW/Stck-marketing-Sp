# -*- coding: utf-8 -*-
"""
knowledge_library.py — Categorised Knowledge Library
======================================================
Allows the AI to learn from trading books organised by category:
  • Forex          — currency market strategies, price action, fundamentals
  • Crypto         — crypto-specific strategies, on-chain, halving cycles
  • Indices        — stock market, macro, sector rotation
  • Commodities    — gold, oil, supply/demand dynamics
  • Psychology     — trading psychology, discipline, emotional control
  • Risk Mgmt      — position sizing, drawdown, risk/reward
  • Technical      — TA methodologies, indicators, chart patterns
  • Macro          — economics, central banks, rates, geopolitics

Workflow:
  1. User creates a Library for a category (e.g. "Forex Books")
  2. Adds books (PDF or ZIP of PDFs) — each book is a knowledge.book record
  3. Clicks "Index All Books" → Claude summarises each book
  4. Combined summary is stored on the library record
  5. During daily_analysis.action_run_analysis(), relevant libraries
     are loaded and their summaries are injected into the AI prompt
"""

import io
import json
import time
import base64
import zipfile
import logging
import urllib.request

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Category → instrument type map (for selecting relevant libraries)
# ─────────────────────────────────────────────────────────────────────────────

CATEGORY_FOR_TYPE = {
    'forex':   ['forex',  'technical_analysis', 'risk_management', 'psychology'],
    'crypto':  ['crypto', 'technical_analysis', 'risk_management', 'psychology'],
    'index':   ['indices', 'macroeconomics',    'risk_management', 'psychology'],
}

INSTRUMENT_CATEGORY_MAP = {
    'forex':  'forex',
    'crypto': 'crypto',
    'index':  'indices',
}


def _get_inst_type(instrument):
    """Derive instrument type from the symbol string."""
    if any(x in instrument for x in ('BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'USDT', 'ADA', 'DOGE')):
        return 'crypto'
    if instrument in ('DIA', 'SPY', 'QQQ', 'EWG'):
        return 'index'
    return 'forex'


def _claude_post_lib(api_key, payload, timeout=90, max_retries=4):
    """Resilient Claude API call with exponential backoff."""
    from urllib.error import HTTPError
    delay = 10
    body  = json.dumps(payload).encode()
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
                _logger.warning("Library Claude %s (attempt %d/%d) waiting %ds…",
                                e.code, attempt, max_retries, wait)
                time.sleep(wait)
                delay = min(delay * 2, 120)
            else:
                raise
        except Exception:
            raise


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class TradingKnowledgeLibrary(models.Model):
    """
    Category-level knowledge library.
    One library per category. Contains multiple books.
    The combined_summary is injected into AI prompts for relevant instruments.
    """
    _name        = 'trading.knowledge.library'
    _description = 'Trading Knowledge Library'
    _inherit     = ['mail.thread']
    _order       = 'category, name'

    name     = fields.Char(required=True, tracking=True)
    category = fields.Selection([
        ('forex',              'Forex Trading'),
        ('crypto',             'Cryptocurrency'),
        ('indices',            'Indices & Stocks'),
        ('commodities',        'Commodities & Gold'),
        ('psychology',         'Trading Psychology'),
        ('risk_management',    'Risk Management'),
        ('technical_analysis', 'Technical Analysis'),
        ('macroeconomics',     'Macroeconomics & Central Banks'),
    ], required=True, tracking=True)
    description = fields.Text()

    book_ids           = fields.One2many('trading.knowledge.book', 'library_id', string='Books')
    book_count         = fields.Integer(compute='_compute_book_stats', store=True)
    indexed_book_count = fields.Integer(compute='_compute_book_stats', store=True)

    combined_summary = fields.Text(
        string='Combined Knowledge',
        readonly=True,
        help='AI-merged summary of all indexed books in this library. '
             'This is what gets injected into trading AI prompts.')
    last_indexed = fields.Datetime(readonly=True)
    state = fields.Selection([
        ('empty',     'Empty'),
        ('has_books', 'Has Books (not indexed)'),
        ('indexed',   'Indexed & Ready'),
    ], compute='_compute_state', store=True)

    @api.depends('book_ids', 'book_ids.indexed')
    def _compute_book_stats(self):
        for rec in self:
            rec.book_count         = len(rec.book_ids)
            rec.indexed_book_count = len(rec.book_ids.filtered('indexed'))

    @api.depends('book_ids', 'book_ids.indexed')
    def _compute_state(self):
        for rec in self:
            if not rec.book_ids:
                rec.state = 'empty'
            elif rec.book_ids.filtered('indexed'):
                rec.state = 'indexed'
            else:
                rec.state = 'has_books'

    def action_index_all_books(self):
        """Process all unindexed books, build combined summary."""
        self.ensure_one()
        cfg     = self.env['trading.config'].get_config()
        api_key = cfg.get('anthropic_api_key', '')
        if not api_key:
            raise UserError("Anthropic API key required for book indexing.")

        unindexed = self.book_ids.filtered(lambda b: not b.indexed)
        if not unindexed and self.combined_summary:
            raise UserError(
                "All books already indexed. Add new books or use 'Re-Index All' "
                "to force reprocessing.")

        # Process unindexed books
        ok, failed = 0, 0
        for book in unindexed:
            try:
                book._do_index(api_key)
                ok += 1
            except Exception as e:
                _logger.warning("Failed to index '%s': %s", book.name, e)
                failed += 1
            time.sleep(5)   # Respect Claude rate limits between books

        # Re-build combined summary
        self._rebuild_combined_summary(api_key)

        msg = f"{ok} book(s) indexed successfully."
        if failed:
            msg += f" {failed} failed (check book attachments)."

        return self._notify('📚 Library Indexed', msg, 'success' if not failed else 'warning')

    def action_reindex_all(self):
        """Force re-index ALL books (including already indexed)."""
        self.ensure_one()
        self.book_ids.write({'indexed': False, 'summary': '', 'key_concepts': ''})
        return self.action_index_all_books()

    def _rebuild_combined_summary(self, api_key=None):
        """Merge all book summaries into one combined_summary."""
        self.ensure_one()
        indexed = self.book_ids.filtered(lambda b: b.indexed and b.summary)
        if not indexed:
            return

        parts = []
        for book in indexed:
            parts.append(f"--- {book.name} ---\n{book.summary[:3000]}")
        merged = '\n\n'.join(parts)

        # If multiple books, compress with Claude
        if len(indexed) > 1 and api_key:
            try:
                payload = {
                    "model":      "claude-haiku-4-5-20251001",
                    "max_tokens": 2500,
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"Merge these {len(indexed)} trading book summaries into ONE "
                            f"comprehensive knowledge base for '{self._get_category_label()}' trading.\n"
                            f"Organise by: key strategies, entry/exit rules, risk rules, "
                            f"what NOT to do, and psychological insights.\n"
                            f"Be specific, practical, and actionable. Remove duplicates.\n\n"
                            f"{merged[:15000]}"
                        )
                    }]
                }
                resp   = _claude_post_lib(api_key, payload)
                merged = resp['content'][0]['text'] if resp.get('content') else merged
            except Exception as e:
                _logger.warning("Could not merge summaries with Claude: %s", e)

        self.write({
            'combined_summary': merged[:20000],
            'last_indexed':     fields.Datetime.now(),
        })

    def get_knowledge_for_ai(self, max_chars=4000):
        """
        Returns the formatted knowledge block to inject into an AI prompt.
        Returns '' if library is empty or not indexed.
        """
        self.ensure_one()
        if not self.combined_summary:
            return ''
        label = self._get_category_label().upper()
        return (
            f"=== 📚 {label} KNOWLEDGE BASE ===\n"
            f"{self.combined_summary[:max_chars]}\n"
            f"=== END {label} KNOWLEDGE ==="
        )

    def _get_category_label(self):
        self.ensure_one()
        return dict(self._fields['category'].selection).get(self.category, self.category)

    def _notify(self, title, message, ntype='success'):
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'sticky': False, 'type': ntype},
        }

    @api.model
    def get_knowledge_for_instrument(self, instrument, max_chars_per_lib=2500):
        """
        Given an instrument symbol, return combined knowledge from all relevant libraries.
        Always includes: type-specific + technical_analysis + risk_management + psychology.
        Returns '' if no indexed libraries found.
        """
        inst_type  = _get_inst_type(instrument)
        categories = CATEGORY_FOR_TYPE.get(inst_type, ['technical_analysis', 'risk_management'])

        # Also add commodities for gold
        if instrument == 'XAU/USD':
            categories = list(set(categories + ['commodities', 'macroeconomics']))

        libraries = self.search([
            ('category', 'in', categories),
            ('state', '=', 'indexed'),
        ])

        if not libraries:
            return ''

        parts = []
        # Prioritise type-specific library (e.g. 'forex' over 'psychology')
        primary_cat = INSTRUMENT_CATEGORY_MAP.get(inst_type, 'technical_analysis')
        primary     = libraries.filtered(lambda l: l.category == primary_cat)
        secondary   = libraries.filtered(lambda l: l.category != primary_cat)

        for lib in list(primary) + list(secondary):
            knowledge = lib.get_knowledge_for_ai(max_chars=max_chars_per_lib)
            if knowledge:
                parts.append(knowledge)
            if len('\n'.join(parts)) > 8000:
                break   # Cap total injection to avoid token explosion

        return '\n\n'.join(parts)


class TradingKnowledgeBook(models.Model):
    """
    A single trading book within a library.
    Stores the attached PDF(s) and the AI-generated summary.
    """
    _name        = 'trading.knowledge.book'
    _description = 'Trading Knowledge Book'
    _order       = 'library_id, name'

    name       = fields.Char(required=True)
    library_id = fields.Many2one(
        'trading.knowledge.library', ondelete='cascade', required=True, index=True)
    category   = fields.Selection(related='library_id.category', store=True, readonly=True)

    attachment_ids = fields.Many2many(
        'ir.attachment',
        'knowledge_book_att_rel',
        'book_id', 'attachment_id',
        string='PDF / ZIP Files',
        help='Upload one or more PDF files, or a ZIP containing PDFs.')

    summary         = fields.Text(readonly=True, help='AI-generated summary.')
    key_concepts    = fields.Text(readonly=True,
                                  help='Bullet-point key concepts extracted by Claude.')
    applicable_to   = fields.Char(
        string='Applicable To',
        help='Comma-separated instruments this book is most relevant to. '
             'E.g. EUR/USD, GBP/USD. Leave empty for all instruments of this category.')

    indexed          = fields.Boolean(default=False, readonly=True)
    indexed_date     = fields.Datetime(readonly=True)
    pages_processed  = fields.Integer(readonly=True)

    def action_index(self):
        """Index this book (user-triggered)."""
        self.ensure_one()
        cfg     = self.env['trading.config'].get_config()
        api_key = cfg.get('anthropic_api_key', '')
        if not api_key:
            raise UserError("Anthropic API key required.")
        self._do_index(api_key)
        # Rebuild the parent library's combined summary
        self.library_id._rebuild_combined_summary(api_key)
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   '📚 Book Indexed',
                'message': f"'{self.name}' indexed ({self.pages_processed} PDF(s) processed).",
                'sticky': False, 'type': 'success',
            }
        }

    def action_reindex(self):
        """Force re-index even if already indexed."""
        self.write({'indexed': False, 'summary': '', 'key_concepts': ''})
        return self.action_index()

    def _do_index(self, api_key):
        """
        Core indexing logic:
        1. Extract text from all attached PDFs / ZIPs
        2. Summarise with Claude Haiku (category-aware prompt)
        3. Extract key concepts
        4. Save summary and mark indexed
        """
        if not self.attachment_ids:
            raise UserError(f"'{self.name}' has no attachments. Upload a PDF or ZIP first.")

        # Import PDF helpers from trading_brain
        try:
            from .trading_brain import _collect_pdfs_from_attachment, _pdf_text_from_bytes
        except ImportError:
            raise UserError("PDF helper import failed. Check trading_brain.py is present.")

        all_text = []
        total_pdfs = 0
        for att in self.attachment_ids:
            pdfs = _collect_pdfs_from_attachment(att)
            for title, pdf_bytes in pdfs:
                text = _pdf_text_from_bytes(pdf_bytes, max_chars=50000)
                if text.strip():
                    all_text.append(f"[{title}]\n{text}")
                    total_pdfs += 1

        if not all_text:
            raise UserError(f"No readable PDF text found in '{self.name}'.")

        combined_text = '\n\n'.join(all_text)[:70000]
        cat_label     = self.library_id._get_category_label() if self.library_id else 'trading'

        # Step 1: Summarise
        payload = {
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 2500,
            "messages": [{
                "role": "user",
                "content": (
                    f"Summarise this {cat_label} trading book for an AI trading system.\n"
                    f"Extract ONLY actionable content:\n"
                    f"1. Core trading strategies and setups\n"
                    f"2. Specific entry and exit rules\n"
                    f"3. Stop-loss and take-profit guidelines\n"
                    f"4. Risk management rules\n"
                    f"5. Common mistakes to avoid\n"
                    f"6. Market-specific insights for {cat_label}\n\n"
                    f"Be precise, specific, and practical. This will guide AI trading decisions.\n"
                    f"Max 600 words. Use bullet points.\n\n"
                    f"BOOK CONTENT:\n{combined_text[:60000]}"
                )
            }]
        }
        resp    = _claude_post_lib(api_key, payload, timeout=120)
        summary = resp['content'][0]['text'] if resp.get('content') else ''

        # Step 2: Key concepts (short list)
        time.sleep(4)
        kc_payload = {
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 400,
            "messages": [{
                "role": "user",
                "content": (
                    f"From this book summary, extract 5–8 key trading rules as bullet points.\n"
                    f"Each bullet: max 15 words. Be specific and actionable.\n\n{summary}"
                )
            }]
        }
        try:
            kc_resp      = _claude_post_lib(api_key, kc_payload)
            key_concepts = kc_resp['content'][0]['text'] if kc_resp.get('content') else ''
        except Exception:
            key_concepts = ''

        self.write({
            'summary':         summary[:10000],
            'key_concepts':    key_concepts,
            'indexed':         True,
            'indexed_date':    fields.Datetime.now(),
            'pages_processed': total_pdfs,
        })
