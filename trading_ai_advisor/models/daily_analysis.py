# -*- coding: utf-8 -*-
"""
daily_analysis.py
=================
Daily Market Analysis — scans multiple forex and crypto instruments in one
session and produces a ranked morning briefing.

Instruments (9 default):
  Forex:  EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, GBP/JPY
  Crypto: BTC/USDT, ETH/USDT, SOL/USDT

Workflow:
  1. User creates a Daily Analysis session (one per day)
  2. Uploads books ZIP (or reuses existing knowledge from a Forex/Crypto Brain)
  3. Clicks "Run Analysis" — the module:
       a. Fetches live price data for every selected instrument (CCXT for crypto,
          Alpha Vantage for forex — falls back to manual attachments)
       b. Computes technical indicators per instrument
       c. Fetches latest news per instrument
       d. Asks Claude for a signal + score for each
       e. Ranks results: STRONG BUY → BUY → HOLD → SELL → STRONG SELL
  4. A ranked briefing card shows the best opportunities for the day
"""

import re
import io
import json
import time
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import zipfile
import logging
import math
import urllib.request
from urllib.error import HTTPError
import datetime as dt

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Instrument catalogue — 29 instruments across 4 asset classes
# ─────────────────────────────────────────────────────────────────────────────

DAILY_INSTRUMENTS = [
    # ── Forex majors (8) ──────────────────────────────────────────────────────
    ('EUR/USD',  'EUR/USD  — Euro/Dollar',         'forex',  'twelvedata'),
    ('GBP/USD',  'GBP/USD  — Pound/Dollar',        'forex',  'twelvedata'),
    ('USD/JPY',  'USD/JPY  — Dollar/Yen',          'forex',  'twelvedata'),
    ('AUD/USD',  'AUD/USD  — Aussie/Dollar',       'forex',  'twelvedata'),
    ('USD/CAD',  'USD/CAD  — Dollar/Loonie',       'forex',  'twelvedata'),
    ('USD/CHF',  'USD/CHF  — Dollar/Swissie',      'forex',  'twelvedata'),
    ('NZD/USD',  'NZD/USD  — Kiwi/Dollar',         'forex',  'twelvedata'),
    ('USD/SGD',  'USD/SGD  — Dollar/S.Dollar',     'forex',  'twelvedata'),
    # ── Forex crosses (8) ─────────────────────────────────────────────────────
    ('GBP/JPY',  'GBP/JPY  — Pound/Yen',          'forex',  'twelvedata'),
    ('EUR/JPY',  'EUR/JPY  — Euro/Yen',            'forex',  'twelvedata'),
    ('AUD/JPY',  'AUD/JPY  — Aussie/Yen',          'forex',  'twelvedata'),
    ('EUR/GBP',  'EUR/GBP  — Euro/Pound',          'forex',  'twelvedata'),
    ('USD/NOK',  'USD/NOK  — Dollar/Krone (NO)',    'forex',  'twelvedata'),
    ('GBP/CHF',  'GBP/CHF  — Pound/Swissie',      'forex',  'twelvedata'),
    ('USD/ZAR',  'USD/ZAR  — Dollar/Rand',         'forex',  'twelvedata'),
    ('USD/MXN',  'USD/MXN  — Dollar/Peso',         'forex',  'twelvedata'),
    # ── Commodities (2 — free tier) ───────────────────────────────────────────
    ('XAU/USD',  'XAU/USD  — Gold',                'forex',  'twelvedata'),
    ('EUR/CAD',  'EUR/CAD  — Euro/Loonie',         'forex',  'twelvedata'),
    # ── Indices (4) ───────────────────────────────────────────────────────────
    ('DIA',      'DIA      — Dow Jones ETF',        'index',  'twelvedata'),
    ('SPY',      'SPY      — S&P 500 ETF',          'index',  'twelvedata'),
    ('QQQ',      'QQQ      — Nasdaq 100 ETF',       'index',  'twelvedata'),
    ('EWG',      'EWG      — Germany ETF (DAX)',    'index',  'twelvedata'),
    # ── US Stocks (Yahoo Finance — free, no API key required) ────────────────
    ('AAPL',     'AAPL     — Apple Inc.',            'stock',  'yfinance'),
    ('TSLA',     'TSLA     — Tesla Inc.',             'stock',  'yfinance'),
    ('NVDA',     'NVDA     — NVIDIA Corp.',           'stock',  'yfinance'),
    ('MSFT',     'MSFT     — Microsoft Corp.',        'stock',  'yfinance'),
    ('AMZN',     'AMZN     — Amazon.com Inc.',        'stock',  'yfinance'),
    ('META',     'META     — Meta Platforms Inc.',    'stock',  'yfinance'),
    ('GOOGL',    'GOOGL    — Alphabet Inc.',          'stock',  'yfinance'),

    # ── Commodities — Yahoo Finance futures (free, no API key) ───────────────
    # Energy
    ('CL=F',     'CL=F     — WTI Crude Oil',          'commodity', 'yfinance'),
    ('BZ=F',     'BZ=F     — Brent Crude Oil',         'commodity', 'yfinance'),
    ('NG=F',     'NG=F     — Natural Gas',              'commodity', 'yfinance'),
    # Precious metals
    ('SI=F',     'SI=F     — Silver',                   'commodity', 'yfinance'),
    ('GC=F',     'GC=F     — Gold Futures (COMEX)',     'commodity', 'yfinance'),
    ('HG=F',     'HG=F     — Copper',                   'commodity', 'yfinance'),
    ('PL=F',     'PL=F     — Platinum',                 'commodity', 'yfinance'),
    # Agriculturals
    ('ZW=F',     'ZW=F     — Wheat',                    'commodity', 'yfinance'),
    ('ZC=F',     'ZC=F     — Corn',                     'commodity', 'yfinance'),
    ('KC=F',     'KC=F     — Coffee',                   'commodity', 'yfinance'),
    # ── Crypto (5) ────────────────────────────────────────────────────────────
    ('BTC/USDT', 'BTC/USDT — Bitcoin',             'crypto', 'binance'),
    ('ETH/USDT', 'ETH/USDT — Ethereum',            'crypto', 'binance'),
    ('SOL/USDT', 'SOL/USDT — Solana',              'crypto', 'binance'),
    ('XRP/USDT', 'XRP/USDT — Ripple',              'crypto', 'binance'),
    ('BNB/USDT', 'BNB/USDT — BNB',                 'crypto', 'binance'),
]

# Session windows: best open/close times in GMT for each instrument
SESSION_WINDOWS = {
    # Forex majors
    'EUR/USD':  {'open': '13:00', 'close': '16:00', 'session': 'London/NY overlap'},
    'GBP/USD':  {'open': '08:00', 'close': '16:00', 'session': 'London + NY overlap'},
    'USD/JPY':  {'open': '00:00', 'close': '09:00', 'session': 'Tokyo + London open'},
    'AUD/USD':  {'open': '22:00', 'close': '07:00', 'session': 'Sydney + Tokyo'},
    'USD/CAD':  {'open': '13:00', 'close': '17:00', 'session': 'NY session'},
    'USD/CHF':  {'open': '08:00', 'close': '15:00', 'session': 'London session'},
    'NZD/USD':  {'open': '21:00', 'close': '06:00', 'session': 'Sydney + Asia'},
    'USD/SGD':  {'open': '01:00', 'close': '09:00', 'session': 'Asia session'},
    # Forex crosses
    'GBP/JPY':  {'open': '08:00', 'close': '12:00', 'session': 'London open'},
    'EUR/JPY':  {'open': '08:00', 'close': '16:00', 'session': 'London + NY'},
    'AUD/JPY':  {'open': '00:00', 'close': '08:00', 'session': 'Tokyo session'},
    'EUR/GBP':  {'open': '08:00', 'close': '11:00', 'session': 'London open'},
    'USD/NOK':  {'open': '07:00', 'close': '15:00', 'session': 'European session'},
    'GBP/CHF':  {'open': '08:00', 'close': '11:00', 'session': 'London open'},
    'USD/ZAR':  {'open': '06:00', 'close': '14:00', 'session': 'Johannesburg + London'},
    'USD/MXN':  {'open': '13:00', 'close': '17:00', 'session': 'NY session'},
    # Commodities (free tier)
    'XAU/USD':  {'open': '08:00', 'close': '17:00', 'session': 'London + NY'},
    'EUR/CAD':  {'open': '13:00', 'close': '17:00', 'session': 'NY session'},
    # Indices (correct Twelve Data symbols)
    'DIA':      {'open': '13:30', 'close': '16:00', 'session': 'NYSE open hours'},
    'SPY':      {'open': '13:30', 'close': '16:00', 'session': 'NYSE open hours'},
    # US Stocks — NYSE/NASDAQ 13:30–20:00 GMT (15:30–22:00 NL)
    'AAPL':     {'open': '13:30', 'close': '20:00', 'session': 'NASDAQ hours'},
    'TSLA':     {'open': '13:30', 'close': '20:00', 'session': 'NASDAQ hours'},
    'NVDA':     {'open': '13:30', 'close': '20:00', 'session': 'NASDAQ hours'},
    'MSFT':     {'open': '13:30', 'close': '20:00', 'session': 'NASDAQ hours'},
    'AMZN':     {'open': '13:30', 'close': '20:00', 'session': 'NASDAQ hours'},
    'META':     {'open': '13:30', 'close': '20:00', 'session': 'NASDAQ hours'},
    'GOOGL':    {'open': '13:30', 'close': '20:00', 'session': 'NASDAQ hours'},
    # Commodities — futures trade nearly 24h, but best liquidity windows:
    # Energy: NYMEX open = 00:00–21:00 GMT, peak liquidity 13:00–19:00 GMT
    'CL=F':  {'open': '13:00', 'close': '19:00', 'session': 'NYMEX peak (13:00-19:00 GMT)'},
    'BZ=F':  {'open': '13:00', 'close': '19:00', 'session': 'ICE Brent peak (13:00-19:00 GMT)'},
    'NG=F':  {'open': '13:00', 'close': '19:00', 'session': 'NYMEX Natural Gas peak'},
    # Precious metals: COMEX 00:00–21:00 GMT, peak = London+NY overlap 07:00–16:00
    'SI=F':  {'open': '07:00', 'close': '16:00', 'session': 'COMEX Silver peak (London+NY)'},
    'GC=F':  {'open': '07:00', 'close': '16:00', 'session': 'COMEX Gold peak (London+NY)'},
    'HG=F':  {'open': '07:00', 'close': '16:00', 'session': 'COMEX Copper peak'},
    'PL=F':  {'open': '07:00', 'close': '16:00', 'session': 'NYMEX Platinum peak'},
    # Agriculturals: CBOT 10:30–20:20 GMT
    'ZW=F':  {'open': '10:30', 'close': '20:20', 'session': 'CBOT Wheat hours'},
    'ZC=F':  {'open': '10:30', 'close': '20:20', 'session': 'CBOT Corn hours'},
    'KC=F':  {'open': '10:30', 'close': '19:30', 'session': 'ICE Coffee hours'},
    'QQQ':      {'open': '13:30', 'close': '16:00', 'session': 'NASDAQ open hours'},
    'EWG':      {'open': '14:30', 'close': '21:00', 'session': 'NYSE/XETRA hours'},
    # Crypto
    'BTC/USDT': {'open': '13:30', 'close': '16:00', 'session': 'NY stock market open'},
    'ETH/USDT': {'open': '13:30', 'close': '16:00', 'session': 'NY stock market open'},
    'SOL/USDT': {'open': '13:30', 'close': '16:00', 'session': 'NY stock market open'},
    'XRP/USDT': {'open': '08:00', 'close': '16:00', 'session': 'London + NY'},
    'BNB/USDT': {'open': '13:30', 'close': '16:00', 'session': 'NY stock market open'},
}

# Twelve Data symbol overrides — indices use short ticker, no exchange suffix
_TD_SYMBOL_MAP = {
    'DJI': 'DJI',
    'SPX': 'SPX',
    'NDX': 'NDX',
    'DAX': 'DAX',
}

INSTRUMENT_SELECTION = [(i[0], i[1]) for i in DAILY_INSTRUMENTS]
INSTRUMENT_TYPE      = {i[0]: i[2] for i in DAILY_INSTRUMENTS}


# ─────────────────────────────────────────────────────────────────────────────
# Price data helpers
# ─────────────────────────────────────────────────────────────────────────────

# Track last Twelve Data call time to enforce 8s spacing between calls
_TD_LAST_CALL = [0.0]   # mutable container so it works across calls
_TD_MIN_GAP   = 3.0     # seconds between TD calls (actual gap incl. Claude ≈ 7-8s)


def _fetch_forex_bars(pair, td_key):
    """
    Fetch last 200 5-min bars from Twelve Data API with automatic
    rate-limit compliance and retry.

    Strategy:
      - Enforce 8s minimum gap between consecutive calls (free tier: 8/min)
      - On rate-limit response: auto-wait 65s and retry once
      - Uses 5min bars instead of 1min: 200 bars = ~16h of data,
        sufficient for all indicators. Also reduces bandwidth.

    Key from: twelvedata.com (free signup, no credit card)
    """
    global _TD_LAST_CALL

    # Enforce minimum gap between calls
    elapsed = time.time() - _TD_LAST_CALL[0]
    if elapsed < _TD_MIN_GAP:
        wait_for = _TD_MIN_GAP - elapsed
        _logger.info("TD rate-limit spacing: sleeping %.1fs for %s", wait_for, pair)
        time.sleep(wait_for)

    symbol = _TD_SYMBOL_MAP.get(pair, pair)

    def _do_request():
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}"
            f"&interval=5min"      # 5min bars — enough for all indicators
            f"&outputsize=200"     # 200 × 5min = ~16h of data
            f"&apikey={td_key}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "TradingAI/1.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read())

    # First attempt
    _TD_LAST_CALL[0] = time.time()
    data = _do_request()

    # Handle rate limit with one auto-retry
    status = data.get('status', 'ok')
    if status == 'error':
        msg  = data.get('message', 'unknown error')
        code = data.get('code', 0)
        is_limit = (code in (429,) or
                    'limit' in msg.lower() or
                    'minute' in msg.lower() or
                    'per minute' in msg.lower())
        is_paid  = 'grow' in msg.lower() or 'venture' in msg.lower() or 'plan' in msg.lower()

        if is_paid:
            raise RuntimeError(
                f"Twelve Data: {pair} requires a paid plan. "
                f"Remove this instrument or upgrade at twelvedata.com/pricing."
            )
        if is_limit:
            # Don't sleep 65s — just raise so caller skips this instrument
            # The 3s gap + Claude time between calls prevents sustained rate limiting
            _logger.warning("TD rate limit hit for %s", pair)
            raise RuntimeError(
                f"Twelve Data rate limit hit for {pair}. "
                f"Will retry next run — other instruments continue."
            )
        else:
            raise RuntimeError(f"Twelve Data error for {pair}: {msg}")

    values = data.get('values', [])
    if not values:
        raise RuntimeError(
            f"No data returned for {pair} from Twelve Data. "
            f"Market may be closed or pair symbol not supported on free tier."
        )

    rows = []
    for bar in values:
        try:
            rows.append((
                bar['datetime'].replace(' ', 'T'),
                float(bar['open']), float(bar['high']),
                float(bar['low']),  float(bar['close']), 0.0
            ))
        except (KeyError, ValueError):
            continue

    rows.sort(key=lambda r: r[0])
    return rows


def _fetch_crypto_bars(pair):
    """Fetch last 200 1-min bars from Binance public API via CCXT."""
    try:
        import ccxt
        exchange = ccxt.binance({'enableRateLimit': True})
        symbol   = pair.replace('/', '')
        # Use direct REST for speed (no load_markets overhead for public data)
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1m&limit=200"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "TradingAI/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        rows = []
        for bar in raw:
            ts     = int(bar[0]) // 1000
            dt_str = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
            rows.append((dt_str, float(bar[1]), float(bar[2]),
                          float(bar[3]), float(bar[4]), float(bar[5])))
        rows.sort(key=lambda r: r[0])
        return rows
    except ImportError:
        # CCXT not installed — try raw urllib fallback
        symbol = pair.replace('/', '')
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval=1m&limit=200"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "TradingAI/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        rows = []
        for bar in raw:
            ts     = int(bar[0]) // 1000
            dt_str = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
            rows.append((dt_str, float(bar[1]), float(bar[2]),
                          float(bar[3]), float(bar[4]), float(bar[5])))
        rows.sort(key=lambda r: r[0])
        return rows


