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

def _compute_indicators(rows):
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
    return {
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


# ─────────────────────────────────────────────────────────────────────────────
# News fetch
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_news(serper_key, instrument, hours=12):
    if not serper_key:
        return []
    query = instrument.split('/')[0] + ' forex' if '/' in instrument and 'USDT' not in instrument \
            else instrument.split('/')[0] + ' crypto price'
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
                {'title': i.get('title',''), 'snippet': i.get('snippet','')}
                for i in data.get('news', [])[:5]
            ]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Claude API
# ─────────────────────────────────────────────────────────────────────────────

def _claude_post(api_key, payload, timeout=60, max_retries=5):
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
                _logger.warning("Claude %s (attempt %d/%d) waiting %ds…",
                                e.code, attempt, max_retries, wait)
                time.sleep(wait)
                delay = min(delay * 2, 120)
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
                         brain_summary, api_key, env=None):
    """
    Ask Claude to score ONE instrument for daily trading.
    Includes session timing advice and injects past loss history if available.
    Returns a dict with signal, score, confidence, reasoning,
    entry/sl/tp, best_open_time, best_close_time.
    """
    ind_block    = "\n".join(f"  {k}: {v}" for k, v in indicators.items())
    news_block   = "\n".join(f"• {n['title']} — {n['snippet']}" for n in news_items) \
                   or "No recent news."
    brain_cap    = (brain_summary or '')[:3000]
    mkt_type     = "index" if instrument_type == 'index' else \
                   ("forex/commodity" if instrument_type == 'forex' else "cryptocurrency")

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

    system = f"""You are a daily trading analyst specialising in {mkt_type}.
Score the {instrument} opportunity for TODAY and return ONLY valid JSON:
{{
  "signal":          "STRONG BUY"|"BUY"|"HOLD"|"SELL"|"STRONG SELL"|"NO TRADE",
  "score":           <integer 1-10, 10=strongest opportunity>,
  "confidence":      "HIGH"|"MEDIUM"|"LOW",
  "entry_price":     <float|null>,
  "stop_loss":       <float|null>,
  "take_profit":     <float|null>,
  "best_open_time":  "<HH:MM GMT>",
  "best_close_time": "<HH:MM GMT>",
  "session_advice":  "<one sentence about timing today>",
  "reasoning":       "<2-3 sentences on the setup>",
  "risk_warning":    "<one sentence>"
}}
Scoring: 8-10=strong, 5-7=moderate, 1-4=weak/avoid. NO TRADE if signals conflict.
For timing: return times in GMT only (HH:MM format). The trader is in the Netherlands.
Optimal session for {instrument}: {session_name} ({session_open} GMT / {session_open_nl})."""

    user_parts = []
    if brain_cap:
        user_parts.append(f"=== DAILY TRADING KNOWLEDGE ===\n{brain_cap}")
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
            "max_tokens": 500,
            "system":     system,
            "messages":   [{"role": "user", "content": user}]
        })
        raw   = data['content'][0]['text']
        clean = re.sub(r'^```[a-z]*\s*|\s*```$', '', raw.strip())
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            t = clean.strip()
            if not t.endswith('}'): t += '"}'
            try:
                return json.loads(t)
            except Exception:
                pass
        sig_m = re.search(r'"signal"\s*:\s*"([^"]+)"', clean)
        return {
            "signal": sig_m.group(1) if sig_m else "NO TRADE",
            "score": 1, "confidence": "LOW",
            "best_open_time":  session_open,
            "best_close_time": session_close,
            "best_open_time_nl":  session_open_nl,
            "best_close_time_nl": session_close_nl,
            "session_advice": f"Trade during {session_name}.",
            "reasoning": "Response parsing failed — retry.",
            "risk_warning": "Do not trade on this signal.",
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
    """Summarise uploaded books into a compact daily-trading knowledge base."""
    summaries = []
    for title, pdf_bytes in pdf_collection[:6]:    # cap at 6 books for speed
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(pdf_bytes))
            chunks, total = [], 0
            for page in reader.pages:
                t = page.extract_text() or ''
                chunks.append(t); total += len(t)
                if total >= 6000: break
            text = '\n'.join(chunks)[:6000]
        except Exception:
            continue
        if len(text) < 200:
            continue
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
            summaries.append(f"[{title}]\n{data['content'][0]['text']}")
        except Exception:
            pass
        time.sleep(5)

    if not summaries:
        return ""
    # Merge
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
        serper_key = cfg.get('serper_api_key', '')

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
                log.append(f"  ✓ Knowledge base built from {len(pdf_collection)} books.")
            else:
                log.append("  ⚠ No PDFs found — proceeding with indicators + news only.")
        else:
            log.append("📚 No books uploaded — using indicators + news only.")

        # ── Step 2: delete old results for this session ───────────────────────
        self.result_ids.unlink()

        # ── Step 3: analyse each instrument ───────────────────────────────────
        results = []
        instrument_list = list(instruments)

        # Inform user about pacing
        forex_count = sum(1 for i in instrument_list
                          if INSTRUMENT_TYPE.get(i.instrument_key) != 'crypto')
        if forex_count > 0:
            log.append(
                f"⏱ {forex_count} forex/index instruments will be fetched with "
                f"automatic 8s spacing to respect Twelve Data free tier (8 calls/min). "
                f"Expected data fetch time: ~{forex_count * 9 // 60 + 1} min."
            )

        self.env.cr.commit()

        for idx, inst_rec in enumerate(instrument_list, 1):
            instrument    = inst_rec.instrument_key
            inst_type     = INSTRUMENT_TYPE.get(instrument, 'forex')
            pace_note     = '' if inst_type == 'crypto' else ' (paced)'
            log.append(f"[{idx}/{len(instrument_list)}] Fetching {instrument}{pace_note}…")
            self.write({'run_log': '\n'.join(log)})
            self.env.cr.commit()

            # Fetch price bars
            rows = []
            try:
                if inst_type == 'crypto':
                    rows = _fetch_crypto_bars(instrument)
                else:
                    # forex, index, commodity — all via Twelve Data
                    if td_key:
                        rows = _fetch_forex_bars(instrument, td_key)
                    else:
                        log.append(
                            f"  ⚠ No Twelve Data key — skipping {instrument}. "
                            f"Add key in Configuration (free at twelvedata.com)."
                        )
            except Exception as e:
                log.append(f"  ⚠ Price fetch failed: {e}")

            if len(rows) < 20:
                log.append(f"  ⚠ Insufficient data ({len(rows)} bars) — skipping {instrument}.")
                continue

            indicators = _compute_indicators(rows)
            news_items = _fetch_news(serper_key, instrument)

            log.append(f"  Analysing with Claude…")
            self.write({'run_log': '\n'.join(log)})
            self.env.cr.commit()

            result = _analyse_instrument(
                instrument, inst_type, indicators, news_items,
                brain_summary, api_key, env=self.env
            )

            # Compute NL times from Claude's GMT response
            def _gmt_str_to_nl(hhmm_gmt):
                if not hhmm_gmt or hhmm_gmt == 'N/A':
                    return hhmm_gmt or ''
                try:
                    import calendar as _cal
                    _now = dt.datetime.utcnow()
                    def _last_sun(yr, mo):
                        ld = _cal.monthrange(yr, mo)[1]
                        d  = dt.date(yr, mo, ld)
                        return d - dt.timedelta(days=d.weekday()+1 if d.weekday()!=6 else 0)
                    dst_s = _last_sun(_now.year, 3)
                    dst_e = _last_sun(_now.year, 10)
                    off   = 2 if dst_s <= _now.date() < dst_e else 1
                    tz    = 'CEST' if off == 2 else 'CET'
                    # Strip any trailing GMT/UTC label Claude may have added
                    clean_t = hhmm_gmt.split()[0]
                    h, m  = map(int, clean_t.split(':'))
                    nlh   = (h + off) % 24
                    return f"{nlh:02d}:{m:02d} {tz}"
                except Exception:
                    return hhmm_gmt

            r_open_gmt  = result.get('best_open_time',  '')
            r_close_gmt = result.get('best_close_time', '')
            # Use pre-computed NL if in result, else compute from GMT
            r_open_nl   = result.get('best_open_time_nl')  or _gmt_str_to_nl(r_open_gmt)
            r_close_nl  = result.get('best_close_time_nl') or _gmt_str_to_nl(r_close_gmt)

            # Save result record
            self.env['trading.daily_result'].create({
                'analysis_id':       self.id,
                'instrument':        instrument,
                'inst_type':         inst_type,
                'signal':            result.get('signal', 'NO TRADE'),
                'score':             int(result.get('score', 1)),
                'confidence':        result.get('confidence', 'LOW'),
                'current_price':     indicators.get('current_price', 0),
                'entry_price':       result.get('entry_price'),
                'stop_loss':         result.get('stop_loss'),
                'take_profit':       result.get('take_profit'),
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
            })
            results.append(result)
            log.append(
                f"  ✓ {instrument}: {result.get('signal','?')} "
                f"(score {result.get('score','?')}/10, {result.get('confidence','?')})"
            )
            self.write({'run_log': '\n'.join(log)})
            self.env.cr.commit()

            # Pause between Claude calls to avoid rate limiting
            if idx < len(instrument_list):
                time.sleep(3)

        # ── Step 4: generate ranked briefing ──────────────────────────────────
        sorted_results = self.result_ids.sorted(key=lambda r: r.score, reverse=True)
        briefing_lines = [
            f"Daily Analysis — {self.analysis_date}",
            "=" * 40,
        ]
        for r in sorted_results:
            bar   = "█" * r.score + "░" * (10 - r.score)
            line  = f"{r.instrument:12s} {r.signal:12s} [{bar}] {r.score}/10 — {r.reasoning[:80]}"
            briefing_lines.append(line)

        briefing_lines.append("")
        top = sorted_results[0] if sorted_results else None
        if top and top.signal not in ('NO TRADE', 'HOLD'):
            briefing_lines.append(
                f"TOP PICK: {top.instrument} — {top.signal} "
                f"(score {top.score}/10, {top.confidence} confidence)"
            )

        log.append("✅ Daily analysis complete!")

        self.write({
            'state':   'done',
            'briefing': '\n'.join(briefing_lines),
            'run_log':  '\n'.join(log),
        })
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
        [('forex', 'Forex'), ('crypto', 'Crypto'), ('index', 'Index')],
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
        [('forex', 'Forex'), ('crypto', 'Crypto'), ('index', 'Index')], string='Type')

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

        # Use AI's SL/TP if available, else calculate from ATR-style 1% rule
        entry  = live_price
        sl     = self.stop_loss  or 0
        tp     = self.take_profit or 0
        if sl == 0:
            sl = entry * 0.99 if direction == 'BUY' else entry * 1.01
        if tp == 0:
            tp = entry * 1.02 if direction == 'BUY' else entry * 0.98

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
    instrument   = fields.Selection(
        INSTRUMENT_SELECTION, string='Instrument', required=True)
    direction    = fields.Selection(
        [('BUY', '⬆ BUY'), ('SELL', '⬇ SELL')],
        string='Direction', required=True)
    outcome      = fields.Selection([
        ('WIN',        '✅ Win'),
        ('LOSS',       '❌ Loss'),
        ('BREAKEVEN',  '➡ Breakeven'),
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
    instrument = fields.Selection(
        INSTRUMENT_SELECTION, string='Instrument',
        help='Only set for instrument-specific rules.')
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
