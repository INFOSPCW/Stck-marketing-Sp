# -*- coding: utf-8 -*-
"""
hooks.py — post_init_hook to clean stale instrument records on upgrade.
Removes any trading.daily_instrument records whose instrument_key is no
longer in the current DAILY_INSTRUMENTS list (e.g. old paid-tier symbols).
"""
import logging
_logger = logging.getLogger(__name__)

VALID_KEYS = [
    'EUR/USD','GBP/USD','USD/JPY','AUD/USD','USD/CAD','USD/CHF','NZD/USD','USD/SGD',
    'GBP/JPY','EUR/JPY','AUD/JPY','EUR/GBP','USD/NOK','GBP/CHF','USD/ZAR','USD/MXN',
    'XAU/USD','EUR/CAD',
    'DIA','SPY','QQQ','EWG',
    'BTC/USDT','ETH/USDT','SOL/USDT','XRP/USDT','BNB/USDT',
    # US Stocks
    'AAPL','TSLA','NVDA','MSFT','AMZN','META','GOOGL',
    # Commodities
    'CL=F','BZ=F','NG=F','SI=F','GC=F','HG=F','PL=F','ZW=F','ZC=F','KC=F',
]


def post_init_hook(env):
    """Remove stale instrument records and migrate data after module upgrade."""
    # 1 — Remove stale trading.daily_instrument catalogue records
    Instrument = env['trading.daily_instrument']
    stale = Instrument.search([('instrument_key', 'not in', VALID_KEYS)])
    if stale:
        _logger.info(
            "Removing %d stale trading.daily_instrument records: %s",
            len(stale), stale.mapped('instrument_key')
        )
        stale.unlink()
    else:
        _logger.info("No stale trading.daily_instrument records found.")

    # 2 — Remap instrument values in sim_position that use old symbols
    REMAP = {
        'DAX':    'EWG',
        'DJI':    'DIA',
        'SPX':    'SPY',
        'NDX':    'QQQ',
        'GER40':  'EWG',
        'US30':   'DIA',
        'SPX500': 'SPY',
        'NAS100': 'QQQ',
        'XAG/USD': 'XAU/USD',
        'WTI/USD': 'XAU/USD',
        'XNG/USD': 'XAU/USD',
        'EUR/CHF': 'USD/NOK',
        'NZD/JPY': 'USD/ZAR',
        'AUD/CAD': 'EUR/CAD',
    }
    for old_sym, new_sym in REMAP.items():
        # Fix sim positions
        env.cr.execute(
            "UPDATE trading_sim_position SET instrument = %s WHERE instrument = %s",
            (new_sym, old_sym)
        )
        # Fix trade log (now Char, but old records may have stale values)
        env.cr.execute(
            "UPDATE trading_trade_log SET instrument = %s WHERE instrument = %s",
            (new_sym, old_sym)
        )
    _logger.info("Instrument remapping complete.")

    # 3 — Remove stale instruments from daily_instrument catalogue via ORM
    stale_catalogue = env['trading.daily_instrument'].sudo().search([
        ('instrument_key', 'not in', VALID_KEYS)
    ])
    if stale_catalogue:
        _logger.info("Removing %d stale catalogue entries: %s",
                     len(stale_catalogue), stale_catalogue.mapped('instrument_key'))
        stale_catalogue.unlink()

    # 4 — Remove stale instrument_ids links from all daily_analysis sessions
    # Find all analysis sessions and remove any linked instruments not in VALID_KEYS
    try:
        all_analyses = env['trading.daily_analysis'].sudo().search([])
        for analysis in all_analyses:
            stale_linked = analysis.instrument_ids.filtered(
                lambda i: i.instrument_key not in VALID_KEYS
            )
            if stale_linked:
                analysis.instrument_ids = [(3, inst.id) for inst in stale_linked]
                _logger.info("Removed %d stale instruments from analysis %d",
                             len(stale_linked), analysis.id)
    except Exception as e:
        _logger.warning("Could not clean analysis instrument_ids: %s", e)

    # 5 — Verify yfinance is installed (required for US stock data)
    try:
        import yfinance  # noqa
        _logger.info("yfinance is installed and ready for US stock data.")
    except ImportError:
        _logger.warning(
            "yfinance is NOT installed. US stock signals (AAPL, TSLA etc) will fail. "
            "Fix: SSH into Odoo.sh and run: pip install yfinance --break-system-packages"
        )