# ─────────────────────────────────────────────────────────────────────────────
# Technical indicators (pure Python)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_stock_bars(symbol):
    """
    Fetch last 200 5-minute bars + daily ATR for a US stock using yfinance.
    Returns list of (datetime_str, open, high, low, close, volume) tuples.
    Also stores daily_atr_14 in module-level cache for use in SL calculation.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError(
            "yfinance not installed. SSH into Odoo.sh and run: "
            "pip install yfinance --break-system-packages"
        )

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period='5d', interval='5m',
                              prepost=False, auto_adjust=True)
    except Exception as e:
        raise RuntimeError(f"yfinance network error for {symbol}: {e}.")

    if hist is None or hist.empty:
        try:
            hist = ticker.history(period='1d', interval='5m',
                                  prepost=False, auto_adjust=True)
        except Exception:
            pass

    if hist is None or hist.empty:
        raise RuntimeError(
            f"yfinance returned no data for {symbol}. "
            f"Try: pip install --upgrade yfinance --break-system-packages"
        )

    # ── Fetch DAILY bars for ATR + trend direction ─────────────────────────
    try:
        daily = ticker.history(period='30d', interval='1d', auto_adjust=True)
        if daily is not None and len(daily) >= 5:
            # ATR-14 from daily bars
            tr_list = []
            for i in range(1, min(15, len(daily))):
                h  = float(daily['High'].iloc[i])
                l  = float(daily['Low'].iloc[i])
                pc = float(daily['Close'].iloc[i-1])
                tr_list.append(max(h-l, abs(h-pc), abs(l-pc)))
            if tr_list:
                _daily_atr_cache[symbol] = round(sum(tr_list)/len(tr_list), 4)

            # 5-day trend: % change from 5 sessions ago to today
            close_now  = float(daily['Close'].iloc[-1])
            close_5d   = float(daily['Close'].iloc[-5])
            trend_5d   = round((close_now - close_5d) / close_5d * 100, 2)
            # 20-day trend for longer bias
            close_20d  = float(daily['Close'].iloc[0]) if len(daily) >= 20 else close_5d
            trend_20d  = round((close_now - close_20d) / close_20d * 100, 2)
            _daily_trend_cache[symbol] = {
                'trend_5d':  trend_5d,
                'trend_20d': trend_20d,
                'close_now': close_now,
            }
    except Exception:
        pass  # daily data is best-effort

    rows = []
    for ts, row in hist.iterrows():
        try:
            # Convert pandas Timestamp → ISO string for compatibility
            dt_str = ts.strftime('%Y-%m-%dT%H:%M:%S')
            rows.append((
                dt_str,
                float(row['Open']),
                float(row['High']),
                float(row['Low']),
                float(row['Close']),
                float(row.get('Volume', 0)),
            ))
        except (KeyError, ValueError, TypeError):
            continue

    rows.sort(key=lambda r: r[0])
    # Keep only last 200 bars to match forex/crypto behaviour
    return rows[-200:] if len(rows) > 200 else rows


def _get_stock_live_price(symbol):
    """
    Fetch the current live price for a US stock using yfinance.
    Uses fast_info for a single lightweight request.
    """
    try:
        import yfinance as yf
    except ImportError:
        raise RuntimeError("yfinance not installed. Run: pip install yfinance --break-system-packages")

    ticker = yf.Ticker(symbol)
    try:
        # fast_info is a single lightweight call — no full ticker download
        price = ticker.fast_info.last_price
        if price is None or price <= 0:
            raise ValueError("fast_info returned no price")
        return float(price)
    except Exception:
        # Fallback: use 1-minute history
        hist = ticker.history(period='1d', interval='1m', prepost=False)
        if hist.empty:
            raise RuntimeError(f"Cannot get live price for {symbol} — market may be closed.")
        return float(hist['Close'].iloc[-1])


def _get_stock_news(symbol, company_name, hours=12):
    """
    Fetch recent news for a US stock using yfinance.
    Returns list of {title, snippet} dicts — same format as _fetch_news().
    """
    try:
        import yfinance as yf
        import time as _time
        ticker = yf.Ticker(symbol)
        raw_news = ticker.news or []
        cutoff   = _time.time() - (hours * 3600)
        items    = []
        for n in raw_news:
            if n.get('providerPublishTime', 0) < cutoff:
                continue
            title   = n.get('title', '')
            summary = n.get('summary', '') or ''
            if title:
                items.append({'title': title, 'snippet': summary[:150]})
            if len(items) >= 8:
                break
        return items
    except Exception:
        return []


def _ema(prices, period):
    k, ema = 2 / (period + 1), [prices[0]]
    for p in prices[1:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema

def _rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    if len(gains) < period: return 50.0
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))

def _macd(closes):
    if len(closes) < 26: return 0, 0
    line = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    return line[-1], _ema(line, 9)[-1]

def _compute_indicators(rows, instrument=''):
    if not rows: return {}
    closes = [r[4] for r in rows]
    highs  = [r[2] for r in rows]
    lows   = [r[3] for r in rows]
    rsi_val         = _rsi(closes)
    macd_val, sig   = _macd(closes)
    ema20  = _ema(closes, 20)[-1]  if len(closes) >= 20  else closes[-1]
    ema50  = _ema(closes, 50)[-1]  if len(closes) >= 50  else closes[-1]
    ema200 = _ema(closes, 200)[-1] if len(closes) >= 200 else closes[-1]
    current = closes[-1]
    h = max(highs[-60:]) if len(highs) >= 60 else max(highs)
    l = min(lows[-60:])  if len(lows)  >= 60 else min(lows)
    slope = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0

    # Compute Fibonacci retracement from first 15-min candle
    fib = _fibonacci_levels(rows)

    result = {
        'current_price':    round(current, 6),
        'high_1h':          round(h, 6),
        'low_1h':           round(l, 6),
        'rsi_14':           round(rsi_val, 2),
        'macd':             round(macd_val, 8),
        'macd_signal':      round(sig, 8),
        'macd_hist':        round(macd_val - sig, 8),
        'ema_20':           round(ema20, 6),
        'ema_50':           round(ema50, 6),
        'ema_200':          round(ema200, 6),
        'vs_ema20':         'ABOVE' if current > ema20  else 'BELOW',
        'vs_ema50':         'ABOVE' if current > ema50  else 'BELOW',
        'vs_ema200':        'ABOVE' if current > ema200 else 'BELOW',
        'slope_20bar_pct':  round(slope, 4),
        'bars':             len(rows),
    }
    # ATR-14 (Average True Range) — volatility-aware SL anchor
    # For stocks/commodities: use DAILY ATR from cache (populated by yfinance)
    # For forex/crypto: use 5-min ATR × sqrt(78) to approximate daily (78 5-min bars/day)
    import math as _math
    tr_list = []
    for i in range(1, min(15, len(rows))):
        h_i  = rows[i][2]
        l_i  = rows[i][3]
        pc   = rows[i-1][4]
        tr_list.append(max(h_i - l_i, abs(h_i - pc), abs(l_i - pc)))
    atr_5min = sum(tr_list) / len(tr_list) if tr_list else (h - l) / 60

    # Use daily ATR if available (stocks/commodities), otherwise scale 5-min
    _symbol = rows[0][0].split('T')[0] if rows else ''
    daily_atr   = _daily_atr_cache.get(_symbol, 0)
    daily_trend = _daily_trend_cache.get(_symbol, {})
    if daily_atr > 0:
        atr14 = daily_atr          # real daily ATR from yfinance daily bars
    else:
        # Scale 5-min to approximate daily: multiply by sqrt(78) ≈ 8.83
        atr14 = atr_5min * _math.sqrt(78)

    # ATR as % of price — used for SL/TP minimum guidance in prompt
    atr_pct = (atr14 / current * 100) if current > 0 else 0

    # Apply per-instrument ATR floor — prevents SL from being set inside daily noise
    # The 5-min scaled ATR often underestimates real daily volatility for forex/crypto
    _inst_name = instrument  # passed in directly — rows[0][0] is a datetime string
    _floor = _ATR_FLOORS.get(_inst_name, 0)
    if _floor > 0 and atr_pct < _floor:
        atr_pct = _floor   # use real-world floor instead of underestimated scaled value
        atr14   = _floor * current / 100  # keep consistent with price

    # Pre-compute confirmation score (0-4) so AI knows how many signals align
    # Bullish confirmations: RSI>50, MACD>signal, price>EMA20, slope>0
    # Bearish confirmations: RSI<50, MACD<signal, price<EMA20, slope<0
    bull_conf = sum([
        rsi_val > 52,
        macd_val > sig,
        current > ema20,
        slope > 0,
    ])
    bear_conf = sum([
        rsi_val < 48,
        macd_val < sig,
        current < ema20,
        slope < 0,
    ])
    # Fib adds a confirmation if present and not neutral
    fib_signal = fib.get('fib_signal', 'NEUTRAL')
    if fib_signal == 'BULLISH':   bull_conf = min(bull_conf + 1, 5)
    elif fib_signal == 'BEARISH': bear_conf = min(bear_conf + 1, 5)

    # Counter-trend warning — if daily 5d trend strongly opposes signal direction
    trend_5d  = daily_trend.get('trend_5d', 0)
    trend_20d = daily_trend.get('trend_20d', 0)

    result.update({
        'atr_14':              round(atr14, 6),
        'atr_pct':             round(atr_pct, 4),
        'bull_confirmations':  bull_conf,
        'bear_confirmations':  bear_conf,
        'daily_trend_5d_pct':  round(trend_5d, 2),
        'daily_trend_20d_pct': round(trend_20d, 2),
    })
    result.update(fib)   # merge Fibonacci levels into indicators dict
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Fibonacci Retracement — computed from first 15-min candle of current session
# Based on: "Fibonacci Retracement in Day Trading" (IJIRMPS, Vol 9, Issue 4, 2021)
# ─────────────────────────────────────────────────────────────────────────────

def _fibonacci_levels(rows, session_open_utc=None):
    """
    Compute Fibonacci retracement levels from the first 15-minute candle
    of the current trading session (or the first 3 × 5-min bars).

    Returns a dict with:
        fib_high, fib_low         — H/L of the first 15-min candle
        fib_range                 — fib_high - fib_low
        fib_up_1382, fib_up_1618  — upside extension levels
        fib_dn_1382, fib_dn_1618  — downside extension levels
        fib_retrace_382, _500, _618 — standard retracement levels
        fib_current_zone          — where current price sits vs fib levels
        fib_signal                — BULLISH/BEARISH/NEUTRAL/RANGE
        fib_trend_strength        — STRONG/MODERATE/WEAK
        fib_setup                 — human-readable summary
        fib_first_candle_bars     — number of bars used for the first candle
    """
    if not rows or len(rows) < 3:
        return {}

    # Identify first 15-min candle: use first 3 × 5-min bars
    # If session_open_utc provided, find bars from that time
    first_bars = rows[:3]   # first 3 bars = 15 minutes at 5-min interval
    if session_open_utc:
        session_bars = [r for r in rows if r[0] >= session_open_utc]
        if len(session_bars) >= 3:
            first_bars = session_bars[:3]

    fib_high = max(b[2] for b in first_bars)  # high of 15-min candle
    fib_low  = min(b[3] for b in first_bars)  # low  of 15-min candle
    fib_range = fib_high - fib_low

    if fib_range <= 0:
        return {}

    # Minimum range guard — if first candle is too tight relative to price,
    # Fibonacci extensions will be meaningless (e.g. ETH $1.20 range on $2,326 = 0.05%)
    # Minimum useful range: 0.08% of price for crypto, 0.05% for forex/stocks
    range_pct = (fib_range / fib_high) * 100
    min_range_pct = 0.08 if any(c in str(first_bars[0][0]) for c in ['T', ':']) else 0.05
    # Use simpler threshold: 0.07% works across all asset types
    if range_pct < 0.07:
        # Range too tight — return RANGE-BOUND signal only, no extension targets
        return {
            'fib_high':        round(fib_high, 6),
            'fib_low':         round(fib_low, 6),
            'fib_range':       round(fib_range, 6),
            'fib_range_pct':   round(range_pct, 4),
            'fib_signal':      'NEUTRAL',
            'fib_strength':    'WEAK',
            'fib_range_bound': True,
            'fib_zone':        f"First candle range too tight ({range_pct:.3f}% of price) — Fibonacci not reliable",
            'fib_setup':       (
                f"⚠ FIBONACCI SKIPPED: First 15-min candle range is only {range_pct:.3f}% "
                f"(${fib_range:.4g} on ${fib_high:.5g}). Minimum useful range is 0.07%. "
                f"Price is range-bound — use RSI/MACD/EMA for direction instead of Fibonacci. "
                f"Do NOT use Fibonacci extension targets from this session."
            ),
        }

    # Standard retracement levels (inside the candle range)
    fib_ret_236 = fib_high - 0.236 * fib_range
    fib_ret_382 = fib_high - 0.382 * fib_range
    fib_ret_500 = fib_high - 0.500 * fib_range
    fib_ret_618 = fib_high - 0.618 * fib_range
    fib_ret_786 = fib_high - 0.786 * fib_range

    # Extension levels (outside the first candle, based on paper rules)
    # Upside: from high, extend by 1.382× and 1.618× of the range
    fib_up_1382 = fib_high + 0.382 * fib_range   # = fib_high + 38.2% of range
    fib_up_1618 = fib_high + 0.618 * fib_range   # golden ratio extension up
    fib_up_2618 = fib_high + 1.618 * fib_range   # second target up
    fib_up_3618 = fib_high + 2.618 * fib_range   # third target up

    # Downside: from low, extend downward
    fib_dn_1382 = fib_low - 0.382 * fib_range    # = fib_low - 38.2% of range
    fib_dn_1618 = fib_low - 0.618 * fib_range    # golden ratio extension down
    fib_dn_2618 = fib_low - 1.618 * fib_range    # second target down
    fib_dn_3618 = fib_low - 2.618 * fib_range    # third target down

    # Current price position
    current = rows[-1][4]

    # Determine which zone current price is in
    if current > fib_up_1618:
        zone = f"ABOVE UP-1.618 ({fib_up_1618:.5g}) — STRONG UPSIDE BREAKOUT"
        fib_signal = 'BULLISH'
        strength   = 'STRONG'
    elif current > fib_up_1382:
        zone = f"BETWEEN UP-1.382 ({fib_up_1382:.5g}) and UP-1.618 ({fib_up_1618:.5g}) — UPSIDE EXTENSION"
        fib_signal = 'BULLISH'
        strength   = 'MODERATE'
    elif current > fib_high:
        zone = f"ABOVE first-candle HIGH ({fib_high:.5g}) — testing upside"
        fib_signal = 'BULLISH'
        strength   = 'WEAK'
    elif current >= fib_low:
        zone = f"INSIDE first-candle range ({fib_low:.5g}–{fib_high:.5g}) — RANGE-BOUND"
        fib_signal = 'NEUTRAL'
        strength   = 'WEAK'
    elif current > fib_dn_1382:
        zone = f"BETWEEN first-candle LOW ({fib_low:.5g}) and DN-1.382 ({fib_dn_1382:.5g})"
        fib_signal = 'BEARISH'
        strength   = 'WEAK'
    elif current > fib_dn_1618:
        zone = f"BETWEEN DN-1.382 ({fib_dn_1382:.5g}) and DN-1.618 ({fib_dn_1618:.5g})"
        fib_signal = 'BEARISH'
        strength   = 'MODERATE'
    else:
        zone = f"BELOW DN-1.618 ({fib_dn_1618:.5g}) — STRONG DOWNSIDE BREAKOUT"
        fib_signal = 'BEARISH'
        strength   = 'STRONG'

    # Paper rule: range-bound = price stayed within 1.618 levels all day
    range_bound = (fib_dn_1618 < current < fib_up_1618)

    # Setup summary following paper methodology
    if strength == 'STRONG' and fib_signal == 'BULLISH':
        _mid_up = (fib_up_2618 + fib_up_3618) / 2
        if current > fib_up_3618:
            setup = (f"TRENDING UP (EXTENDED): Price ({current:.5g}) has ALREADY PASSED "
                     f"UP-2.618 ({fib_up_2618:.5g}) AND UP-3.618 ({fib_up_3618:.5g}). "
                     f"All Fibonacci targets are behind current price. "
                     f"DO NOT use these levels as TP. Use trailing stop or next resistance above {current:.5g}.")
        elif current > fib_up_2618:
            setup = (f"TRENDING UP: Broke UP-1.618 ({fib_up_1618:.5g}) and UP-2.618 ({fib_up_2618:.5g}). "
                     f"Current price ({current:.5g}) is above UP-2.618. "
                     f"Valid remaining targets: Midpoint={_mid_up:.5g}, UP-3.618={fib_up_3618:.5g}. "
                     f"Do NOT set TP below current price.")
        else:
            setup = (f"TRENDING UP: Price broke UP-1.618 ({fib_up_1618:.5g}). "
                     f"Targets: UP-2.618 = {fib_up_2618:.5g}, UP-3.618 = {fib_up_3618:.5g}. "
                     f"Midpoint 2.618-3.618 = {_mid_up:.5g}.")
    elif strength == 'STRONG' and fib_signal == 'BEARISH':
        _mid_dn = (fib_dn_2618 + fib_dn_3618) / 2
        if current < fib_dn_3618:
            setup = (f"TRENDING DOWN (EXTENDED): Price ({current:.5g}) has ALREADY PASSED "
                     f"DN-2.618 ({fib_dn_2618:.5g}) AND DN-3.618 ({fib_dn_3618:.5g}). "
                     f"All Fibonacci targets are behind current price. "
                     f"DO NOT use these levels as TP. Use trailing stop or next support below {current:.5g}.")
        elif current < fib_dn_2618:
            setup = (f"TRENDING DOWN: Broke DN-1.618 ({fib_dn_1618:.5g}) and DN-2.618 ({fib_dn_2618:.5g}). "
                     f"Current price ({current:.5g}) is below DN-2.618. "
                     f"Valid remaining targets: Midpoint={_mid_dn:.5g}, DN-3.618={fib_dn_3618:.5g}. "
                     f"Do NOT set TP above current price.")
        else:
            setup = (f"TRENDING DOWN: Price broke DN-1.618 ({fib_dn_1618:.5g}). "
                     f"Targets: DN-2.618 = {fib_dn_2618:.5g}, DN-3.618 = {fib_dn_3618:.5g}. "
                     f"Midpoint 2.618-3.618 = {_mid_dn:.5g}.")
    elif fib_signal == 'NEUTRAL':
        setup = (f"RANGE-BOUND: Price inside first-candle ({fib_low:.5g}–{fib_high:.5g}). "
                 f"Fib boundaries: DN-1.618={fib_dn_1618:.5g} / UP-1.618={fib_up_1618:.5g}. "
                 f"Do not trade breakouts unless 1.618 level is SUSTAINED for 2-4 candles.")
    else:
        setup = (f"Fib zone: {zone}. "
                 f"Key levels — UP: {fib_up_1382:.5g} / {fib_up_1618:.5g} | "
                 f"DOWN: {fib_dn_1382:.5g} / {fib_dn_1618:.5g}")

    return {
        'fib_high':        round(fib_high,    6),
        'fib_low':         round(fib_low,     6),
        'fib_range':       round(fib_range,   6),
        'fib_ret_236':     round(fib_ret_236, 6),
        'fib_ret_382':     round(fib_ret_382, 6),
        'fib_ret_500':     round(fib_ret_500, 6),
        'fib_ret_618':     round(fib_ret_618, 6),
        'fib_ret_786':     round(fib_ret_786, 6),
        'fib_up_1382':     round(fib_up_1382, 6),
        'fib_up_1618':     round(fib_up_1618, 6),
        'fib_up_2618':     round(fib_up_2618, 6),
        'fib_up_3618':     round(fib_up_3618, 6),
        'fib_dn_1382':     round(fib_dn_1382, 6),
        'fib_dn_1618':     round(fib_dn_1618, 6),
        'fib_dn_2618':     round(fib_dn_2618, 6),
        'fib_dn_3618':     round(fib_dn_3618, 6),
        'fib_current':     round(current,     6),
        'fib_zone':        zone,
        'fib_signal':      fib_signal,
        'fib_strength':    strength,
        'fib_range_bound': range_bound,
        'fib_setup':       setup,
        'fib_first_bars':  len(first_bars),
    }


# Daily ATR cache — populated by _fetch_stock_bars, used in _compute_indicators
_daily_atr_cache = {}

# Minimum ATR floors per instrument — based on real daily volatility research
# (Myfxbook, offbeatforex.com, journal analysis May 2026)
# These prevent SL from being set tighter than the instrument's normal daily noise
_ATR_FLOORS = {
    # Forex Majors — daily ATR ~70-110 pips
    'EUR/USD': 0.70, 'GBP/USD': 0.80, 'USD/JPY': 0.75,
    'AUD/USD': 0.65, 'USD/CAD': 0.65, 'USD/CHF': 0.65, 'NZD/USD': 0.60,
    # Forex Crosses — daily ATR ~80-120 pips
    'EUR/JPY': 0.85, 'GBP/JPY': 0.90, 'AUD/JPY': 0.80, 'CAD/JPY': 0.80,
    'EUR/GBP': 0.55, 'EUR/CAD': 0.80, 'GBP/CHF': 0.80, 'USD/SGD': 0.50,
    # Forex Exotics — daily ATR 150-300 pips
    'USD/ZAR': 1.40, 'USD/MXN': 1.20, 'USD/NOK': 1.20,
    # Crypto — daily range 2-6%
    'BTC/USDT': 1.20, 'ETH/USDT': 1.50, 'BNB/USDT': 1.20,
    'SOL/USDT': 1.80, 'XRP/USDT': 1.50,
    # US Stocks
    'AAPL': 2.00, 'MSFT': 2.00, 'GOOGL': 2.00, 'AMZN': 2.00, 'META': 2.00,
    'NVDA': 3.50, 'TSLA': 4.00,
    # Index ETFs
    'SPY': 0.80, 'QQQ': 1.00, 'DIA': 0.70, 'EWG': 1.00,
    # Commodities
    'XAU/USD': 1.50, 'GC=F': 1.50, 'SI=F': 2.00,
    'CL=F': 2.00, 'BZ=F': 2.00, 'NG=F': 3.00,
    'HG=F': 1.50, 'PL=F': 2.00,
    'ZW=F': 2.00, 'ZC=F': 1.80, 'KC=F': 1.80,
}
# Daily trend cache — 5d and 20d % change, used to filter counter-trend signals
_daily_trend_cache = {}

# ─────────────────────────────────────────────────────────────────────────────
# News fetch — Finnhub (primary) + Serper (fallback)
# ─────────────────────────────────────────────────────────────────────────────

# Finnhub category map — maps instrument to Finnhub market news category
_FINNHUB_CATEGORY = {
    # Forex — use 'forex' category
    'EUR/USD':'forex','GBP/USD':'forex','USD/JPY':'forex','AUD/USD':'forex',
    'USD/CAD':'forex','USD/CHF':'forex','NZD/USD':'forex','GBP/JPY':'forex',
    'EUR/JPY':'forex','AUD/JPY':'forex','EUR/GBP':'forex','EUR/CAD':'forex',
    'GBP/CHF':'forex','USD/ZAR':'forex','USD/MXN':'forex','USD/SGD':'forex',
    'USD/NOK':'forex','CAD/JPY':'forex',
    # Crypto — use 'crypto' category
    'BTC/USDT':'crypto','ETH/USDT':'crypto','SOL/USDT':'crypto',
    'XRP/USDT':'crypto','BNB/USDT':'crypto',
    # Commodities and indices — use 'general' category
    'XAU/USD':'general','GC=F':'general','CL=F':'general','NG=F':'general',
    'SI=F':'general','PL=F':'general','HG=F':'general',
    'ZW=F':'general','ZC=F':'general','KC=F':'general','BZ=F':'general',
    # Stock ETFs — use 'general'
    'SPY':'general','QQQ':'general','DIA':'general','EWG':'general',
}

# Finnhub stock symbol map — company news endpoint
_FINNHUB_STOCK_SYMBOL = {
    'AAPL':'AAPL','TSLA':'TSLA','NVDA':'NVDA','MSFT':'MSFT',
    'AMZN':'AMZN','META':'META','GOOGL':'GOOGL',
}

# Serper query map — fallback if Finnhub not set
_NEWS_QUERY_MAP = {
    'EUR/USD': 'EUR USD euro dollar ECB Fed',
    'GBP/USD': 'GBP USD pound dollar Bank of England',
    'USD/JPY': 'USD JPY dollar yen Bank of Japan',
    'AUD/USD': 'AUD USD Australian dollar RBA',
    'USD/CAD': 'USD CAD dollar loonie oil Bank of Canada',
    'USD/CHF': 'USD CHF dollar Swiss franc SNB',
    'NZD/USD': 'NZD USD kiwi dollar RBNZ',
    'GBP/JPY': 'GBP JPY pound yen',
    'EUR/JPY': 'EUR JPY euro yen',
    'EUR/GBP': 'EUR GBP euro pound',
    'XAU/USD': 'gold price XAU inflation Fed rates',
    'SPY':     'S&P 500 stock market Fed',
    'QQQ':     'Nasdaq tech stocks Fed',
    'DIA':     'Dow Jones stocks economy',
    'EWG':     'Germany DAX ECB European economy',
    'BTC/USDT':'Bitcoin BTC crypto market',
    'ETH/USDT':'Ethereum ETH crypto',
    'SOL/USDT':'Solana SOL crypto',
    'XRP/USDT':'XRP Ripple crypto',
    'BNB/USDT':'BNB Binance crypto',
    'NG=F':    'natural gas price EIA inventory energy',
    'CL=F':    'crude oil price OPEC WTI',
    'GC=F':    'gold price XAU inflation Fed',
    'SI=F':    'silver price metals commodities',
    'ZW=F':    'wheat price USDA agricultural commodities',
}


def _fetch_finnhub_calendar(finnhub_key, hours_ahead=8):
    """
    Fetch upcoming economic events from Finnhub economic calendar.
    Returns list of high-impact events in the next hours_ahead hours.
    Free tier: 60 calls/min — plenty for this.
    """
    if not finnhub_key:
        return []
    try:
        import datetime as _dt
        now   = _dt.datetime.utcnow()
        end   = now + _dt.timedelta(hours=hours_ahead)
        from_str = now.strftime('%Y-%m-%d')
        to_str   = end.strftime('%Y-%m-%d')
        url = (f"https://finnhub.io/api/v1/calendar/economic"
               f"?from={from_str}&to={to_str}&token={finnhub_key}")
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            events = data.get('economicCalendar', [])
            # Filter high-impact only (impact = 3)
            high_impact = []
            for e in events:
                if int(e.get('impact', 0)) >= 2:
                    high_impact.append({
                        'event':    e.get('event', ''),
                        'country':  e.get('country', ''),
                        'time':     e.get('time', ''),
                        'impact':   e.get('impact', 0),
                        'actual':   e.get('actual', ''),
                        'forecast': e.get('estimate', ''),
                        'previous': e.get('prev', ''),
                    })
            return high_impact
    except Exception as e:
        _logger.debug("Finnhub calendar fetch failed: %s", e)
        return []


def _fetch_finnhub_earnings(finnhub_key, days_ahead=3):
    """Fetch upcoming earnings releases — avoid trading stocks pre-earnings."""
    if not finnhub_key:
        return []
    try:
        import datetime as _dt
        now   = _dt.datetime.utcnow()
        end   = now + _dt.timedelta(days=days_ahead)
        from_str = now.strftime('%Y-%m-%d')
        to_str   = end.strftime('%Y-%m-%d')
        url = (f"https://finnhub.io/api/v1/calendar/earnings"
               f"?from={from_str}&to={to_str}&token={finnhub_key}")
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data  = json.loads(resp.read())
            items = data.get('earningsCalendar', [])
            # Only keep our tracked stocks
            tracked = {'AAPL','TSLA','NVDA','MSFT','AMZN','META','GOOGL'}
            return [
                {'symbol': e.get('symbol'), 'date': e.get('date'), 'hour': e.get('hour')}
                for e in items if e.get('symbol') in tracked
            ]
    except Exception as e:
        _logger.debug("Finnhub earnings fetch failed: %s", e)
        return []


def _fetch_finnhub_news(finnhub_key, instrument, hours=6):
    """
    Fetch news from Finnhub — primary news source.
    60 calls/min free. Returns list of {title, snippet, sentiment} dicts.
    Finnhub provides built-in sentiment scores unlike Serper.
    """
    if not finnhub_key:
        return []

    import datetime as _dt
    now  = int(_dt.datetime.utcnow().timestamp())
    then = now - (hours * 3600)

    try:
        # Check if it's a stock with a dedicated company news endpoint
        stock_sym = _FINNHUB_STOCK_SYMBOL.get(instrument)
        if stock_sym:
            from_dt = _dt.datetime.utcfromtimestamp(then).strftime('%Y-%m-%d')
            to_dt   = _dt.datetime.utcnow().strftime('%Y-%m-%d')
            url = (f"https://finnhub.io/api/v1/company-news"
                   f"?symbol={stock_sym}&from={from_dt}&to={to_dt}&token={finnhub_key}")
        else:
            # Market news by category
            cat = _FINNHUB_CATEGORY.get(instrument, 'general')
            url = f"https://finnhub.io/api/v1/news?category={cat}&token={finnhub_key}"

        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            articles = json.loads(resp.read())
            if not isinstance(articles, list):
                articles = articles.get('articles', []) if isinstance(articles, dict) else []

            items = []
            cutoff = now - (hours * 3600)
            for a in articles[:12]:
                ts = a.get('datetime', a.get('publishedAt', 0))
                if isinstance(ts, str):
                    try:
                        import time as _time
                        ts = int(_time.mktime(_dt.datetime.strptime(
                            ts[:19], '%Y-%m-%dT%H:%M:%S').timetuple()))
                    except Exception:
                        ts = 0
                if ts and ts < cutoff:
                    continue
                title   = a.get('headline', a.get('title', ''))
                snippet = a.get('summary', a.get('description', ''))[:150]
                if not title:
                    continue
                # Finnhub sometimes provides sentiment
                sentiment_score = a.get('sentiment', None)
                items.append({
                    'title':   title,
                    'snippet': snippet,
                    'sentiment_score': sentiment_score,
                })
                if len(items) >= 8:
                    break
            return items
    except Exception as e:
        _logger.debug("Finnhub news fetch failed for %s: %s", instrument, e)
        return []


def _fetch_news(serper_key, instrument, hours=6, finnhub_key=''):
    """
    Fetch news — Finnhub primary, Serper fallback.
    Returns list of {title, snippet} dicts.
    """
    # Try Finnhub first (better rate limits + sentiment + calendar)
    if finnhub_key:
        items = _fetch_finnhub_news(finnhub_key, instrument, hours=hours)
        if items:
            return items

    # Fallback to Serper
    if not serper_key:
        return []

    query = _NEWS_QUERY_MAP.get(instrument)
    if not query:
        base = instrument.split('/')[0]
        if 'USDT' in instrument:
            query = f"{base} cryptocurrency price market"
        elif '/' in instrument:
            query = f"{instrument} forex currency"
        else:
            query = f"{instrument} market price"

    try:
        payload = json.dumps({"q": query, "num": 5, "tbs": f"qdr:h{hours}"}).encode()
        req = urllib.request.Request(
            "https://google.serper.dev/news", data=payload,
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return [
                {'title': i.get('title', ''), 'snippet': i.get('snippet', '')}
                for i in data.get('news', [])[:8]
                if i.get('title')
            ]
    except Exception as e:
        _logger.debug("Serper news fetch failed for %s: %s", instrument, e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Claude API
# ─────────────────────────────────────────────────────────────────────────────

def _claude_post(api_key, payload, timeout=30, max_retries=2):
    """
    Post to Claude API with smart retry.
    timeout=30s, max_retries=2: total wait = 5+10 = 15s max per instrument.
    Worst case 44 instruments: 44 × (30+15) = 33 min — fits in cron window.
    If Claude is overloaded after 2 retries, skip and save HOLD.
    """
    delay = 5
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
                wait = min(wait, 15)  # never wait more than 15s per retry
                _logger.warning("Claude %s (attempt %d/%d) waiting %ds…",
                                e.code, attempt, max_retries, wait)
                time.sleep(wait)
                delay = min(delay * 2, 15)  # cap at 15s
            elif e.code in (529, 503, 429):
                # All retries exhausted — skip this instrument gracefully
                _logger.warning("Claude overloaded after %d retries for this instrument — skipping",
                                max_retries)
                raise RuntimeError(f"Claude {e.code}: overloaded after {max_retries} retries")
            else:
                raise
        except Exception:
            raise


def _get_mistake_context(env, instrument):
    """
    Query last 20 LOSS trade logs for this instrument.
    Returns a formatted string injected into the Claude prompt so the AI
    can learn from past mistakes and avoid repeating them.
    """
    try:
        logs = env['trading.trade_log'].sudo().search([
            ('instrument', '=', instrument),
            ('outcome', '=', 'LOSS'),
        ], order='trade_date desc', limit=20)
        if not logs:
            return ''
        lines = [f"=== PAST LOSSES ON {instrument} (last {len(logs)}) ==="]
        for lg in logs:
            lines.append(
                f"• {lg.trade_date} | {lg.direction} | Entry {lg.entry_price} "
                f"SL {lg.stop_loss} | PnL {lg.pnl} | "
                f"Mistake: {lg.mistake_category} — {lg.what_went_wrong or 'N/A'}"
            )
        lines.append("INSTRUCTION: Avoid repeating these mistakes. Adjust your analysis accordingly.")
        return '\n'.join(lines)
    except Exception:
        return ''


def _analyse_instrument(instrument, instrument_type, indicators, news_items,
                         brain_summary, api_key, env=None,
                         calendar_events=None, earnings_events=None):
    """
    Ask Claude to score ONE instrument for daily trading.
    Includes session timing advice and injects past loss history if available.
    Returns a dict with signal, score, confidence, reasoning,
    entry/sl/tp, best_open_time, best_close_time.
    """
    # Build indicator block — Fibonacci gets its own formatted section
    _fib_keys = {'fib_high','fib_low','fib_range','fib_ret_236','fib_ret_382',
                 'fib_ret_500','fib_ret_618','fib_ret_786',
                 'fib_up_1382','fib_up_1618','fib_up_2618','fib_up_3618',
                 'fib_dn_1382','fib_dn_1618','fib_dn_2618','fib_dn_3618',
                 'fib_current','fib_zone','fib_signal','fib_strength',
                 'fib_range_bound','fib_setup','fib_first_bars'}
    _base_ind  = {k: v for k, v in indicators.items() if k not in _fib_keys}
    _fib_ind   = {k: v for k, v in indicators.items() if k in _fib_keys}

    # Only include scalar values in the raw indicator block
    ind_block  = "\n".join(
        f"  {k}: {v}" for k, v in _base_ind.items()
        if isinstance(v, (int, float, str, bool)) or v is None
    )

    if _fib_ind:
        fib_block = (
            f"\n\n--- FIBONACCI RETRACEMENT (first 15-min candle) ---"
            f"\n  Signal:   {_fib_ind.get('fib_signal','N/A')} | "
            f"Strength: {_fib_ind.get('fib_strength','N/A')} | "
            f"Range-bound: {_fib_ind.get('fib_range_bound','N/A')}"
            f"\n  First candle: LOW={_fib_ind.get('fib_low','N/A')} "
            f"HIGH={_fib_ind.get('fib_high','N/A')} "
            f"RANGE={_fib_ind.get('fib_range','N/A')}"
            f"\n  Retracement: 23.6%={_fib_ind.get('fib_ret_236','N/A')} "
            f"38.2%={_fib_ind.get('fib_ret_382','N/A')} "
            f"50%={_fib_ind.get('fib_ret_500','N/A')} "
            f"61.8%={_fib_ind.get('fib_ret_618','N/A')}"
            f"\n  Upside ext:  1.382={_fib_ind.get('fib_up_1382','N/A')} "
            f"1.618={_fib_ind.get('fib_up_1618','N/A')} "
            f"2.618={_fib_ind.get('fib_up_2618','N/A')}"
            f"\n  Downside ext: 1.382={_fib_ind.get('fib_dn_1382','N/A')} "
            f"1.618={_fib_ind.get('fib_dn_1618','N/A')} "
            f"2.618={_fib_ind.get('fib_dn_2618','N/A')}"
            f"\n  Current price: {_fib_ind.get('fib_current','N/A')} → {_fib_ind.get('fib_zone','N/A')}"
            f"\n  Setup: {_fib_ind.get('fib_setup','N/A')}"
        )
        ind_block += fib_block
    if news_items:
        news_lines = []
        for n in news_items:
            title   = n.get('title', '')
            snippet = n.get('snippet', '')[:120]
            # Tag obvious sentiment keywords to help Claude weight news impact
            bearish_kw = ['rate hike','hawkish','inflation surge','recession','sell-off',
                          'crash','downgrade','sanctions','war','crisis','ban','decline',
                          'slump','weak','disappoints','miss','deficit']
            bullish_kw = ['rate cut','dovish','strong jobs','beat','surge','rally','upgrade',
                          'deal','stimulus','growth','record','approval','partnership',
                          'breakout','expansion','bullish']
            text_lower = (title + snippet).lower()
            sentiment  = ''
            if any(kw in text_lower for kw in bullish_kw):
                sentiment = ' [BULLISH]'
            elif any(kw in text_lower for kw in bearish_kw):
                sentiment = ' [BEARISH]'
            line = f"• {title}{sentiment}"
            if snippet:
                line += f"\n  {snippet}"
            news_lines.append(line)
        news_block = "\n".join(news_lines)
    else:
        news_block = (
            "No news data available (Serper API may be rate-limited or key not set). "
            "Base decision on technical indicators ONLY. "
            "Apply extra caution: if today is Wednesday (EIA), Friday (NFP risk), or "
            "any major CPI/FOMC release week, treat as HIGH-IMPACT and reduce score by 1."
        )
    brain_cap    = (brain_summary or '')[:3000]
    mkt_type     = "index" if instrument_type == 'index' else \
                   ("forex/commodity" if instrument_type == 'forex' else
                    ("US equity stock" if instrument_type == 'stock' else
                     ("commodity futures (energy/metals/agriculturals)"
                      if instrument_type == 'commodity' else "cryptocurrency")))

    # Session window for this instrument
    session      = SESSION_WINDOWS.get(instrument, {})
    session_open  = session.get('open', 'N/A')
    session_close = session.get('close', 'N/A')
    session_name  = session.get('session', 'N/A')

    # Netherlands offset: UTC+2 Apr–Oct (CEST), UTC+1 Oct–Apr (CET)
    def _gmt_to_nl(hhmm):
        """Convert HH:MM GMT string to Netherlands local time string."""
        if not hhmm or hhmm == 'N/A':
            return hhmm
        try:
            now = dt.datetime.utcnow()
            # DST: last Sunday in March → last Sunday in October
            import calendar
            def last_sunday(year, month):
                last_day = calendar.monthrange(year, month)[1]
                d = dt.date(year, month, last_day)
                return d - dt.timedelta(days=d.weekday() + 1 if d.weekday() != 6 else 0)
            dst_start = last_sunday(now.year, 3)   # last Sun March
            dst_end   = last_sunday(now.year, 10)  # last Sun October
            today     = now.date()
            offset    = 2 if dst_start <= today < dst_end else 1
            tz_label  = 'CEST' if offset == 2 else 'CET'
            h, m      = map(int, hhmm.split(':'))
            nl_h      = (h + offset) % 24
            return f"{nl_h:02d}:{m:02d} {tz_label}"
        except Exception:
            return hhmm

    session_open_nl  = _gmt_to_nl(session_open)
    session_close_nl = _gmt_to_nl(session_close)

    # Current time in both zones
    utc_now = dt.datetime.utcnow()
    now_offset = 2 if _gmt_to_nl('12:00').endswith('CEST') else 1
    nl_now = (utc_now + dt.timedelta(hours=now_offset)).strftime('%H:%M')
    now_tz = 'CEST' if now_offset == 2 else 'CET'

    # Past mistakes context
    mistake_ctx  = _get_mistake_context(env, instrument) if env else ''

    # Learned rules from rulebook (self-learning mechanism)
    learned_rules = _get_learned_rules(env, instrument) if env else ''

    system = f"""You are an aggressive intraday trading analyst specialising in {mkt_type}.
