# -*- coding: utf-8 -*-
{
    'name': 'Trading AI Advisor',
    'version': '19.0.25.45.0',
    'category': 'Finance/Trading',
    'summary': 'AI signals, paper trading simulator, trade journal, 27 instruments',
    'description': """
Trading AI Advisor v18
======================
NEW — Paper Trading Simulator:
  • Virtual account with configurable balance and risk %
  • "Simulate Trade" button on every AI signal → opens position at live price
  • "Check Positions" auto-closes trades that hit SL or TP
  • Unrealised P&L tracked in real time
  • Auto-creates Trade Journal entries on close
  • AI Performance Review — Claude analyses your win/loss patterns

Daily Analysis: 27 instruments (forex, gold, indices, crypto)
  • Session timing: best open/close time per instrument
  • Trade Loss Journal with AI mistake injection
  • Twelve Data for forex/indices (800 calls/day free)
  • Binance for crypto (no key needed)

Forex Brain + Crypto Brain: deep historical analysis
    """,
    'author': 'Custom',
    'depends': ['base', 'mail', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'data/daily_instruments.xml',
        'data/automation_cron.xml',
        'views/trading_config_views.xml',
        'views/daily_analysis_views.xml',
        'views/simulator_views.xml',
        'views/rulebook_views.xml',
        'views/automation_views.xml',
        'views/system_log_views.xml',
        'views/cortex_views.xml',
        'views/knowledge_library_views.xml',
        'wizard/data_export_wizard_views.xml',
        'wizard/data_import_wizard_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'trading_ai_advisor/static/src/css/trading.css',
            'trading_ai_advisor/static/src/js/trading_dashboard.js',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}
