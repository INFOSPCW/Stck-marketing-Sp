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
]


def post_init_hook(env):
    """Remove stale instrument records after module upgrade."""
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