Your job is to find ACTIONABLE trades — prefer BUY/SELL over HOLD whenever a setup exists.
HOLD only when signals are genuinely contradictory or there is NO setup at all.
Score the {instrument} opportunity and return ONLY valid JSON:
{{
  "signal":          "STRONG BUY"|"BUY"|"HOLD"|"SELL"|"STRONG SELL"|"NO TRADE",
  "score":           <integer 1-10, 10=strongest opportunity>,
  "confidence":      "HIGH"|"MEDIUM"|"LOW",
  "entry_price":     <float|null>,
  "stop_loss":       <float|null>,
  "take_profit":     <float|null>,
  "r_r_ratio":       <float — take_profit distance / stop_loss distance, e.g. 2.5>,
  "best_open_time":  "<HH:MM GMT>",
  "best_close_time": "<HH:MM GMT>",
  "hold_overnight":  <true|false — true if setup is likely to continue into next session>,
  "session_advice":  "<one sentence about timing today>",
  "reasoning":       "<2-3 sentences on the setup — be specific about indicator readings>",
  "risk_warning":    "<one sentence>"
}}

SCORING RULES — STRICTLY ENFORCED:

STEP 1 — CONFIRMATION CHECK (must pass before any BUY/SELL signal):
You are given bull_confirmations and bear_confirmations in the indicators (each 0–5).
- bull_confirmations counts: RSI>52, MACD>signal, price>EMA20, slope>0, Fib=BULLISH
- bear_confirmations counts: RSI<48, MACD<signal, price<EMA20, slope<0, Fib=BEARISH
Rules:
  * To output BUY or STRONG BUY:  bull_confirmations >= 2 required. If < 2 → output HOLD.
  * To output SELL or STRONG SELL: bear_confirmations >= 2 required. If < 2 → output HOLD.
  * bull_confirmations >= 4 OR bear_confirmations >= 4 → may output STRONG signal.
  * Contradictory (bull=2, bear=2) → HOLD regardless of other signals.

