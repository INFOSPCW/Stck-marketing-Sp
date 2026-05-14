# -*- coding: utf-8 -*-
"""
Migration 19.0.25.33.0 — load bundled seed data on upgrade.
Calls the same _load_seed_data() used by post_init_hook, which
guards itself and skips if the trade log is already populated.
"""
from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.trading_ai_advisor.hooks import _load_seed_data
    _load_seed_data(env)