STEP 2 — SCORE IS DETERMINED BY R/R (calculated from your SL/TP, not assumed):
Set SL and TP FIRST using ATR and price levels, THEN calculate R/R, THEN assign score:
  | R/R        | Max score | Signal allowed      |
  |------------|-----------|---------------------|
  | < 1.0      | 3         | NO TRADE only       |
  | 1.0 – 1.49 | 5         | BUY/SELL (LOW conf) |
  | 1.5 – 1.99 | 7         | BUY/SELL            |
  | 2.0 – 2.99 | 8         | BUY/SELL/STRONG     |
  | >= 3.0     | 10        | STRONG BUY/SELL     |
  CRITICAL: If you cannot construct a valid SL/TP with R/R >= 1.0, output NO TRADE.
  Do NOT output BUY/SELL with R/R < 1.0 under any circumstances.

STEP 3 — ATR-BASED SL/TP (CRITICAL — your SL must survive real daily noise):
  atr_14 and atr_pct are provided in the indicators. These already include the
  per-instrument ATR floor so they reflect REAL daily volatility, not just session noise.
  
  MANDATORY SL RULES — based on real market data (May 2026 live analysis):
  * SL = 1.5 × atr_pct from entry — ABSOLUTE MINIMUM. Never set SL closer.
  * SL FLOORS by instrument type (use whichever is larger):
    - Forex majors (EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD, USD/CHF): min 0.50% SL
    - Forex crosses (EUR/JPY, GBP/JPY, AUD/JPY, EUR/CAD etc): min 0.70% SL
    - Forex exotics (USD/ZAR, USD/MXN, USD/NOK, USD/SGD): min 1.20% SL
    - Crypto majors (BTC, ETH, BNB): min 1.20% SL
    - Crypto alts (SOL, XRP): min 1.50% SL
    - US Stocks large-cap (AAPL, MSFT, GOOGL, AMZN, META): min 2.00% SL
    - US Stocks volatile (NVDA, TSLA): min 3.50% SL
    - Index ETFs (SPY, QQQ, DIA, EWG): min 0.80% SL
    - Gold/Silver/Platinum (XAU/USD, GC=F, SI=F, PL=F): min 1.50% SL
    - Energy (NG=F, CL=F, BZ=F): min 3.00% SL
    - Agricultural (ZW=F, ZC=F, KC=F): min 2.00% SL
  
  CRITICAL CONTEXT FROM TRADE JOURNAL ANALYSIS:
  * EUR/USD trades with 15-pip (0.13%) SL were stopped out by normal noise — minimum is 50 pips (0.50%)
  * AUD/JPY trades with 2-pip (0.02%) SL were always stopped out — daily ATR is 95 pips
  * NVDA trades with 0.64% SL were stopped out intraday — daily ATR is $6-8 (3%)
  * NG=F trades with <1% SL were hit by normal gas volatility — minimum is 3%
  * A SL tighter than 1× daily ATR WILL be hit by normal market noise before trend invalidation
  
  TP RULES:
  * TP = SL × 1.5 minimum (R/R 1.5 is the floor)
  * Use Fibonacci extension levels when available
  * Use next key resistance/support, EMA, or round number
  * Never set TP tighter than 0.5× ATR from entry

STEP 4 — EXTREME RSI CAP:
  * RSI > 80 on BUY: cap score at 6, confidence MEDIUM, add exhaustion warning.
  * RSI < 20 on SELL: cap score at 6, confidence MEDIUM, add exhaustion warning.
  * RSI > 75 or < 25: cap score at 7.
  * These caps apply AFTER R/R scoring — take the lower of the two caps.

STEP 5 — CONFIDENCE:
  * HIGH:   bull/bear_confirmations >= 4 AND R/R >= 2.0 AND RSI not extreme
  * MEDIUM: bull/bear_confirmations >= 3 AND R/R >= 1.5
  * LOW:    everything else that is still a valid trade
  * NEVER output HIGH confidence with R/R < 1.5
- For crypto: SL minimum 0.8% from entry (normal volatility). For forex: 0.3% minimum.
- For US stocks: SL minimum 0.5% from entry. TSLA/NVDA can move 3-5% intraday — set SL accordingly.
  Consider earnings dates and macro events (Fed meetings, CPI) when setting TP targets.
  US stocks only trade 13:30–20:00 GMT — never recommend holding overnight across earnings.
- For commodity futures (CL=F/BZ=F/SI=F etc): SL minimum 0.4% from entry.
  Energy (CL/BZ/NG): highly sensitive to geopolitical news, OPEC decisions, inventory data (EIA Wed 14:30 GMT).
  Precious metals (SI/GC/HG/PL): inverse USD correlation — watch DXY. Peak liquidity 07:00–16:00 GMT.
  Agriculturals (ZW/ZC/KC): weather events, USDA reports drive sudden moves. Wider SL recommended.
  Futures roll near expiry — if volume drops sharply, signal = NO TRADE (rollover risk).

COUNTER-TREND FILTER — MANDATORY:
You are given daily_trend_5d_pct and daily_trend_20d_pct in the indicators.
These show the % price change over the last 5 and 20 trading days.
Rules:
- If daily_trend_5d_pct > +3% AND you want to output SELL/STRONG SELL:
  → This is a counter-trend SELL into a strong uptrend. REDUCE score by 2.
  → If score drops below 5, output HOLD instead with reasoning "counter-trend — 5d trend is +X%"
- If daily_trend_5d_pct < -3% AND you want to output BUY/STRONG BUY:
  → This is a counter-trend BUY into a strong downtrend. REDUCE score by 2.
  → If score drops below 5, output HOLD instead with reasoning "counter-trend — 5d trend is -X%"
- If daily_trend_20d_pct > +10% and SELL signal: mandatory add warning "SELLING INTO STRONG UPTREND"
- If daily_trend_20d_pct < -10% and BUY signal: mandatory add warning "BUYING INTO STRONG DOWNTREND"
- Exception: if RSI > 78 on a buy (overbought exhaustion SELL) or RSI < 22 on a SELL (oversold bounce BUY),
  counter-trend signals ARE allowed but must be marked "MEAN REVERSION" in reasoning.

HIGH-IMPACT EVENT BLACKOUT:
If the news summary mentions CPI, PPI, NFP, Fed meeting, FOMC, rate decision, EIA inventory,
GDP, retail sales, ISM, PMI, JOLTS, ADP payrolls, or any central bank speech/decision
occurring TODAY within the next 4 hours:
→ ALL signals become HOLD with confidence LOW
→ Add to session_advice: "HIGH-IMPACT EVENT TODAY — wait for data release and 30-min settling period"
→ Exception: signals with Fibonacci STRONG confirmation (fib_strength=STRONG) AND R/R > 2.5 may proceed
  at score 5 maximum with mandatory note about event risk.

RECURRING WEEKLY EVENTS (always apply — no Serper needed):
- Every WEDNESDAY 14:30 GMT: EIA Natural Gas storage report → NG=F signals = HOLD if within 2h
- Every WEDNESDAY 14:30 GMT: EIA Crude Oil inventory → CL=F, BZ=F signals = HOLD if within 2h  
- Every FRIDAY ~13:30 GMT: Non-Farm Payrolls (first Friday of month) → ALL signals = HOLD
- Every THURSDAY 12:30 GMT: ECB rate decision (scheduled months) → EUR pairs = HOLD
- FOMC meetings (8×/year) → ALL USD pairs = HOLD day of decision
- If current day is Wednesday and time is 12:30-16:30 GMT: treat NG=F/CL=F as HIGH RISK
- If current day is Friday and time is 12:00-15:00 GMT: check if NFP day, treat as HIGH RISK

SERPER NEWS RELIABILITY NOTE:
If news_block says "No recent news" or shows fewer than 3 headlines, Serper may be rate-limited.
In this case: rely on technical indicators only AND apply extra caution on high-impact days
(Mon open gaps, Wed EIA, Fri NFP, monthly CPI/PPI release weeks).

FIBONACCI RETRACEMENT RULES (apply when fib data is provided in indicators):
These rules come from: Pawar & Bhoite (2021), "Fibonacci Retracement in Day Trading", IJIRMPS Vol.9 Issue 4.
The first 15-minute candle establishes the Fibonacci framework for the entire day.

SETUP IDENTIFICATION:
- RANGE-BOUND day: Price stays within DN-1.618 and UP-1.618 all day. Do NOT trade breakouts unless
  price SUSTAINS beyond 1.618 for 2-4 consecutive 5-min candles. False breakouts are common.
- TRENDING day: Price breaks AND sustains beyond 1.618 level. This is a strong trend signal.
  → After breaking UP-1.618: set TP at UP-2.618, then mid-point between UP-2.618 and UP-3.618
  → After breaking DN-1.618: set TP at DN-2.618, then mid-point between DN-2.618 and DN-3.618
- GAP UP opening: If price tries to fill the gap and touches DN-1.618 but reverses — this is a BULL TRAP
  for sellers. The gap will likely NOT fill. Signal = BUY with SL at DN-1.618.
- GAP DOWN opening: If price fills the gap and then reverses at UP-1.618 — signal = SELL.

ENTRY/EXIT RULES:
- Use 1.382 level for initial profit booking. Use 1.618 for trailing stop on strong trends.
- If price breaks BOTH 1.382 AND 1.618 in a single candle = STRONG breakout (high momentum signal).
- At 2.618: if trend still strong, set next TP at mid-point of 2.618-3.618
  (mid-point = 2.618_price - (2.618_price - 3.618_price) / 2)
- For re-entry: if price breaks 1.618 then pulls back to test it, 1.618 becomes new support/resistance.
  Re-enter on bounce from 1.618 with tight SL.

SIGNAL ADJUSTMENT based on Fibonacci:
- fib_signal=BULLISH + fib_strength=STRONG → add +1 to score, set TP at fib_up_2618
- fib_signal=BEARISH + fib_strength=STRONG → add +1 to score for SELL, set TP at fib_dn_2618
- fib_range_bound=True → lower score by -1 for directional trades; recommend HOLD unless 1.618 sustains
- fib_signal conflicts with RSI/MACD → note in reasoning, do NOT cancel each other — use Fibonacci as
  the primary intraday level tool and RSI/MACD as confirmation
- Always reference specific fib levels (e.g. "SL at DN-1.618 = 4721.50") in your reasoning
- hold_overnight = true if: the trend is strong (score ≥ 7), no major news overnight, and
  the close time is after 20:00 NL or the setup is a multi-session breakout.

NEWS INTERPRETATION RULES (apply when news is provided):
- [BEARISH] news on a BUY setup: lower score by 1-2, add to risk_warning, consider NO TRADE if score would drop below 4
- [BULLISH] news on a BUY setup: raise score by 1, increase confidence if indicators agree
- [BEARISH] news on a SELL setup: raise score by 1, increase confidence
- [BULLISH] news on a SELL setup: lower score by 1-2, add to risk_warning
- Central bank decisions (ECB/Fed/BOE/BOJ): override technicals if within 2h of announcement — signal = HOLD
- "No news data": ignore this section entirely, base decision on indicators only

Optimal session for {instrument}: {session_name} ({session_open} GMT / {session_open_nl}).
Current time: {utc_now.strftime('%H:%M')} GMT / {nl_now} {now_tz}."""

    # Build calendar block for this instrument
    calendar_events = calendar_events or []
    earnings_events = earnings_events or []
    calendar_block = ""
    if calendar_events:
        cal_lines = []
        import datetime as _cdt
        now_utc = _cdt.datetime.utcnow()
        for e in calendar_events:
            evt_time = e.get('time', '')
            impact   = "🔴 HIGH" if e.get('impact', 0) >= 3 else "🟡 MEDIUM"
            actual   = f" | Actual: {e['actual']}" if e.get('actual') else ""
            forecast = f" | Forecast: {e['forecast']}" if e.get('forecast') else ""
            cal_lines.append(
                f"  {impact} {e['event']} ({e['country']}) @ {evt_time} GMT"
                f"{forecast}{actual}"
            )
        if cal_lines:
            calendar_block = "=== ECONOMIC CALENDAR (next 8h) ===\n" + "\n".join(cal_lines)
            calendar_block += ("\n⚠ If any HIGH-IMPACT event is within 2 hours of current time"
                               " → signal = HOLD regardless of technicals.")

    # Earnings warning for this specific instrument
    earnings_block = ""
    if earnings_events:
        for e in earnings_events:
            if e.get('symbol') == instrument:
                earnings_block = (
                    f"⚠ EARNINGS WARNING: {instrument} reports earnings on {e['date']} "
                    f"({e.get('hour', 'TBC')}). Do NOT hold overnight. "
                    f"Score cap = 5 max. Signal = HOLD if within 24h of earnings."
                )
                break

    user_parts = []
    if brain_cap:
        user_parts.append(f"=== DAILY TRADING KNOWLEDGE ===\n{brain_cap}")
    if calendar_block:
        user_parts.append(calendar_block)
    if earnings_block:
        user_parts.append(earnings_block)
    if learned_rules:
        user_parts.append(learned_rules)
    if mistake_ctx:
        user_parts.append(mistake_ctx)
    user_parts.append(f"=== {instrument} INDICATORS ===\n{ind_block}")
    user_parts.append(f"=== {instrument} NEWS (last 12h) ===\n{news_block}")
    user_parts.append(
        f"=== SESSION CONTEXT ===\n"
        f"Optimal window: {session_name}\n"
        f"  GMT:         {session_open}–{session_close} GMT\n"
        f"  Netherlands: {session_open_nl}–{session_close_nl}\n"
        f"Current time:  {utc_now.strftime('%H:%M')} GMT / {nl_now} {now_tz}\n"
        f"\nAnalyse {instrument} for a daily trade. Return JSON only."
    )
    user = "\n\n".join(user_parts)

    try:
        data  = _claude_post(api_key, {
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 1000,
            "system":     system,
            "messages":   [{"role": "user", "content": user}]
        })
        raw   = data['content'][0]['text']
        _logger.debug("Claude raw for %s: %s", instrument, raw[:200])

        # Strip markdown fences
        clean = re.sub(r'^```[a-z]*\s*|\s*```$', '', raw.strip())

        # Attempt 1: direct JSON parse
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            pass

        # Attempt 2: extract JSON object (handles extra text around it)
        json_match = re.search(r'\{[\s\S]*\}', clean)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Attempt 3: truncated JSON — strip incomplete trailing field and close
        try:
            t = re.sub(r',?\s*"[^"]*"\s*:\s*"[^"]*$', '', clean.strip())
            t = re.sub(r',?\s*"[^"]*"\s*:\s*[^,}\]]*$', '', t)
            if not t.endswith('}'):
                t = t.rstrip(',') + '}'
            return json.loads(t)
        except Exception:
            pass

        # Final fallback: extract field by field
        _logger.warning("JSON parse failed for %s. Raw: %s", instrument, raw[:400])
        def _ex(k, d=''):
            m = re.search(rf'"{{k}}"\s*:\s*"([^"]*)"', clean)
            return m.group(1) if m else d
        def _exn(k, d=1):
            m = re.search(rf'"{{k}}"\s*:\s*(\d+)', clean)
            return int(m.group(1)) if m else d
        def _exf(k, d=0.0):
            m = re.search(rf'"{{k}}"\s*:\s*([\d.]+)', clean)
            return float(m.group(1)) if m else d
        def _exb(k, d=False):
            m = re.search(rf'"{{k}}"\s*:\s*(true|false)', clean, re.I)
            return m.group(1).lower() == 'true' if m else d
        sig = _ex('signal', 'NO TRADE')
        return {
            "signal":            sig if sig in ('STRONG BUY','BUY','HOLD','SELL','STRONG SELL','NO TRADE') else 'NO TRADE',
            "score":             _exn('score', 1),
            "confidence":        _ex('confidence', 'LOW'),
            "entry_price":       _exf('entry_price') or None,
            "stop_loss":         _exf('stop_loss') or None,
            "take_profit":       _exf('take_profit') or None,
            "r_r_ratio":         _exf('r_r_ratio', 0.0),
            "hold_overnight":    _exb('hold_overnight', False),
            "best_open_time":    _ex('best_open_time', session_open),
            "best_close_time":   _ex('best_close_time', session_close),
            "best_open_time_nl": _ex('best_open_time_nl', session_open_nl),
            "best_close_time_nl":_ex('best_close_time_nl', session_close_nl),
            "session_advice":    _ex('session_advice', f"Trade during {session_name}."),
            "reasoning":         _ex('reasoning', "Partial response — check raw_response."),
            "risk_warning":      _ex('risk_warning', "Verify signal manually."),
        }
    except Exception as exc:
        _logger.error("Daily analysis failed for %s: %s", instrument, exc)
        return {
            "signal": "NO TRADE", "score": 1, "confidence": "LOW",
            "best_open_time":  session_open,
            "best_close_time": session_close,
            "best_open_time_nl":  session_open_nl,
            "best_close_time_nl": session_close_nl,
            "session_advice": f"Trade during {session_name}.",
            "reasoning": f"API error: {exc}",
            "risk_warning": "Do not trade on this signal.",
        }


def _summarise_books_for_daily(pdf_collection, api_key):
    """Summarise uploaded books into a compact daily-trading knowledge base.
    Per-book Claude calls run in parallel (3 workers) — no dead sleep between them.
    """
    from .trading_brain import _pdf_text_from_bytes

    def _one(item):
        title, pdf_bytes = item
        text = _pdf_text_from_bytes(pdf_bytes, max_chars=6000)
        if len(text) < 200:
            return None
        prompt = (
            f"From '{title}', extract ONLY the rules relevant to daily/intraday trading: "
            f"entry signals, exit signals, stop placement, position sizing, "
            f"news trading, and when to skip a trade. Max 300 words, bullet points."
        )
        try:
            data = _claude_post(api_key, {
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 400,
                "messages":   [{"role": "user", "content": prompt + f"\n\nEXTRACT:\n{text}"}]
            })
            return f"[{title}]\n{data['content'][0]['text']}"
        except Exception:
            return None

    # Run up to 3 book summaries concurrently — avoids sequential 5s sleeps
    summaries = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(_one, pdf_collection[:6]):
            if result:
                summaries.append(result)

    if not summaries:
        return ""
    combined = "\n\n".join(summaries)[:12000]
    try:
        data = _claude_post(api_key, {
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 800,
            "messages":   [{"role": "user", "content":
                "Merge these daily trading rules into ONE concise knowledge base. "
                "Max 600 words. Focus on: entry/exit rules, stop placement, "
                "when to skip trades, risk per trade.\n\n" + combined}]
        })
        return data['content'][0]['text']
    except Exception:
        return combined[:3000]


# ─────────────────────────────────────────────────────────────────────────────
# Odoo Models
# ─────────────────────────────────────────────────────────────────────────────

class DailyAnalysis(models.Model):
    _name        = 'trading.daily_analysis'
    _description = 'Daily Market Analysis Session'
    _order       = 'analysis_date desc'
    _inherit     = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(
        string='Session', compute='_compute_name', store=True)
    analysis_date = fields.Date(
        string='Date', default=fields.Date.today, required=True)
    state = fields.Selection([
        ('draft',    'Ready'),
        ('running',  'Running…'),
        ('done',     'Complete'),
        ('error',    'Error'),
    ], default='draft', string='Status', tracking=True)

    # ── Instruments to analyse ────────────────────────────────────────────────
    instrument_ids = fields.Many2many(
        'trading.daily_instrument',
        'daily_analysis_instrument_rel',
        'analysis_id', 'instrument_id',
        string='Instruments',
        help='Which instruments to include in today\'s analysis.'
    )

    # ── Books (optional — can reuse existing brain knowledge) ─────────────────
    book_attachment_ids = fields.Many2many(
        'ir.attachment',
        'daily_analysis_book_att_rel',
        'analysis_id', 'attachment_id',
        string='Trading Books',
        help='Upload PDF books on daily trading strategies. Optional — '
             'leave empty to skip book knowledge and use indicators + news only.',
    )
    knowledge_summary = fields.Text(
        string='Daily Trading Knowledge', readonly=True)

    # ── Results ───────────────────────────────────────────────────────────────
    result_ids = fields.One2many(
        'trading.daily_result', 'analysis_id', string='Results')
    result_count = fields.Integer(
        string='Instruments Analysed', compute='_compute_result_count')
    top_opportunity = fields.Char(
        string='Top Opportunity', compute='_compute_top', store=True)
    briefing = fields.Text(
        string='Daily Briefing', readonly=True,
        help='Ranked summary of all instruments analysed today.')
    run_log = fields.Text(string='Run Log', readonly=True)
    create_date = fields.Datetime(string='Created', readonly=True)

    @api.depends('analysis_date')
    def _compute_name(self):
        for rec in self:
            rec.name = f"Daily Analysis — {rec.analysis_date or 'New'}"

    @api.depends('result_ids')
    def _compute_result_count(self):
        for rec in self:
            rec.result_count = len(rec.result_ids)

    @api.depends('result_ids.score', 'result_ids.instrument')
    def _compute_top(self):
        for rec in self:
            best = rec.result_ids.sorted(key=lambda r: r.score, reverse=True)
            rec.top_opportunity = best[0].instrument if best else ''

    def action_run_analysis(self):
        self.ensure_one()
        cfg        = self.env['trading.config'].get_config()
        api_key    = cfg.get('anthropic_api_key', '')
        td_key     = cfg.get('twelve_data_api_key', '')
        serper_key   = cfg.get('serper_api_key', '')
        finnhub_key  = cfg.get('finnhub_api_key', '')

        if not api_key:
            raise UserError("Anthropic API key missing — go to Trading AI → Configuration.")

        instruments = self.instrument_ids
        if not instruments:
            raise UserError(
                "No instruments selected.\n"
                "Add instruments to this session first."
            )

        self.write({'state': 'running', 'run_log': 'Starting daily analysis…\n'})
        self.env.cr.commit()

        utc_now = dt.datetime.utcnow()   # used for weekend checks and NL time display
        log = []

        # ── Step 1: build knowledge base from books (if uploaded) ─────────────
        brain_summary = ''
        if self.book_attachment_ids:
            log.append("📚 Extracting daily trading knowledge from books…")
            self.write({'run_log': '\n'.join(log)})
            self.env.cr.commit()

            pdf_collection = []
            for att in self.book_attachment_ids:
                raw  = base64.b64decode(att.datas or b'')
                name = att.name or ''
                mime = att.mimetype or ''
                if name.lower().endswith('.zip') or 'zip' in mime:
                    try:
                        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                            for pname in sorted(zf.namelist()):
                                if pname.lower().endswith('.pdf') and '__MACOSX' not in pname:
                                    title = re.sub(r'\.pdf$', '', pname.split('/')[-1],
                                                   flags=re.IGNORECASE)
                                    try:
                                        pdf_collection.append((title, zf.read(pname)))
                                    except Exception:
                                        pass
                    except Exception:
                        pass
                elif name.lower().endswith('.pdf'):
                    title = re.sub(r'\.pdf$', '', name, flags=re.IGNORECASE)
                    pdf_collection.append((title, raw))

            if pdf_collection:
                brain_summary = _summarise_books_for_daily(pdf_collection, api_key)
                self.write({'knowledge_summary': brain_summary})
                book_titles = ', '.join(t for t, _ in pdf_collection[:6])
                log.append(
                    f"  ✓ Knowledge base built from {len(pdf_collection)} book(s): {book_titles}"
                )
                log.append(
                    f"  📖 Book knowledge ({len(brain_summary)} chars) will be injected "
                    f"into every instrument prompt as 'DAILY TRADING KNOWLEDGE'."
                )
            else:
                log.append("  ⚠ No PDFs found — proceeding with indicators + news only.")
        else:
            log.append("📚 No session books uploaded — checking Knowledge Library…")

        # ── Step 1b: Load Knowledge Library (category-based books) ────────────
        library_loaded = False
        try:
            lib_model = self.env.get('trading.knowledge.library')
            if lib_model is not None:
                # Library knowledge is per-instrument, loaded during analysis loop below
                log.append("📚 Knowledge Library found — book knowledge will be injected per instrument.")
                library_loaded = True
            elif not brain_summary:
                log.append("📚 No Knowledge Library found — using indicators + news only.")
        except Exception:
            if not brain_summary:
                log.append("📚 Knowledge Library unavailable — using indicators + news only.")

        # ── Step 2: delete old results for this session ───────────────────────
        self.result_ids.unlink()

        # ── Step 3: analyse each instrument (BATCH MODE) ────────────────────
        # Process instruments in batches of 10 to stay within cron timeout.
        # Progress is tracked in ir.config_parameter so each cron tick
        # resumes from where the previous one left off.
        # All 44 instruments complete across multiple ticks (~15 min total).
        BATCH_SIZE     = 10
        PROGRESS_KEY   = f'trading_ai.analysis_progress.{self.id}'
        results        = []
        instrument_list = list(instruments)
        total          = len(instrument_list)

        # Load batch progress
        icp = self.env['ir.config_parameter'].sudo()
        _prog_raw  = icp.get_param(PROGRESS_KEY, '0')
        try:
            batch_start = int(_prog_raw)
        except (ValueError, TypeError):
            batch_start = 0

        batch_end     = min(batch_start + BATCH_SIZE, total)
        batch_slice   = instrument_list[batch_start:batch_end]
        is_last_batch = (batch_end >= total)

        log.append(
            f"📦 Batch {batch_start//BATCH_SIZE + 1}: "
            f"instruments {batch_start+1}–{batch_end} of {total}"
            + (" (FINAL BATCH)" if is_last_batch else " — more batches follow")
        )
        self.write({'run_log': '\n'.join(log)})
        self.env.cr.commit()

        # Override instrument_list to just this batch
        instrument_list = batch_slice

        # ── GMT→NL converter (defined once, reused across all instruments) ─────
        import calendar as _calendar
        def _gmt_str_to_nl(hhmm_gmt):
            if not hhmm_gmt or hhmm_gmt == 'N/A':
                return hhmm_gmt or ''
            try:
                _now = dt.datetime.utcnow()
                def _last_sun(yr, mo):
                    ld = _calendar.monthrange(yr, mo)[1]
                    d  = dt.date(yr, mo, ld)
                    return d - dt.timedelta(days=d.weekday()+1 if d.weekday()!=6 else 0)
                off = 2 if _last_sun(_now.year, 3) <= _now.date() < _last_sun(_now.year, 10) else 1
                tz  = 'CEST' if off == 2 else 'CET'
                clean_t = hhmm_gmt.split()[0]
                h, m = map(int, clean_t.split(':'))
                return f"{(h + off) % 24:02d}:{m:02d} {tz}"
            except Exception:
                return hhmm_gmt

        # ── Pre-fetch ALL news + crypto bars in parallel before TD loop ────────
        # This runs Serper + Binance calls concurrently so the slow TD pacing
        # doesn't add Serper/Binance latency on top.
        _prefetch = {}   # instrument_key -> {'rows': list, 'news': list}

        def _safe_news(key, hours):
            try:
                return key, _fetch_news(serper_key, key, hours=hours, finnhub_key=finnhub_key)
            except Exception:
                return key, []

        def _safe_crypto(key):
            try:
                return key, _fetch_crypto_bars(key)
            except Exception:
                return key, []

        # ── Fetch economic calendar + earnings calendar via Finnhub ────────────
        calendar_events = []
        earnings_events = []
        if finnhub_key:
            calendar_events = _fetch_finnhub_calendar(finnhub_key, hours_ahead=8)
            earnings_events = _fetch_finnhub_earnings(finnhub_key, days_ahead=3)
            if calendar_events:
                high_names = [e['event'] for e in calendar_events if e['impact'] >= 3]
                if high_names:
                    log.append(f"  📅 HIGH-IMPACT events today: {', '.join(high_names[:5])}")
            if earnings_events:
                syms = [e['symbol'] for e in earnings_events]
                log.append(f"  📊 Earnings upcoming: {', '.join(syms)}")
        log.append("⚡ Pre-fetching news + crypto data in parallel…")
        self.write({'run_log': '\n'.join(log)})
        self.env.cr.commit()

        _crypto_keys = [i.instrument_key for i in instrument_list
                        if INSTRUMENT_TYPE.get(i.instrument_key) == 'crypto']
        _stock_keys  = [i.instrument_key for i in instrument_list
                        if INSTRUMENT_TYPE.get(i.instrument_key) in ('stock', 'commodity')]
        _all_keys    = [i.instrument_key for i in instrument_list]

        def _safe_stock_news(key):
            """News for stocks AND commodities via yfinance (free)."""
            try:
                _label_map = {i[0]: i[1] for i in DAILY_INSTRUMENTS}
                _company   = _label_map.get(key, key).split('—')[-1].strip()
                items      = _get_stock_news(key, _company, hours=6)
                # Supplement with Serper if available and few yfinance results
                if serper_key and len(items) < 3:
                    items += _fetch_news(serper_key, key, hours=6, finnhub_key=finnhub_key)
                return key, items
            except Exception:
                return key, []

        def _safe_stock_bars(key):
            try:
                return key, _fetch_stock_bars(key)
            except Exception:
                return key, []

        with ThreadPoolExecutor(max_workers=12) as _pool:
            _nfutures = {
                _pool.submit(
                    _safe_stock_news if INSTRUMENT_TYPE.get(k) in ('stock', 'commodity')
                    else _safe_news,
                    k,
                    *([] if INSTRUMENT_TYPE.get(k) in ('stock', 'commodity')
                      else [6 if INSTRUMENT_TYPE.get(k) == 'crypto' else 12])
                ): k
                for k in _all_keys
            }
            _cfutures = {_pool.submit(_safe_crypto, k): k for k in _crypto_keys}
            _sfutures = {_pool.submit(_safe_stock_bars, k): k for k in _stock_keys}

            for _f in as_completed(_nfutures):
                k, news = _f.result()
                _prefetch.setdefault(k, {})['news'] = news
            for _f in as_completed(_cfutures):
                k, rows = _f.result()
                _prefetch.setdefault(k, {})['rows'] = rows
            for _f in as_completed(_sfutures):
                k, rows = _f.result()
                _prefetch.setdefault(k, {})['rows'] = rows

        log.append(
            f"  ✓ Pre-fetched news for {len(_all_keys)} instruments, "
            f"crypto bars for {len(_crypto_keys)}, "
            f"stock bars for {len(_stock_keys)} instruments."
        )

        # ── Pre-compute trade counts + rule counts (one pass, avoids N×4 queries) ─
        _loss_counts = {}
        _win_counts  = {}
        try:
            TradeLog = self.env['trading.trade_log'].sudo()
            for grp in TradeLog._read_group(
                    [('outcome', '=', 'LOSS')], groupby=['instrument'],
                    aggregates=['__count']):
                _loss_counts[grp[0]] = grp[1]
            for grp in TradeLog._read_group(
                    [('outcome', '=', 'WIN')], groupby=['instrument'],
                    aggregates=['__count']):
                _win_counts[grp[0]] = grp[1]
        except Exception:
            pass

        _global_rule_count = 0
        _inst_rule_counts  = {}
        try:
            _global_rule_count = self.env['trading.ai_rulebook'].sudo().search_count([
                ('rule_type', '=', 'global'), ('active', '=', True)])
            Rulebook = self.env['trading.ai_rulebook'].sudo()
            for grp in Rulebook._read_group(
                    [('rule_type', '=', 'instrument'), ('active', '=', True)],
                    groupby=['instrument'], aggregates=['__count']):
                _inst_rule_counts[grp[0]] = grp[1]
        except Exception:
            pass

        # Inform user about pacing
        forex_count = sum(1 for i in instrument_list
                          if INSTRUMENT_TYPE.get(i.instrument_key) != 'crypto')
        if forex_count > 0:
            log.append(
                f"⏱ {forex_count} forex/index instruments will be fetched with "
                f"automatic 3s spacing to respect Twelve Data free tier. "
                f"Expected time: ~{forex_count * 4 // 60 + 1} min "
                f"(news+crypto already done)."
            )

        self.env.cr.commit()

        for idx, inst_rec in enumerate(instrument_list, 1):
            instrument = inst_rec.instrument_key
            inst_type  = INSTRUMENT_TYPE.get(instrument, 'forex')

            # Crypto bars: use pre-fetched; forex/index: TD sequential; stock: yfinance
            rows = _prefetch.get(instrument, {}).get('rows')
            if rows is None:
                if inst_type in ('stock', 'commodity'):
                    # Stocks & commodities — yfinance (free, no key, parallel-safe)
                    log.append(f"[{idx}/{len(instrument_list)}] Fetching {instrument} (yfinance)…")
                    self.write({'run_log': '\n'.join(log)})
                    self.env.cr.commit()
                    rows = []
                    try:
                        rows = _fetch_stock_bars(instrument)
                    except Exception as e:
                        err_str = str(e)
                        is_true_weekend   = utc_now.weekday() >= 5
                        _current_gmt_hour = utc_now.hour
                        _before_market    = (
                            (inst_type == 'stock'     and _current_gmt_hour < 13) or
                            (inst_type == 'commodity' and _current_gmt_hour < 1)
                        )

                        if is_true_weekend:
                            _sess = SESSION_WINDOWS.get(instrument, {})
                            log.append(f"  📅 {instrument}: weekend — saving NO TRADE")
                            self.env['trading.daily_result'].create({
                                'analysis_id':       self.id,
                                'instrument':        instrument,
                                'inst_type':         inst_type,
                                'signal':            'NO TRADE',
                                'score':             1,
                                'confidence':        'LOW',
                                'current_price':     0,
                                'best_open_time':    _sess.get('open', 'N/A'),
                                'best_close_time':   _sess.get('close', 'N/A'),
                                'best_open_time_nl': _gmt_to_nl(_sess.get('open', 'N/A')),
                                'best_close_time_nl':_gmt_to_nl(_sess.get('close', 'N/A')),
                                'session_advice':    'Market closed — weekend.',
                                'risk_warning':      'Do not trade — market is closed.',
                            })
                            self.env.cr.commit()
                        elif _before_market:
                            _opens = 'stocks open 13:30 GMT' if inst_type == 'stock' else 'commodities open ~01:00 GMT'
                            log.append(f"  ⏰ {instrument}: market not open yet ({_current_gmt_hour:02d}:00 GMT — {_opens})")
                            _sess = SESSION_WINDOWS.get(instrument, {})
                            self.env['trading.daily_result'].create({
                                'analysis_id':       self.id,
                                'instrument':        instrument,
                                'inst_type':         inst_type,
                                'signal':            'HOLD',
                                'score':             1,
                                'confidence':        'LOW',
                                'current_price':     0,
                                'best_open_time':    _sess.get('open', 'N/A'),
                                'best_close_time':   _sess.get('close', 'N/A'),
                                'best_open_time_nl': _gmt_to_nl(_sess.get('open', 'N/A')),
                                'best_close_time_nl':_gmt_to_nl(_sess.get('close', 'N/A')),
                                'session_advice':    f'Market not open yet — {_opens}. Check back at NY Open session.',
                                'risk_warning':      f'Market closed — {_opens}. No analysis available.',
                                'reasoning':         f'Market is closed at {_current_gmt_hour:02d}:00 GMT. {_opens}.',
                            })
                            self.env.cr.commit()
                        else:
                            log.append(f"  ⚠ yfinance fetch failed for {instrument}: {e}")
                        continue
                else:
                    # forex / index — fetch via Twelve Data (must be sequential)
                    log.append(f"[{idx}/{len(instrument_list)}] Fetching {instrument} (Twelve Data)…")
                    self.write({'run_log': '\n'.join(log)})
                    self.env.cr.commit()
                    rows = []
                    try:
                        if td_key:
                            rows = _fetch_forex_bars(instrument, td_key)
                        else:
                            log.append(
                                f"  ⚠ No Twelve Data key — skipping {instrument}. "
                                f"Add key in Configuration (free at twelvedata.com)."
                            )
                    except Exception as e:
                        log.append(f"  ⚠ Price fetch failed for {instrument}: {e}")
            else:
                log.append(f"[{idx}/{len(instrument_list)}] {instrument} (data pre-fetched ⚡)")

            if len(rows) < 20:
                _session = SESSION_WINDOWS.get(instrument, {})
                reason = (
                    "Weekend — ETF/index markets closed (NYSE/NASDAQ closed Sat-Sun)"
                    if utc_now.weekday() >= 5 and inst_type == 'index'
                    else f"Insufficient price data ({len(rows)} bars returned)"
                )
                log.append(f"  ⚠ {reason} — saving NO TRADE for {instrument}")
                self.env['trading.daily_result'].create({
                    'analysis_id':       self.id,
                    'instrument':        instrument,
                    'inst_type':         inst_type,
                    'signal':            'NO TRADE',
                    'score':             1,
                    'confidence':        'LOW',
                    'current_price':     0,
                    'best_open_time':    _session.get('open', 'N/A'),
                    'best_close_time':   _session.get('close', 'N/A'),
                    'best_open_time_nl': _gmt_str_to_nl(_session.get('open', 'N/A')),
                    'best_close_time_nl':_gmt_str_to_nl(_session.get('close', 'N/A')),
                    'session_advice':    reason,
                    'reasoning':         reason,
                    'risk_warning':      'No data — do not trade.',
                })
                self.env.cr.commit()
                continue

            indicators = _compute_indicators(rows, instrument=instrument)
            # Use pre-fetched news (collected in parallel before loop)
            news_items = _prefetch.get(instrument, {}).get('news', [])

            log.append(f"  Analysing with Claude…")
            self.write({'run_log': '\n'.join(log)})
            self.env.cr.commit()

            # Build combined brain summary for this instrument:
            # 1. Uploaded books (global) + 2. Library (per category) + 3. Cortex intelligence
            instrument_brain = brain_summary

            # ── Build AI context audit log for this instrument ──────────────
            context_parts = []

            # 1. Uploaded-book knowledge (global, already in instrument_brain)
            if brain_summary:
                context_parts.append(f"📚 Uploaded books ({len(brain_summary)} chars)")

            # Inject Knowledge Library knowledge for this instrument
            lib_knowledge = ''
            try:
                if library_loaded:
                    lib_knowledge = self.env['trading.knowledge.library'].get_knowledge_for_instrument(
                        instrument, max_chars_per_lib=2000)
                    if lib_knowledge:
                        instrument_brain = (instrument_brain + '\n\n' + lib_knowledge
                                            if instrument_brain else lib_knowledge)
                        context_parts.append(f"📚 Knowledge Library ({len(lib_knowledge)} chars)")
                    else:
                        context_parts.append("📚 Knowledge Library: no matching category found")
            except Exception as lib_e:
                _logger.debug("Library inject skipped for %s: %s", instrument, lib_e)

            # Inject Cortex intelligence for this instrument
            try:
                cortex = self.env.get('trading.cortex')
                if cortex is not None:
                    cortex_rec = cortex.get_singleton()
                    if cortex_rec.total_trades_analysed >= 5:
                        cortex_ctx = cortex_rec.get_cortex_context(instrument)
                        if cortex_ctx:
                            instrument_brain = (instrument_brain + '\n\n' + cortex_ctx
                                                if instrument_brain else cortex_ctx)
                            context_parts.append(
                                f"🧠 Cortex ({cortex_rec.total_trades_analysed} trades, "
                                f"{cortex_rec.state}, {cortex_rec.lesson_count} lessons)"
                            )
                    else:
                        context_parts.append(
                            f"🧠 Cortex: {cortex_rec.total_trades_analysed}/5 trades — "
                            f"still learning, not injected yet"
                        )
            except Exception as ctx_e:
                _logger.debug("Cortex inject skipped for %s: %s", instrument, ctx_e)

            # 4. Past LOSS/WIN trades — use pre-computed counts (no DB query here)
            loss_count = _loss_counts.get(instrument, 0)
            win_count  = _win_counts.get(instrument, 0)
            if loss_count or win_count:
                context_parts.append(
                    f"📉 Past trades: {win_count}W / {loss_count}L "
                    f"({'last 20 losses injected' if loss_count else 'wins only, no losses to inject'})"
                )
            else:
                context_parts.append("📉 Past trades: none recorded yet")

            # 5. AI Rulebook rules — use pre-computed counts
            inst_rules_count = _inst_rule_counts.get(instrument, 0)
            total_rules = _global_rule_count + inst_rules_count
            if total_rules:
                context_parts.append(
                    f"📏 Rules: {_global_rule_count} global + {inst_rules_count} specific"
                )
            else:
                context_parts.append("📏 Rules: none yet")

            # Emit the per-instrument context audit line
            if context_parts:
                log.append(f"  🔍 Context injected → " + " | ".join(context_parts))
            else:
                log.append("  🔍 Context: indicators + news only (no books/history yet)")
            self.write({'run_log': '\n'.join(log)})
            self.env.cr.commit()

            try:
                result = _analyse_instrument(
                    instrument, inst_type, indicators, news_items,
                    instrument_brain, api_key, env=self.env,
                    calendar_events=calendar_events,
                    earnings_events=earnings_events,
                )
            except Exception as _rte:
                _is_overloaded = (
                    isinstance(_rte, RuntimeError) and
                    ('overloaded' in str(_rte).lower() or '529' in str(_rte))
                )
                _label = 'Claude overloaded' if _is_overloaded else f'API error: {_rte}'
                log.append(f"  ⚡ {instrument}: {_label} — saving HOLD, continuing")
                _logger.warning("Instrument %s skipped due to error: %s", instrument, _rte)
                self.env['trading.daily_result'].create({
                    'analysis_id': self.id,
                    'instrument':  instrument,
                    'inst_type':   inst_type,
                    'signal':      'HOLD',
                    'score':       1,
                    'confidence':  'LOW',
                    'current_price': indicators.get('current_price', 0),
                    'reasoning':   f'Skipped: {_label}.',
                    'risk_warning': 'Do not trade — analysis not completed.',
                })
                self.env.cr.commit()
                continue

            # _gmt_str_to_nl defined once before the loop — no re-definition here
            r_open_gmt  = result.get('best_open_time',  '')
            r_close_gmt = result.get('best_close_time', '')
            # Use pre-computed NL if in result, else compute from GMT
            r_open_nl   = result.get('best_open_time_nl')  or _gmt_str_to_nl(r_open_gmt)
            r_close_nl  = result.get('best_close_time_nl') or _gmt_str_to_nl(r_close_gmt)

            # Save result record
            # Calculate real R/R from actual SL/TP distances (don't trust AI's JSON value)
            _entry = result.get('entry_price') or 0
            _sl    = result.get('stop_loss')   or 0
            _tp    = result.get('take_profit')  or 0
            if _entry and _sl and _tp and abs(_entry - _sl) > 0:
                _real_rr = round(abs(_tp - _entry) / abs(_entry - _sl), 2)
            else:
                _real_rr = float(result.get('r_r_ratio', 0) or 0)

            # Define signal/score/conf here so TP check and R/R check can both modify them
            _signal = result.get('signal', 'NO TRADE')
            _score  = int(result.get('score', 1))
            _conf   = result.get('confidence', 'LOW')

            # Enforce minimum TP distance — catch near-zero TPs (META, GOOGL issue)
            _MIN_TP_PCT = {
                'forex': 0.10, 'crypto': 0.30, 'index': 0.30,
                'stock': 0.20, 'commodity': 0.20,
            }
            _min_tp_pct = _MIN_TP_PCT.get(inst_type, 0.15)
            if _entry and _tp and _signal not in ('NO TRADE', 'HOLD'):
                _tp_pct = abs(_tp - _entry) / _entry * 100
                if _tp_pct < _min_tp_pct:
                    _score = min(int(result.get('score', 1)), 3)
                    _conf  = 'LOW'
                    _orig_sig_tp = _signal   # save before overwrite
                    _signal = 'NO TRADE'   # TP too small to be meaningful
                    result['risk_warning'] = (
                        str(result.get('risk_warning', '')) +
                        f" ⚠ TP TOO CLOSE: TP distance = {_tp_pct:.4f}% "
                        f"(minimum {_min_tp_pct}% for {inst_type}). "
                        f"TP is essentially at entry — trade has no viable target. "
                        f"Signal converted to NO TRADE."
                    )
                    _logger.warning("TP too close for %s: %.5f%% on %s signal", instrument, _tp_pct, _orig_sig_tp)

            # ── HARD R/R GATE ─────────────────────────────────────────────────
            # R/R < 1.0 → NO TRADE (no exceptions — mathematically guaranteed loser)
            # R/R 1.0–1.49 → downgrade to score 5 max, confidence LOW
            # R/R >= 1.5 → passes
            if _real_rr > 0 and _signal not in ('NO TRADE', 'HOLD'):
                if _real_rr < 1.0:
                    # Hard gate — convert to NO TRADE
                    _orig_signal = _signal
                    _signal = 'NO TRADE'
                    _score  = min(_score, 3)
                    _conf   = 'LOW'
                    result['risk_warning'] = (
                        str(result.get('risk_warning', '')) +
                        f" ⛔ R/R GATE: Calculated R/R = {_real_rr:.2f} is below 1.0 — "
                        f"mathematically unviable. Signal {_orig_signal} converted to NO TRADE. "
                        f"Widen TP to at least {abs(_entry - _sl) * 1.5 + _entry if 'BUY' in str(_orig_signal) else _entry - abs(_entry - _sl) * 1.5:.5g} "
                        f"for minimum R/R 1.5."
                    )
                    _logger.warning("R/R GATE triggered for %s: %.2f — converted to NO TRADE", instrument, _real_rr)
                elif _real_rr < 1.5:
                    # Soft downgrade
                    _orig_signal = _signal
                    _score  = min(_score, 5)
                    _conf   = 'LOW'
                    result['risk_warning'] = (
                        str(result.get('risk_warning', '')) +
                        f" ⚠ R/R BELOW TARGET: Calculated R/R = {_real_rr:.2f} "
                        f"(target 1.5). Score capped at {_score}. "
                        f"Consider widening TP or tightening SL before trading."
                    )
                    _logger.warning("R/R below target for %s: %.2f", instrument, _real_rr)

            # Enforce SL minimum distance — flag if SL is too tight for instrument type
            # Per-instrument SL minimum — use ATR floor if available
            # Falls back to instrument-type default
            _inst_atr_floor = _ATR_FLOORS.get(instrument, 0)
            _SL_MIN_PCT = {
                'forex':     max(0.50, _inst_atr_floor),   # ~50 pips min on majors
                'crypto':    max(1.00, _inst_atr_floor),   # crypto daily range 2-6%
                'index':     max(0.70, _inst_atr_floor),   # index ETFs
                'stock':     max(1.50, _inst_atr_floor),   # stocks move 1-4%/day
                'commodity': max(1.50, _inst_atr_floor),   # commodities very volatile
            }
            _sl_min_pct = _SL_MIN_PCT.get(inst_type, 0.15)
            if _entry and _sl and _signal not in ('NO TRADE', 'HOLD'):
                _sl_pct = abs(_entry - _sl) / _entry * 100
                if round(_sl_pct, 2) < _sl_min_pct:
                    _score = min(_score, 4)
                    _conf  = 'LOW'
                    # ── AUTO-CORRECT the SL to the minimum safe distance ──────
                    # Don't just warn — actually fix it so the trade is usable
                    _corrected_sl = (_entry - _entry * _sl_min_pct / 100) \
                                    if 'BUY' in _signal \
                                    else (_entry + _entry * _sl_min_pct / 100)
                    _sl_direction = 'BUY: SL below' if 'BUY' in _signal else 'SELL: SL above'
                    _sl_note = (
                        f" ⚠ SL AUTO-CORRECTED: Claude set {_sl_pct:.3f}% "
                        f"(min {_sl_min_pct}% for {inst_type}). "
                        f"SL widened to {_sl_min_pct}% = {_corrected_sl:.5g}. "
                        f"TP also adjusted for 1.5× R/R."
                    )
                    result['risk_warning'] = str(result.get('risk_warning', '')) + _sl_note
                    result['stop_loss']    = round(_corrected_sl, 5)
                    # Recalculate TP for minimum 1.5× R/R from corrected SL
                    _sl_dist = abs(_entry - _corrected_sl)
                    _tp_dist = _sl_dist * 1.5
                    _corrected_tp = (_entry + _tp_dist) if 'BUY' in _signal \
                                    else (_entry - _tp_dist)
                    result['take_profit'] = round(_corrected_tp, 5)
                    _sl = _corrected_sl   # update for R/R recalculation below
                    _logger.warning(
                        "SL auto-corrected for %s: %.4f%% → %.2f%% on %s signal",
                        instrument, _sl_pct, _sl_min_pct, _signal
                    )

            self.env['trading.daily_result'].create({
                'analysis_id':       self.id,
                'instrument':        instrument,
                'inst_type':         inst_type,
                'signal':            _signal,
                'score':             _score,
                'confidence':        _conf,
                'current_price':     indicators.get('current_price', 0),
                'entry_price':       result.get('entry_price'),
                'stop_loss':         result.get('stop_loss'),
                'take_profit':       result.get('take_profit'),
                'r_r_ratio':         _real_rr,
                'hold_overnight_ai': bool(result.get('hold_overnight', False)),
                'best_open_time':    r_open_gmt,
                'best_close_time':   r_close_gmt,
                'best_open_time_nl': r_open_nl,
                'best_close_time_nl':r_close_nl,
                'session_advice':    result.get('session_advice', ''),
                'rsi':               indicators.get('rsi_14'),
                'macd':              indicators.get('macd'),
                'ema_20':            indicators.get('ema_20'),
                'ema_50':            indicators.get('ema_50'),
                'ema_200':           indicators.get('ema_200'),
                'reasoning':         result.get('reasoning', ''),
                'risk_warning':      result.get('risk_warning', ''),
                'raw_response':      json.dumps(result, indent=2),
                'fib_signal':        indicators.get('fib_signal', False),
                'fib_strength':      indicators.get('fib_strength', False),
                'fib_up_1618':       indicators.get('fib_up_1618', 0),
                'fib_dn_1618':       indicators.get('fib_dn_1618', 0),
                'fib_up_2618':       indicators.get('fib_up_2618', 0),
                'fib_dn_2618':       indicators.get('fib_dn_2618', 0),
                'fib_range_bound':   indicators.get('fib_range_bound', False),
                'fib_setup':         indicators.get('fib_setup', ''),
            })
            self.env.cr.commit()  # commit each instrument so timeout preserves partial results
            results.append(result)
            log.append(
                f"  ✓ {instrument}: {result.get('signal','?')} "
                f"(score {result.get('score','?')}/10, {result.get('confidence','?')})"
            )
            self.write({'run_log': '\n'.join(log)})
            self.env.cr.commit()
            # No sleep between instruments — Claude's built-in retry handles 529s

        # ── Step 4: generate ranked briefing ──────────────────────────────────
        try:
            sorted_results = self.result_ids.sorted(key=lambda r: r.score, reverse=True)
            briefing_lines = [
                f"Daily Analysis — {self.analysis_date}",
                "=" * 40,
            ]
            for r in sorted_results:
                bar  = "█" * (r.score or 0) + "░" * (10 - (r.score or 0))
                line = (f"{(r.instrument or '?'):12s} {(r.signal or '?'):12s} [{bar}]"
                        f" {r.score or 0}/10 — {(r.reasoning or '')[:80]}")
                briefing_lines.append(line)

            briefing_lines.append("")
            top = sorted_results[0] if sorted_results else None
            if top and (top.signal or '') not in ('NO TRADE', 'HOLD'):
                briefing_lines.append(
                    f"TOP PICK: {top.instrument} — {top.signal} "
                    f"(score {top.score}/10, {top.confidence or ''} confidence)"
                )
        except Exception as _be:
            _logger.warning("Briefing generation failed (non-fatal): %s", _be, exc_info=True)
            briefing_lines = [
                f"Daily Analysis — {self.analysis_date}",
                "(Briefing generation failed — results still saved)",
            ]

        log.append("✅ Daily analysis complete!")

        # ── Batch completion check ────────────────────────────────────────────
        PROGRESS_KEY = f'trading_ai.analysis_progress.{self.id}'
        icp          = self.env['ir.config_parameter'].sudo()
        batch_start  = int(icp.get_param(PROGRESS_KEY, '0') or '0')

        # Get full instrument list to know total count
        all_instruments = list(self.instrument_ids)
        total            = len(all_instruments)
        BATCH_SIZE       = 10
        batch_end        = min(batch_start + BATCH_SIZE, total)
        is_last_batch    = (batch_end >= total)

        result_count = self.env['trading.daily_result'].search_count(
            [('analysis_id', '=', self.id)])

        if not is_last_batch:
            # Save progress and queue next batch
            icp.set_param(PROGRESS_KEY, str(batch_end))
            log.append(
                f"⏭ Batch done ({result_count}/{total} so far). "
                f"Queuing next batch ({batch_end+1}–{min(batch_end+BATCH_SIZE, total)})…"
            )
            self.write({'state': 'running', 'run_log': '\n'.join(log)})
            self.env.cr.commit()
            # Queue continuation cron on trading.automation
            try:
                old = self.env['ir.cron'].sudo().search(
                    [('name', 'like', 'Trading AI: Batch Continue')], limit=5)
                old.unlink()
                icp.set_param('trading_ai.batch_analysis_id', str(self.id))
                model_id = self.env['ir.model'].sudo()._get_id('trading.automation')
                self.env['ir.cron'].sudo().create({
                    'name':            'Trading AI: Batch Continue',
                    'model_id':        model_id,
                    'state':           'code',
                    'code':            'model.search([], limit=1).cron_continue_batch()',
                    'interval_number': 999,
                    'interval_type':   'days',
                    'active':          True,
                    'nextcall':        fields.Datetime.now(),
                    'priority':        1,
                })
            except Exception as _be:
                _logger.error("Failed to queue next batch: %s", _be)
            return self
        else:
            # All batches done — clear progress
            icp.set_param(PROGRESS_KEY, '0')
            icp.set_param('trading_ai.batch_analysis_id', '0')
            log.append(
                f"✅ ALL BATCHES COMPLETE — {result_count}/{total} instruments analysed."
            )

        self.write({
            'briefing': '\n'.join(briefing_lines),
            'run_log':  '\n'.join(log),
        })
        self.env.cr.commit()  # Ensure state='done' is committed before returning to cron
        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   f'✅ Daily Analysis Complete — {self.analysis_date}',
                'message': (
                    f"Analysed {len(sorted_results)} instruments. "
                    f"Top pick: {self.top_opportunity or 'None'}"
                ),
                'sticky': True, 'type': 'success',
            },
        }

    def action_update_rulebook(self):
        """Manually trigger AI rulebook update from all analysed losses."""
        self.ensure_one()
        cfg     = self.env['trading.config'].get_config()
        api_key = cfg.get('anthropic_api_key', '')
        if not api_key:
            raise UserError("Anthropic API key missing.")
        result = _update_rulebook_from_losses(self.env, api_key)
        ntype  = 'success' if result.get('count', 0) > 0 else 'warning'
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   '🧠 Rulebook Updated',
                'message': result.get('message', 'Done.'),
                'sticky':  True, 'type': ntype,
            },
        }


class DailyInstrument(models.Model):
    """Catalogue of instruments available for daily analysis."""
    _name        = 'trading.daily_instrument'
    _description = 'Daily Analysis Instrument'
    _order       = 'sequence, instrument_key'

    instrument_key = fields.Selection(
        INSTRUMENT_SELECTION, string='Instrument', required=True)
    name = fields.Char(string='Label', compute='_compute_name', store=True)
    inst_type = fields.Selection(
        [('forex', 'Forex'), ('crypto', 'Crypto'), ('index', 'Index'),
         ('stock', 'Stock'), ('commodity', 'Commodity')],
        string='Type', compute='_compute_type', store=True)
    sequence = fields.Integer(default=10)
    active   = fields.Boolean(default=True)

    @api.depends('instrument_key')
    def _compute_name(self):
        label_map = {i[0]: i[1] for i in INSTRUMENT_SELECTION}
        for rec in self:
            rec.name = label_map.get(rec.instrument_key, rec.instrument_key or '')

    @api.depends('instrument_key')
    def _compute_type(self):
        for rec in self:
            rec.inst_type = INSTRUMENT_TYPE.get(rec.instrument_key, 'forex')

    def name_get(self):
        return [(rec.id, rec.name or rec.instrument_key) for rec in self]


class DailyResult(models.Model):
    """Result for a single instrument within a Daily Analysis session."""
    _name        = 'trading.daily_result'
    _description = 'Daily Analysis Result'
    _order       = 'score desc, instrument'

    analysis_id = fields.Many2one(
        'trading.daily_analysis', string='Session',
        ondelete='cascade', required=True)
    analysis_date = fields.Date(
        related='analysis_id.analysis_date', store=True)
    instrument  = fields.Char(string='Instrument', required=True)
    inst_type   = fields.Selection(
        [('forex', 'Forex'), ('crypto', 'Crypto'), ('index', 'Index'),
         ('stock', 'Stock'), ('commodity', 'Commodity')], string='Type')

    signal = fields.Selection([
        ('STRONG BUY',  '⬆⬆ STRONG BUY'),
        ('BUY',         '⬆ BUY'),
        ('HOLD',        '➡ HOLD'),
        ('SELL',        '⬇ SELL'),
        ('STRONG SELL', '⬇⬇ STRONG SELL'),
        ('NO TRADE',    '✗ No Trade'),
    ], string='Signal', required=True, default='NO TRADE')

    score = fields.Integer(
        string='Score (1-10)',
        help='10 = strongest opportunity today, 1 = avoid')
    confidence = fields.Selection(
        [('HIGH','HIGH'), ('MEDIUM','MEDIUM'), ('LOW','LOW')],
        string='Confidence', default='LOW')
    color = fields.Integer(compute='_compute_color', store=True)

    # Price
    current_price = fields.Float(string='Price',       digits=(16, 6))
    entry_price   = fields.Float(string='Entry',       digits=(16, 6))
    stop_loss     = fields.Float(string='Stop Loss',   digits=(16, 6))
    take_profit   = fields.Float(string='Take Profit', digits=(16, 6))
    risk_reward   = fields.Float(
        string='R/R', compute='_compute_rr', store=True)

    # Indicators
    rsi     = fields.Float(string='RSI',    digits=(6, 2))
    macd    = fields.Float(string='MACD',   digits=(16, 8))
    ema_20  = fields.Float(string='EMA 20', digits=(16, 6))
    ema_50  = fields.Float(string='EMA 50', digits=(16, 6))
    ema_200 = fields.Float(string='EMA 200',digits=(16, 6))

    # AI aggressiveness fields
    r_r_ratio        = fields.Float(string='R/R Ratio',
        digits=(8, 2), help='Risk/Reward ratio as recommended by AI')
    hold_overnight_ai = fields.Boolean(string='AI: Hold Overnight',
        help='AI recommends holding this position overnight into the next session')

    # Fibonacci fields
    fib_signal    = fields.Selection([
        ('BULLISH',  '📈 Bullish'),
        ('BEARISH',  '📉 Bearish'),
        ('NEUTRAL',  '➡ Neutral / Range-bound'),
    ], string='Fib Signal', help='Fibonacci retracement signal from first 15-min candle')
    fib_strength  = fields.Selection([
        ('STRONG',   '💪 Strong  — price broke 1.618'),
        ('MODERATE', '⚖ Moderate — between 1.382 and 1.618'),
        ('WEAK',     '🤏 Weak    — near 1st candle range'),
    ], string='Fib Strength')
    fib_up_1618   = fields.Float(string='Fib UP-1.618',  digits=(16, 6))
    fib_dn_1618   = fields.Float(string='Fib DN-1.618',  digits=(16, 6))
    fib_up_2618   = fields.Float(string='Fib UP-2.618 (Target)', digits=(16, 6))
    fib_dn_2618   = fields.Float(string='Fib DN-2.618 (Target)', digits=(16, 6))
    fib_range_bound = fields.Boolean(string='Fib: Range-bound Day')
    fib_setup     = fields.Text(string='Fibonacci Setup', help='Human-readable Fib analysis summary')

    # Timing advice — GMT and Netherlands (CET/CEST)
    best_open_time  = fields.Char(string='Open GMT',
        help='Recommended entry time (GMT)')
    best_close_time = fields.Char(string='Close GMT',
        help='Recommended exit time (GMT)')
    best_open_time_nl  = fields.Char(string='Open NL (CET/CEST)',
        help='Recommended entry time — Netherlands time')
    best_close_time_nl = fields.Char(string='Close NL (CET/CEST)',
        help='Recommended exit time — Netherlands time')
    session_advice  = fields.Char(string='Session Advice')

    reasoning    = fields.Text(string='Reasoning')
    risk_warning = fields.Text(string='Risk Warning')
    raw_response = fields.Text(string='Raw JSON', groups='base.group_system')

    @api.depends('signal', 'score')
    def _compute_color(self):
        color_map = {
            'STRONG BUY': 10, 'BUY': 10,
            'SELL': 1, 'STRONG SELL': 1,
            'HOLD': 2, 'NO TRADE': 0,
        }
        for rec in self:
            rec.color = color_map.get(rec.signal, 0)

    @api.depends('entry_price', 'stop_loss', 'take_profit')
    def _compute_rr(self):
        for rec in self:
            if rec.entry_price and rec.stop_loss and rec.take_profit:
                risk   = abs(rec.entry_price - rec.stop_loss)
                reward = abs(rec.take_profit - rec.entry_price)
                rec.risk_reward = round(reward / risk, 2) if risk > 0 else 0
            else:
                rec.risk_reward = 0

    def action_log_trade(self):
        """Open a pre-filled trade log form linked to this signal result."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Log Trade — {self.instrument}',
            'res_model': 'trading.trade_log',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_instrument':  self.instrument,
                'default_trade_date':  str(self.analysis_date or fields.Date.today()),
                'default_result_id':   self.id,
                'default_entry_price': self.entry_price,
                'default_stop_loss':   self.stop_loss,
                'default_take_profit': self.take_profit,
            },
        }

    def action_open_sim_position(self):
        """
        Open a simulated position from this signal in the paper trading account.
        Fetches live price as entry, sizes position using account's risk % setting.
        """
        self.ensure_one()
        if self.signal in ('NO TRADE', 'HOLD'):
            raise UserError("Cannot simulate a NO TRADE or HOLD signal.")

        cfg    = self.env['trading.config'].get_config()
        td_key = cfg.get('twelve_data_api_key', '')

        # Find or prompt to create a simulator account
        simulator = self.env['trading.simulator'].search(
            [('state', '=', 'active')], limit=1)
        if not simulator:
            raise UserError(
                "No active Paper Trading account found.\n"
                "Go to Daily → Paper Trading → New to create one first."
            )

        # Fetch live entry price
        from .simulator import _get_live_price
        try:
            live_price = _get_live_price(self.instrument,
                                          self.inst_type or 'forex', td_key)
        except Exception as e:
            raise UserError(
                f"Could not fetch live price for {self.instrument}:\n{e}\n\n"
                f"Check your Twelve Data key in Configuration."
            )

        # Direction from signal
        direction = 'BUY' if 'BUY' in self.signal else 'SELL'

        # Actual live entry price (already fetched above)
        entry = live_price

        # Warn if live price deviates significantly from AI suggested entry
        ai_entry = self.entry_price or 0
        if ai_entry and abs(entry - ai_entry) / ai_entry > 0.01:
            slip_pct = abs(entry - ai_entry) / ai_entry * 100
            slip_msg = (
                f"⚠ Price slippage {slip_pct:.2f}%: "
                f"AI suggested {ai_entry:.5g}, live is {entry:.5g}. "
                f"SL/TP recalculated from actual entry."
            )
            _logger.warning(slip_msg)
            self.env['trading.system_log'].log(
                'warning', 'price', slip_msg,
                detail=f"AI entry: {ai_entry:.5g} | Live entry: {entry:.5g} | "
                       f"Diff: {slip_pct:.2f}% | SL/TP will be proportionally adjusted.",
                instrument=self.instrument
            )

        # Recalculate SL/TP relative to actual live entry price
        # NOT the AI's suggested entry — this prevents misaligned SL/TP
        # when live price differs from AI forecast
        ai_sl = self.stop_loss  or 0
        ai_tp = self.take_profit or 0

        if ai_sl > 0 and ai_entry > 0:
            # Preserve the AI's intended SL distance % and apply to real entry
            sl_dist_pct = abs(ai_entry - ai_sl) / ai_entry
            sl = entry * (1 - sl_dist_pct) if direction == 'BUY'                  else entry * (1 + sl_dist_pct)
            sl = round(sl, 6)
        else:
            # Fallback: 1% SL
            sl = entry * 0.99 if direction == 'BUY' else entry * 1.01

        if ai_tp > 0 and ai_entry > 0:
            # Preserve the AI's intended TP distance % and apply to real entry
            tp_dist_pct = abs(ai_tp - ai_entry) / ai_entry
            tp = entry * (1 + tp_dist_pct) if direction == 'BUY'                  else entry * (1 - tp_dist_pct)
            tp = round(tp, 6)
        else:
            # Fallback: 2% TP
            tp = entry * 1.02 if direction == 'BUY' else entry * 0.98

        # Sanity: ensure SL/TP are on correct sides of entry
        if direction == 'BUY':
            if sl >= entry:
                sl = entry * 0.99
            if tp <= entry:
                tp = entry * 1.02
        else:  # SELL
            if sl <= entry:
                sl = entry * 1.01
            if tp >= entry:
                tp = entry * 0.98

        # Position sizing: risk % of current balance
        risk_pct  = simulator.risk_per_trade / 100
        risk_usd  = simulator.current_balance * risk_pct
        sl_dist   = abs(entry - sl)
        # pos_size = risk_usd / sl_distance * entry (notional value)
        pos_size  = round((risk_usd / sl_dist) * entry, 2) if sl_dist > 0 else risk_usd * 10

        pos = self.env['trading.sim_position'].create({
            'simulator_id':    simulator.id,
            'result_id':       self.id,
            'instrument':      self.instrument,
            'inst_type':       self.inst_type or 'forex',
            'direction':       direction,
            'entry_price':     entry,
            'current_price':   entry,
            'stop_loss':       sl,
            'take_profit':     tp,
            'position_size_usd': pos_size,
            'ai_score':        self.score,
            'ai_confidence':   self.confidence,
            'ai_reasoning':    self.reasoning,
        })

        self.env['trading.system_log'].log(
            'success', 'position',
            f"📈 Position opened: {self.instrument} {direction} @ {entry:.5g}",
            detail=f"SL: {sl:.5g} | TP: {tp:.5g} | Size: ${pos_size:,.0f} | "
                   f"AI Score: {self.score}/10 | Confidence: {self.confidence}",
            instrument=self.instrument
        )

        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   f'📈 Sim Position Opened — {self.instrument}',
                'message': (
                    f"{direction} @ {entry:.5g} | "
                    f"SL: {sl:.5g} | TP: {tp:.5g} | "
                    f"Size: ${pos_size:,.0f} | "
                    f"Risk: ${risk_usd:.2f} ({simulator.risk_per_trade}%)"
                ),
                'sticky': True, 'type': 'success',
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# Trade Loss Journal — AI learns from mistakes
# ─────────────────────────────────────────────────────────────────────────────

class TradeLog(models.Model):
    """
    Log every completed trade — win or loss.
    For losses, the AI reads the mistake context before analysing
    that instrument again, helping it avoid repeating the same errors.
    """
    _name        = 'trading.trade_log'
    _description = 'Trade Journal — Loss & Win Log'
    _order       = 'trade_date desc'
    _inherit     = ['mail.thread']

    name = fields.Char(
        string='Trade', compute='_compute_name', store=True)
    trade_date   = fields.Date(
        string='Date', default=fields.Date.today, required=True)
    instrument   = fields.Char(
        string='Instrument', required=True,
        help='Instrument symbol — stored as text to remain compatible across version upgrades.')
    direction    = fields.Selection(
        [('BUY', '⬆ BUY'), ('SELL', '⬇ SELL')],
        string='Direction', required=True)
    outcome      = fields.Selection([
        ('WIN',        '✅ Win'),
        ('LOSS',       '❌ Loss'),
        ('BREAKEVEN',  '➡ Breakeven'),
        ('INVALID',    '⛔ Invalid — Price Feed Error'),
    ], string='Outcome', required=True)

    # Trade levels
    entry_price  = fields.Float(string='Entry Price',  digits=(16, 6))
    exit_price   = fields.Float(string='Exit Price',   digits=(16, 6))
    stop_loss    = fields.Float(string='Stop Loss',    digits=(16, 6))
    take_profit  = fields.Float(string='Take Profit',  digits=(16, 6))
    lot_size     = fields.Float(string='Lot / Position Size', default=0.01)
    pnl          = fields.Float(string='P&L (pips or %)', digits=(10, 2))

    # Mistake analysis (for losses)
    mistake_category = fields.Selection([
        ('wrong_direction',    'Wrong direction — misread trend'),
        ('bad_entry',          'Entry too early / too late'),
        ('sl_too_tight',       'Stop loss too tight — normal noise'),
        ('ignored_news',       'Ignored major news event'),
        ('wrong_session',      'Traded outside optimal session'),
        ('overtraded',         'Overtraded — too many positions'),
        ('chased_trade',       'Chased trade — FOMO entry'),
        ('ignored_indicator',  'Ignored conflicting indicator'),
        ('no_clear_setup',     'No clear setup — should have waited'),
        ('other',              'Other'),
    ], string='Mistake Category')
    what_went_wrong = fields.Text(
        string='What Went Wrong',
        help='Describe in your own words what led to this loss. '
             'The AI will read this before analysing this instrument again.')
    lesson_learned  = fields.Text(
        string='Lesson Learned',
        help='What would you do differently next time?')

    # Link back to the signal that generated the trade
    result_id = fields.Many2one(
        'trading.daily_result', string='Signal Source',
        help='The Daily Analysis result that generated this trade signal.')
    analysis_id = fields.Many2one(
        'trading.daily_analysis', string='Analysis Session',
        related='result_id.analysis_id', store=True)

    # Computed
    risk_reward_actual = fields.Float(
        string='Actual R/R', compute='_compute_actual_rr', store=True)
    color = fields.Integer(compute='_compute_color', store=True)

    @api.depends('trade_date', 'instrument', 'direction', 'outcome')
    def _compute_name(self):
        for rec in self:
            rec.name = (
                f"{rec.trade_date or 'New'} | "
                f"{rec.instrument or '?'} {rec.direction or '?'} — "
                f"{rec.outcome or 'Pending'}"
            )

    @api.depends('entry_price', 'exit_price', 'stop_loss')
    def _compute_actual_rr(self):
        for rec in self:
            if rec.entry_price and rec.exit_price and rec.stop_loss:
                risk   = abs(rec.entry_price - rec.stop_loss)
                reward = abs(rec.exit_price  - rec.entry_price)
                rec.risk_reward_actual = round(reward / risk, 2) if risk > 0 else 0
            else:
                rec.risk_reward_actual = 0

    @api.depends('outcome')
    def _compute_color(self):
        for rec in self:
            rec.color = {'WIN': 10, 'LOSS': 1, 'BREAKEVEN': 2}.get(rec.outcome, 0)

    def action_analyse_loss(self):
        """
        Deep AI analysis of a single loss.
        Claude examines all available data — price levels, indicators,
        original signal reasoning, R/R, session timing — and fills in
        mistake_category, what_went_wrong, and lesson_learned automatically.
        """
        self.ensure_one()
        if self.outcome != 'LOSS':
            raise UserError("AI loss analysis only applies to losing trades.")

        cfg     = self.env['trading.config'].get_config()
        api_key = cfg.get('anthropic_api_key', '')
        if not api_key:
            raise UserError("Anthropic API key missing — check Configuration.")

        # Gather all context we have about this trade
        instrument = self.instrument
        direction  = self.direction
        entry      = self.entry_price
        exit_p     = self.exit_price
        sl         = self.stop_loss
        tp         = self.take_profit
        pnl        = self.pnl
        rr         = self.risk_reward_actual

        # Price movement analysis
        price_moved_pct  = round((exit_p - entry) / entry * 100, 3) if entry else 0
        sl_dist_pct      = round(abs(entry - sl) / entry * 100, 3) if entry else 0
        tp_dist_pct      = round(abs(tp - entry) / entry * 100, 3) if entry else 0
        hit_sl_exactly   = abs(exit_p - sl) < (abs(entry - sl) * 0.01)  # within 1% of SL

        # Get original signal context if available
        signal_ctx = ''
        if self.result_id:
            r = self.result_id
            signal_ctx = f"""
ORIGINAL AI SIGNAL:
  Signal:      {r.signal}
  Score:       {r.score}/10
  Confidence:  {r.confidence}
  Entry:       {r.entry_price}
  Stop Loss:   {r.stop_loss}
  Take Profit: {r.take_profit}
  RSI at entry: {r.rsi}
  MACD at entry: {r.macd}
  EMA 20:      {r.ema_20}
  EMA 50:      {r.ema_50}
  EMA 200:     {r.ema_200}
  Best session: {r.best_open_time}–{r.best_close_time} GMT ({r.session_advice})
  Reasoning:   {r.reasoning}
  Risk warning: {r.risk_warning}"""

        # Look up past losses on this instrument for pattern context
        past_losses = self.env['trading.trade_log'].search([
            ('instrument', '=', instrument),
            ('outcome', '=', 'LOSS'),
            ('id', '!=', self.id),
        ], order='trade_date desc', limit=5)
        past_ctx = ''
        if past_losses:
            past_lines = []
            for p in past_losses:
                past_lines.append(
                    f"  {p.trade_date} | {p.direction} | Entry {p.entry_price} "
                    f"Exit {p.exit_price} | PnL {p.pnl}% | "
                    f"Category: {p.mistake_category or 'unknown'} | "
                    f"{p.what_went_wrong[:100] if p.what_went_wrong else 'N/A'}"
                )
            past_ctx = f"\nPAST LOSSES ON {instrument} (for pattern detection):\n" + \
                       "\n".join(past_lines)

        prompt = f"""You are an expert trading coach analysing a losing trade.
Your job is to diagnose exactly WHY this trade failed and categorise the mistake.

TRADE DATA:
  Instrument:   {instrument}
  Direction:    {direction}
  Entry price:  {entry}
  Exit price:   {exit_p}  ({'SL hit' if hit_sl_exactly else 'manual/other close'})
  Stop loss:    {sl}  ({sl_dist_pct}% from entry)
  Take profit:  {tp}  ({tp_dist_pct}% from entry, never reached)
  P&L:          {pnl}%
  Actual R/R:   {rr}
  Price moved:  {price_moved_pct}% {'against' if (direction=='BUY' and price_moved_pct<0) or (direction=='SELL' and price_moved_pct>0) else 'with'} the trade direction
  Trade date:   {self.trade_date}
{signal_ctx}
{past_ctx}

ANALYSIS REQUIRED:
Based on ALL the above data, determine:

1. PRIMARY MISTAKE — pick exactly ONE from this list:
   wrong_direction    = The trend direction was misread (indicators suggested wrong direction)
   bad_entry          = The direction was right but entry timing was poor (too early/late)
   sl_too_tight       = Stop loss was too close to entry, hit by normal market noise
   ignored_news       = A major news event moved the market against the trade
   wrong_session      = Trade was taken outside the optimal session for this instrument
   overtraded         = Too many concurrent positions, reducing focus/capital
   chased_trade       = Entry was made after the move already happened (FOMO)
   ignored_indicator  = A conflicting indicator was visible but ignored
   no_clear_setup     = No valid setup existed — trade should not have been taken
   other              = None of the above fits

2. DEEP EXPLANATION — explain in 3-5 sentences exactly what the data tells us went wrong.
   Be specific: reference the actual prices, the RSI/MACD values, the R/R, the session.
   Do NOT be vague. Example of GOOD: "The RSI was 68 at entry — already overbought on a BUY.
   The MACD histogram was shrinking, signalling weakening momentum. The SL was only 0.3% away
   from entry while normal volatility for ETH/USDT is 0.8-1.2%, meaning the SL was almost
   certain to be hit by normal price action before the trade could develop."

3. SPECIFIC LESSON — one concrete rule to follow next time (max 2 sentences).

Return ONLY valid JSON:
{{
  "mistake_category": "<one of the exact keys above>",
  "what_went_wrong":  "<3-5 sentence deep explanation referencing actual data>",
  "lesson_learned":   "<one specific actionable rule for next time>"
}}"""

        try:
            data  = _claude_post(api_key, {
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "messages":   [{"role": "user", "content": prompt}]
            })
            raw   = data['content'][0]['text']
            clean = re.sub(r'^```[a-z]*\s*|\s*```$', '', raw.strip())
            result = json.loads(clean)

            cat     = result.get('mistake_category', 'other')
            wwg     = result.get('what_went_wrong', '')
            lesson  = result.get('lesson_learned', '')

            # Validate category is in our selection
            valid_cats = [c[0] for c in self._fields['mistake_category'].selection]
            if cat not in valid_cats:
                cat = 'other'

            self.write({
                'mistake_category': cat,
                'what_went_wrong':  wwg,
                'lesson_learned':   lesson,
            })
            self.message_post(
                body=f"🤖 AI Loss Analysis complete:\n"
                     f"Category: {cat}\n\n"
                     f"What went wrong: {wwg}\n\n"
                     f"Lesson: {lesson}"
            )

            # Trigger rulebook update — AI learns from this loss immediately
            try:
                rb_result = _update_rulebook_from_losses(self.env, api_key)
                rb_msg = rb_result.get('message', '')
            except Exception as rb_e:
                rb_msg = f"(Rulebook update skipped: {rb_e})"

            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {
                    'title':   f'🤖 Loss Analysed — {instrument}',
                    'message': f"Category: {cat.replace('_',' ').title()} | {rb_msg}",
                    'sticky':  False, 'type': 'success',
                },
            }
        except json.JSONDecodeError as e:
            raise UserError(f"AI returned invalid JSON: {e}\nRaw: {raw[:300]}")
        except Exception as e:
            raise UserError(f"AI analysis failed: {e}")

    def action_analyse_all_losses(self):
        """
        Bulk analyse all loss records that still have 'other' or empty category.
        Processes up to 10 at a time with a short delay between calls.
        """
        pending = self.env['trading.trade_log'].search([
            ('outcome', '=', 'LOSS'),
            '|',
            ('mistake_category', '=', 'other'),
            ('mistake_category', '=', False),
        ], limit=10)

        if not pending:
            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {
                    'title':   '✅ All losses already analysed',
                    'message': 'No losses with "other" or empty category found.',
                    'sticky':  False, 'type': 'info',
                },
            }

        errors = []
        done   = 0
        for log in pending:
            try:
                log.action_analyse_loss()
                done += 1
                time.sleep(3)  # avoid Claude rate limiting
            except Exception as e:
                errors.append(f"{log.instrument}: {e}")

        msg = f"Analysed {done}/{len(pending)} losses."
        if errors:
            msg += f" Errors: {'; '.join(errors[:3])}"

        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title':   '🤖 Bulk Loss Analysis Complete',
                'message': msg,
                'sticky':  True, 'type': 'success' if not errors else 'warning',
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# AI Rulebook — Self-Learning Mechanism
# ─────────────────────────────────────────────────────────────────────────────

class AiRulebook(models.Model):
    """
    The AI's persistent memory of learned trading rules.

    Built automatically from analysed losses. Every time Claude analyses
    a loss, patterns compound here. The rulebook is injected into every
    future Daily Analysis so the AI avoids repeating known mistakes.

    Two types of rules:
      - global:     applies to all instruments (e.g. RSI thresholds)
      - instrument: specific to one pair (e.g. ETH min SL distance)
    """
    _name        = 'trading.ai_rulebook'
    _description = 'AI Self-Learning Rulebook'
    _order       = 'confidence desc, instrument'

    name       = fields.Char(string='Rule Name',   required=True)
    rule_type  = fields.Selection([
        ('global',     'Global — all instruments'),
        ('instrument', 'Instrument-specific'),
    ], string='Type', default='global', required=True)
    instrument = fields.Char(
        string='Instrument',
        help='Only set for instrument-specific rules (e.g. EUR/USD, BTC/USDT).')
    category   = fields.Selection([
        ('entry',      'Entry Condition'),
        ('exit',       'Exit / SL Management'),
        ('timing',     'Session / Timing'),
        ('indicator',  'Indicator Signal'),
        ('risk',       'Risk Management'),
        ('filter',     'Trade Filter (when NOT to trade)'),
    ], string='Category', default='filter')
    rule_text  = fields.Text(
        string='Rule Description',
        help='The specific rule Claude extracted from loss patterns.')
    confidence = fields.Integer(
        string='Confidence (1-10)',
        default=5,
        help='How strongly this rule is supported by evidence. '
             'Increases each time the same pattern triggers a loss.')
    times_triggered = fields.Integer(
        string='Times Triggered',
        default=1,
        help='How many losses matched this pattern.')
    last_triggered  = fields.Date(string='Last Triggered')
    active          = fields.Boolean(default=True,
        help='Inactive rules are not injected into analyses.')
    source_losses   = fields.Text(
        string='Source Losses',
        help='IDs of the loss trades that created/updated this rule.')

    def name_get(self):
        for rec in self:
            prefix = f"[{rec.instrument}] " if rec.instrument else "[Global] "
            return [(rec.id, prefix + (rec.name or ''))]
        return []


def _get_learned_rules(env, instrument):
    """
    Fetch active rules from the AI Rulebook relevant to this instrument.
    Returns a formatted block injected into every analysis prompt.
    Combines global rules + instrument-specific rules, sorted by confidence.
    """
    try:
        # Global rules (apply to all)
        global_rules = env['trading.ai_rulebook'].sudo().search([
            ('rule_type', '=', 'global'),
            ('active', '=', True),
            ('confidence', '>=', 3),
        ], order='confidence desc', limit=15)

        # Instrument-specific rules
        inst_rules = env['trading.ai_rulebook'].sudo().search([
            ('rule_type', '=', 'instrument'),
            ('instrument', '=', instrument),
            ('active', '=', True),
        ], order='confidence desc', limit=10)

        if not global_rules and not inst_rules:
            return ''

        lines = ["=== AI LEARNED RULES (from past mistake analysis) ==="]
        lines.append("These rules were extracted from real losses. STRICTLY follow them.")
        lines.append("")

        if inst_rules:
            lines.append(f"── {instrument} specific rules ──")
            for r in inst_rules:
                lines.append(f"  [CONFIDENCE {r.confidence}/10 | triggered {r.times_triggered}x] {r.rule_text}")
            lines.append("")

        if global_rules:
            lines.append("── Global rules (all instruments) ──")
            for r in global_rules:
                lines.append(f"  [CONFIDENCE {r.confidence}/10 | triggered {r.times_triggered}x] {r.rule_text}")

        lines.append("")
        lines.append("CRITICAL: If this trade would violate any rule above with confidence ≥ 7, "
                     "score it NO TRADE (score=1) regardless of indicators.")
        return '\n'.join(lines)
    except Exception:
        return ''


def _update_rulebook_from_losses(env, api_key):
    """
    Core self-learning function. Called after losses are analysed.
    Claude reads ALL analysed losses, finds recurring patterns,
    and updates the AI Rulebook with distilled rules.

    This is what makes the AI smarter over time — each batch of
    losses teaches it a new rule or strengthens an existing one.
    """
    try:
        # Get all losses that have been AI-analysed (have what_went_wrong filled)
        analysed_losses = env['trading.trade_log'].sudo().search([
            ('outcome', '=', 'LOSS'),
            ('what_went_wrong', '!=', False),
            ('what_went_wrong', '!=', ''),
        ], order='trade_date desc', limit=50)

        if len(analysed_losses) < 2:
            return {'count': 0, 'message': 'Need at least 2 analysed losses to learn from.'}

        # Build loss summary for Claude
        loss_lines = []
        for lg in analysed_losses:
            loss_lines.append(
                f"LOSS: {lg.trade_date} | {lg.instrument} | {lg.direction} | "
                f"Entry {lg.entry_price:.5g} SL {lg.stop_loss:.5g} TP {lg.take_profit:.5g} | "
                f"PnL {lg.pnl:.3f}% | "
                f"Category: {lg.mistake_category or 'unknown'} | "
                f"What went wrong: {(lg.what_went_wrong or '')[:200]} | "
                f"Lesson: {(lg.lesson_learned or '')[:100]}"
            )
        loss_summary = "\n".join(loss_lines)

        # Get existing rules so Claude can update/strengthen them
        existing_rules = env['trading.ai_rulebook'].sudo().search(
            [('active', '=', True)], order='confidence desc', limit=30)
        existing_block = ""
        if existing_rules:
            existing_lines = []
            for r in existing_rules:
                inst = r.instrument or 'GLOBAL'
                existing_lines.append(
                    f"  ID:{r.id} | [{inst}] [{r.category}] "
                    f"confidence:{r.confidence} triggered:{r.times_triggered}x | "
                    f"{r.rule_text}"
                )
            existing_block = (
                "EXISTING RULES IN RULEBOOK (update confidence if pattern repeats, "
                "don't duplicate):\n" + "\n".join(existing_lines)
            )

        prompt = f"""You are a systematic trading coach building a self-learning rule system.

Analyse these {len(analysed_losses)} real trading losses and extract/update trading rules.

{existing_block}

ANALYSED LOSSES:
{loss_summary}

Your task:
1. Find PATTERNS across multiple losses (not one-off events)
2. Extract clear, actionable rules that would have PREVENTED these losses
3. For EXISTING rules that appear again: increase confidence (max 10), update times_triggered
4. For NEW patterns: create new rules

Return ONLY a JSON array of rule objects. Each object:
{{
  "action": "create" | "update",
  "id": <integer, only for "update" — the existing rule ID>,
  "name": "<short rule title, max 10 words>",
  "rule_type": "global" | "instrument",
  "instrument": "<instrument key or null>",
  "category": "entry" | "exit" | "timing" | "indicator" | "risk" | "filter",
  "rule_text": "<specific actionable rule, 1-3 sentences, reference actual numbers>",
  "confidence": <integer 1-10>,
  "times_triggered": <integer — how many losses in this batch match this pattern>
}}

Rules MUST be:
- Specific (reference actual numbers: RSI > 70, SL < 0.5%, etc.)
- Actionable (clear YES/NO decision)
- Based on REPEATED patterns (≥2 losses), not single events
- Not duplicating existing rules (update confidence instead)

Maximum 10 rules total (create + update combined).
Return ONLY the JSON array, nothing else."""

        data  = _claude_post(api_key, {
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 1500,
            "messages":   [{"role": "user", "content": prompt}]
        })
        raw   = data['content'][0]['text']
        clean = re.sub(r'^```[a-z]*\s*|\s*```$', '', raw.strip())
        rules = json.loads(clean)

        if not isinstance(rules, list):
            return {'count': 0, 'message': f'Unexpected response format.'}

        created = 0
        updated = 0
        today   = dt.date.today()
        loss_ids = ','.join(str(l.id) for l in analysed_losses)

        for rule in rules:
            action = rule.get('action', 'create')

            if action == 'update' and rule.get('id'):
                existing = env['trading.ai_rulebook'].sudo().browse(rule['id'])
                if existing.exists():
                    existing.sudo().write({
                        'confidence':      min(10, rule.get('confidence', existing.confidence)),
                        'times_triggered': rule.get('times_triggered', existing.times_triggered),
                        'last_triggered':  today,
                        'rule_text':       rule.get('rule_text', existing.rule_text),
                    })
                    updated += 1
                    continue

            # Create new rule
            inst = rule.get('instrument')
            # Validate instrument is in our selection
            valid_instruments = [i[0] for i in INSTRUMENT_SELECTION]
            if inst and inst not in valid_instruments:
                inst = None
                rule['rule_type'] = 'global'

            env['trading.ai_rulebook'].sudo().create({
                'name':           rule.get('name', 'Unnamed rule')[:100],
                'rule_type':      rule.get('rule_type', 'global'),
                'instrument':     inst,
                'category':       rule.get('category', 'filter'),
                'rule_text':      rule.get('rule_text', ''),
                'confidence':     max(1, min(10, int(rule.get('confidence', 5)))),
                'times_triggered': int(rule.get('times_triggered', 1)),
                'last_triggered': today,
                'source_losses':  loss_ids,
            })
            created += 1

        return {
            'count':   created + updated,
            'created': created,
            'updated': updated,
            'message': f"Rulebook updated: {created} new rules, {updated} strengthened.",
        }

    except json.JSONDecodeError as e:
        _logger.error("Rulebook update JSON error: %s | raw: %s", e, raw[:300])
        return {'count': 0, 'message': f'JSON parse error: {e}'}
    except Exception as e:
        _logger.error("Rulebook update failed: %s", e)
        return {'count': 0, 'message': f'Error: {e}'}
