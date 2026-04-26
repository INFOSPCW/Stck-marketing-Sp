# -*- coding: utf-8 -*-
"""
crypto_brain.py
===============
The "brain" of the Crypto Trading Advisor.

Architecture mirrors trading_brain.py (forex) but is tuned for crypto:
  - 24/7 markets (no session gaps)
  - Binance public API for live data (no key needed)
  - CryptoDataDownload CSV format parser
  - Crypto-aware Claude prompting (halving cycles, on-chain narrative, BTC dominance)
  - Top 10 crypto pairs: BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, XRP/USDT,
                         ADA/USDT, DOGE/USDT, AVAX/USDT, LTC/USDT, MATIC/USDT
"""

import re
import io
import csv
import json
import time
import base64
import zipfile
import logging
import math
import urllib.request
from urllib.error import HTTPError

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

CRYPTO_PAIRS = [
    ('BTC/USDT',   'BTC/USDT  — Bitcoin'),
    ('ETH/USDT',   'ETH/USDT  — Ethereum'),
    ('BNB/USDT',   'BNB/USDT  — BNB'),
    ('SOL/USDT',   'SOL/USDT  — Solana'),
    ('XRP/USDT',   'XRP/USDT  — Ripple'),
    ('ADA/USDT',   'ADA/USDT  — Cardano'),
    ('DOGE/USDT',  'DOGE/USDT — Dogecoin'),
    ('AVAX/USDT',  'AVAX/USDT — Avalanche'),
    ('LTC/USDT',   'LTC/USDT  — Litecoin'),
    ('MATIC/USDT', 'MATIC/USDT — Polygon'),
]

# ─────────────────────────────────────────────────────────────────────────────
# CCXT — unified crypto exchange data fetch
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_EXCHANGES = [
    ('binance',  'Binance'),
    ('bybit',    'Bybit'),
    ('kraken',   'Kraken'),
    ('okx',      'OKX'),
    ('kucoin',   'KuCoin'),
    ('gate',     'Gate.io'),
    ('coinbase', 'Coinbase'),
]

# Some pairs are named differently on specific exchanges
_EXCHANGE_PAIR_OVERRIDES = {
    'kraken':   {'BTC/USDT': 'BTC/USDT', 'MATIC/USDT': 'MATIC/USDT'},
    'coinbase': {p[0]: p[0].replace('USDT', 'USD') for p in CRYPTO_PAIRS},
}


def _get_exchange_pair(exchange_id, pair):
    """Return the correct symbol string for a given exchange."""
    overrides = _EXCHANGE_PAIR_OVERRIDES.get(exchange_id, {})
    return overrides.get(pair, pair)


def _fetch_ccxt_ohlcv(pair, exchange_id='binance', days=30):
    """
    Fetch 1-min OHLCV bars via CCXT for the given pair and exchange.

    Paginates automatically — fetches `days` worth of 1-min bars.
    1 day  =  1,440 bars
    30 days = 43,200 bars  (approx, markets have gaps)
    365 days = ~525,000 bars

    Returns (rows, csv_bytes) where rows = [(dt_str, o, h, l, c, vol), ...]
    """
    try:
        import ccxt
    except ImportError:
        raise RuntimeError(
            "CCXT library not installed.\n"
            "Run this on Odoo.sh shell:\n"
            "  pip install ccxt --break-system-packages"
        )

    import datetime as dt

    # Initialise exchange — no API key needed for public OHLCV
    try:
        exchange_class = getattr(ccxt, exchange_id)
    except AttributeError:
        raise RuntimeError(f"Unknown exchange: {exchange_id}")

    exchange = exchange_class({'enableRateLimit': True})

    if not exchange.has.get('fetchOHLCV'):
        raise RuntimeError(f"{exchange_id} does not support OHLCV fetching.")

    symbol = _get_exchange_pair(exchange_id, pair)

    # Calculate start timestamp (ms)
    since_dt  = dt.datetime.utcnow() - dt.timedelta(days=days)
    since_ms  = int(since_dt.timestamp() * 1000)
    now_ms    = int(dt.datetime.utcnow().timestamp() * 1000)
    timeframe = '1m'
    limit     = 1000   # max bars per request for most exchanges

    all_bars = []
    current_since = since_ms
    request_count = 0
    max_requests  = 600   # safety cap — 600 * 1000 = 600,000 bars = ~416 days

    _logger.info("CCXT fetch: %s %s from %s (%d days)",
                 exchange_id, symbol, since_dt.strftime('%Y-%m-%d'), days)

    while current_since < now_ms and request_count < max_requests:
        try:
            bars = exchange.fetch_ohlcv(symbol, timeframe,
                                         since=current_since, limit=limit)
        except ccxt.BadSymbol:
            raise RuntimeError(
                f"Pair {symbol} not available on {exchange_id}.\n"
                f"Try a different exchange or pair."
            )
        except ccxt.NetworkError as e:
            raise RuntimeError(f"Network error fetching from {exchange_id}: {e}")
        except Exception as e:
            raise RuntimeError(f"CCXT error ({exchange_id}): {e}")

        if not bars:
            break

        all_bars.extend(bars)
        last_ts = bars[-1][0]

        # If the exchange returned fewer bars than requested, we've reached the end
        if len(bars) < limit:
            break

        current_since = last_ts + 60_000  # advance by 1 minute
        request_count += 1

    if not all_bars:
        raise RuntimeError(
            f"No bars returned for {symbol} on {exchange_id}.\n"
            f"The pair may not be listed, or the exchange may be unavailable."
        )

    # Deduplicate and convert to our standard tuple format
    seen = set()
    rows = []
    for bar in sorted(all_bars, key=lambda b: b[0]):
        ts  = int(bar[0]) // 1000
        dt_str = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        if dt_str in seen:
            continue
        seen.add(dt_str)
        try:
            o, h, l, c, vol = (float(bar[1]), float(bar[2]),
                                float(bar[3]), float(bar[4]),
                                float(bar[5]) if bar[5] else 0.0)
            rows.append((dt_str, o, h, l, c, vol))
        except (TypeError, ValueError):
            continue

    # Build CSV bytes (same format as our parser expects)
    csv_lines = [f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}" for r in rows]
    csv_bytes = "\n".join(csv_lines).encode('utf-8')

    _logger.info("CCXT fetch complete: %d bars, %d requests", len(rows), request_count)
    return rows, csv_bytes


# ─────────────────────────────────────────────────────────────────────────────
# CSV parsing  — handles both CryptoDataDownload and Binance formats
# ─────────────────────────────────────────────────────────────────────────────

def _parse_crypto_csv(text_content):
    """
    Parse crypto OHLC CSV into (dt_str, open, high, low, close, volume).

    Handles three formats:

    Format A — CCXT output (our own generated CSVs):
        2026-03-15T09:09:00,71867.03,71920.0,71860.0,71919.6,27.53
        → parts[0] is ISO datetime, 6 fields, OHLCV at parts[1-5]

    Format B — CryptoDataDownload:
        unix,date,symbol,open,high,low,close,Volume BTC,Volume USDT
        1609459200,2021-01-01 00:00:00,BTCUSDT,29374.15,...
        → parts[1] is datetime string, 8+ fields, OHLCV at parts[3-6]

    Format C — Binance unix timestamp:
        1609459200000,29374.15,29600.0,29200.0,29374.15,123.45
        → parts[0] is integer unix ms/s, OHLCV at parts[1-5]
    """
    import datetime as _dt
    rows = []
    for line in text_content.splitlines():
        line = line.strip()
        if not line or line.lower().startswith(('unix', 'date', 'timestamp', '#')):
            continue
        parts = re.split(r',', line)
        if len(parts) < 5:
            continue
        try:
            p0 = parts[0]

            # Format A — CCXT: ISO datetime in parts[0] e.g. "2026-03-15T09:09:00"
            if 'T' in p0 or (len(p0) >= 10 and p0[4] == '-' and p0[7] == '-'):
                dt_str = p0.replace(' ', 'T')
                o   = float(parts[1])
                h   = float(parts[2])
                l   = float(parts[3])
                c   = float(parts[4])
                vol = float(parts[5]) if len(parts) > 5 else 0.0

            # Format B — CryptoDataDownload: unix in parts[0], datetime in parts[1]
            elif len(parts) >= 7 and '-' in parts[1] and ':' in parts[1]:
                dt_str = parts[1].replace(' ', 'T')
                o   = float(parts[3])
                h   = float(parts[4])
                l   = float(parts[5])
                c   = float(parts[6])
                vol = float(parts[7]) if len(parts) > 7 else 0.0

            # Format C — pure unix timestamp (ms or s)
            else:
                ts = int(float(p0))
                if ts > 1_000_000_000_000:   # milliseconds → seconds
                    ts //= 1000
                dt_str = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
                o   = float(parts[1])
                h   = float(parts[2])
                l   = float(parts[3])
                c   = float(parts[4])
                vol = float(parts[5]) if len(parts) > 5 else 0.0

            rows.append((dt_str, o, h, l, c, vol))
        except (ValueError, IndexError):
            continue
    return rows


def _collect_ohlc_from_attachments(attachments):
    """Read all crypto price attachments (CSV or ZIP). Returns sorted, deduped rows."""
    all_rows = []

    def _read_inner_zip(zb, name):
        res = []
        try:
            with zipfile.ZipFile(io.BytesIO(zb)) as iz:
                csvs = [n for n in iz.namelist() if n.lower().endswith('.csv')]
                for fn in csvs:
                    content = iz.read(fn).decode('utf-8', errors='ignore')
                    res.extend(_parse_crypto_csv(content))
        except Exception as e:
            _logger.warning("Inner zip %s: %s", name, e)
        return res

    for att in attachments:
        raw  = base64.b64decode(att.datas or b'')
        name = (att.name or '').lower()
        mime = att.mimetype or ''

        if name.endswith('.zip') or 'zip' in mime:
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    for entry in zf.namelist():
                        if entry.endswith('/') or '__MACOSX' in entry:
                            continue
                        if entry.lower().endswith('.zip'):
                            all_rows.extend(_read_inner_zip(zf.read(entry), entry))
                        elif entry.lower().endswith('.csv'):
                            content = zf.read(entry).decode('utf-8', errors='ignore')
                            all_rows.extend(_parse_crypto_csv(content))
            except Exception as e:
                _logger.warning("ZIP attachment '%s': %s", att.name, e)
        elif name.endswith('.csv'):
            all_rows.extend(_parse_crypto_csv(raw.decode('utf-8', errors='ignore')))

    all_rows.sort(key=lambda r: r[0])
    seen, deduped = set(), []
    for r in all_rows:
        if r[0] not in seen:
            seen.add(r[0])
            deduped.append(r)
    return deduped


# ─────────────────────────────────────────────────────────────────────────────
# Technical indicators  (same pure-Python implementation as forex brain)
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
    if len(gains) < period:
        return 50.0
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))

def _macd(closes):
    if len(closes) < 26:
        return 0, 0
    e12 = _ema(closes, 12); e26 = _ema(closes, 26)
    line = [a - b for a, b in zip(e12, e26)]
    return line[-1], _ema(line, 9)[-1]

def _bollinger(closes, period=20):
    if len(closes) < period:
        return closes[-1], closes[-1], closes[-1]
    w = closes[-period:]; mid = sum(w) / period
    std = math.sqrt(sum((x - mid) ** 2 for x in w) / period)
    return mid + 2 * std, mid, mid - 2 * std

def _compute_indicators(rows, lookback_hours=48):
    max_rows = lookback_hours * 60
    rows = rows[-max_rows:] if len(rows) > max_rows else rows
    if not rows:
        return {}
    closes = [r[4] for r in rows]
    highs  = [r[2] for r in rows]
    lows   = [r[3] for r in rows]
    opens  = [r[1] for r in rows]
    vols   = [r[5] for r in rows]

    rsi_val          = _rsi(closes)
    macd_val, sig    = _macd(closes)
    bb_u, bb_m, bb_l = _bollinger(closes)
    ema20  = _ema(closes, 20)[-1]  if len(closes) >= 20  else closes[-1]
    ema50  = _ema(closes, 50)[-1]  if len(closes) >= 50  else closes[-1]
    ema200 = _ema(closes, 200)[-1] if len(closes) >= 200 else closes[-1]
    current = closes[-1]
    h24 = max(highs[-1440:])  if len(highs)  >= 1440 else max(highs)
    l24 = min(lows[-1440:])   if len(lows)   >= 1440 else min(lows)
    avg_vol = sum(vols[-60:]) / 60 if len(vols) >= 60 else (sum(vols) / len(vols) if vols else 0)
    slope = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0

    return {
        'current_price':         round(current, 6),
        'open_period':           round(opens[0], 6),
        'high_24h':              round(h24, 6),
        'low_24h':               round(l24, 6),
        'price_change_pct':      round((current - opens[0]) / opens[0] * 100, 4),
        'rsi_14':                round(rsi_val, 2),
        'macd':                  round(macd_val, 8),
        'macd_signal':           round(sig, 8),
        'macd_histogram':        round(macd_val - sig, 8),
        'ema_20':                round(ema20, 6),
        'ema_50':                round(ema50, 6),
        'ema_200':               round(ema200, 6),
        'bb_upper':              round(bb_u, 6),
        'bb_mid':                round(bb_m, 6),
        'bb_lower':              round(bb_l, 6),
        'price_vs_ema20':        'ABOVE' if current > ema20  else 'BELOW',
        'price_vs_ema50':        'ABOVE' if current > ema50  else 'BELOW',
        'price_vs_ema200':       'ABOVE' if current > ema200 else 'BELOW',
        'trend_slope_20bar_pct': round(slope, 4),
        'avg_volume_60min':      round(avg_vol, 4),
        'bars_analysed':         len(rows),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Claude API helpers (shared pattern from forex brain)
# ─────────────────────────────────────────────────────────────────────────────

def _claude_post(api_key, payload, timeout=120, max_retries=6):
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
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            if e.code in (529, 503, 429) and attempt < max_retries:
                retry_after = e.headers.get('Retry-After')
                wait = int(retry_after) if retry_after and retry_after.isdigit() else delay
                _logger.warning("Claude HTTP %s (attempt %d/%d) — waiting %ds…",
                                e.code, attempt, max_retries, wait)
                time.sleep(wait)
                delay = min(delay * 2, 120)
            else:
                raise
        except Exception:
            raise


def _summarise_book(title, text, api_key):
    if len(text) > 8000:
        text_sample = text[:4000] + "\n...\n" + text[-4000:]
    else:
        text_sample = text
    prompt = (
        f"Summarise the KEY crypto trading/investing principles from '{title}' "
        f"in bullet points (max 400 words). Focus on: entry/exit signals, "
        f"market cycles, halving cycles, on-chain signals, risk management, "
        f"HODLing vs trading, and when NOT to trade.\n\nEXTRACT:\n{text_sample}"
    )
    try:
        data = _claude_post(api_key, {
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 600,
            "messages":   [{"role": "user", "content": prompt}]
        }, timeout=45, max_retries=6)
        return data['content'][0]['text']
    except Exception as exc:
        _logger.error("Book summarisation failed for '%s': %s", title, exc)
        return f"[Summarisation unavailable for '{title}': {exc}]"


def _pdf_text_from_bytes(pdf_bytes, max_chars=80_000):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        chunks, total = [], 0
        for page in reader.pages:
            t = page.extract_text() or ''
            chunks.append(t); total += len(t)
            if total >= max_chars:
                break
        return '\n'.join(chunks)[:max_chars]
    except Exception as exc:
        _logger.warning("PDF extraction failed: %s", exc)
        return ''


def _collect_pdfs_from_attachment(attachment):
    raw  = base64.b64decode(attachment.datas or b'')
    name = attachment.name or ''
    mime = attachment.mimetype or ''
    if name.lower().endswith('.zip') or 'zip' in mime:
        results = []
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for pname in sorted(zf.namelist()):
                    if pname.lower().endswith('.pdf') and '__MACOSX' not in pname:
                        title = re.sub(r'\.pdf$', '', pname.split('/')[-1], flags=re.IGNORECASE)
                        try:
                            results.append((title, zf.read(pname)))
                        except Exception as e:
                            _logger.warning("Could not read %s: %s", pname, e)
        except Exception as e:
            _logger.warning("Could not open zip '%s': %s", name, e)
        return results
    title = re.sub(r'\.pdf$', '', name, flags=re.IGNORECASE)
    return [(title, raw)]


def _fetch_news(serper_key, hours=10, pair='BTC/USDT'):
    if not serper_key:
        return []
    coin = pair.split('/')[0]
    try:
        payload = json.dumps({"q": f"{coin} crypto", "num": 10, "tbs": f"qdr:h{hours}"}).encode()
        req = urllib.request.Request(
            "https://google.serper.dev/news",
            data=payload,
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return [
                {'title': i.get('title',''), 'snippet': i.get('snippet',''),
                 'date':  i.get('date',''),  'source':  i.get('source','')}
                for i in data.get('news', [])
            ]
    except Exception as exc:
        _logger.warning("News fetch failed: %s", exc)
        return []


def _repair_truncated_json(text):
    t = text.strip()
    if not t.startswith('{'):
        return None
    try:
        depth, in_string, escape_next = 0, False, False
        for ch in t:
            if escape_next: escape_next = False; continue
            if ch == '\\' and in_string: escape_next = True; continue
            if ch == '"' and not escape_next: in_string = not in_string; continue
            if not in_string:
                if ch == '{': depth += 1
                elif ch == '}': depth -= 1
        if in_string: t += '"'
        t = t.rstrip().rstrip(',') + '}' * max(depth, 0)
        return json.loads(t)
    except Exception:
        return None


def _extract_signal_from_partial(text):
    signal = 'INSUFFICIENT DATA'
    sig_m  = re.search(r'"signal"\s*:\s*"([^"]+)"', text)
    if sig_m: signal = sig_m.group(1)
    conf_m = re.search(r'"confidence"\s*:\s*"([^"]+)"', text)
    conf   = conf_m.group(1) if conf_m else 'LOW'
    return {
        "signal": signal, "confidence": conf,
        "reasoning": "Partial response recovered — please retry.",
        "risk_warning": "Verify before trading.",
        "entry_price": None, "stop_loss": None, "take_profit": None,
        "price_analysis": "", "news_analysis": "", "book_wisdom": "", "conflicts": "",
    }


def _ask_claude_for_signal(brain_summary, indicators, recent_ohlc_str,
                            news_items, api_key, news_hours, pair='BTC/USDT'):
    coin = pair.split('/')[0]
    news_block = (
        "\n".join(f"• [{i['date']}] {i['source']}: {i['title']} — {i['snippet']}"
                  for i in news_items)
        if news_items else f"No news available for {coin}."
    )
    ind_block    = "\n".join(f"  {k}: {v}" for k, v in indicators.items())
    brain_capped = (brain_summary or "No summary.")[:6000]

    system_prompt = f"""You are a quantitative crypto trading AI. Analyse {pair} and return a signal.
Crypto-specific context: 24/7 market, high volatility, halving cycles affect BTC fundamentals,
sentiment and on-chain flows drive short-term moves, correlation with BTC dominance matters.
Respond ONLY with valid JSON — no markdown, no preamble:
{{
  "signal": "BUY"|"SELL"|"HOLD"|"INSUFFICIENT DATA",
  "confidence": "HIGH"|"MEDIUM"|"LOW",
  "price_analysis": "...",
  "news_analysis": "...",
  "book_wisdom": "...",
  "conflicts": "...",
  "entry_price": <float|null>,
  "stop_loss": <float|null>,
  "take_profit": <float|null>,
  "reasoning": "...",
  "risk_warning": "..."
}}
Rules: HIGH only if technicals+news+book wisdom all agree. MEDIUM if 2/3. LOW or INSUFFICIENT DATA if conflicting."""

    user_prompt = f"""=== CRYPTO KNOWLEDGE (from books) ===
{brain_capped}

=== TECHNICAL INDICATORS ({pair}) ===
{ind_block}

=== RECENT OHLC — {pair} (last 20 bars, 1-min) ===
{recent_ohlc_str}

=== {coin} NEWS (last {news_hours} hours) ===
{news_block}

Return JSON signal for {pair} now."""

    try:
        data = _claude_post(api_key, {
            "model":      "claude-haiku-4-5-20251001",
            "max_tokens": 1200,
            "system":     system_prompt,
            "messages":   [{"role": "user", "content": user_prompt}]
        }, timeout=60, max_retries=6)
        raw   = data['content'][0]['text']
        clean = re.sub(r'^```[a-z]*\s*|\s*```$', '', raw.strip())
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            repaired = _repair_truncated_json(clean)
            return repaired if repaired else _extract_signal_from_partial(clean)
    except json.JSONDecodeError as e:
        _logger.error("Crypto signal JSON error: %s", e)
        return {"signal": "INSUFFICIENT DATA", "confidence": "LOW",
                "reasoning": f"JSON parse error: {e}", "risk_warning": "Do not trade on this signal."}
    except Exception as exc:
        _logger.error("Crypto signal API failed: %s", exc)
        return {"signal": "INSUFFICIENT DATA", "confidence": "LOW",
                "reasoning": f"API failed: {exc}. Wait 1-2 min and retry.",
                "risk_warning": "Do not trade on this signal."}


# ─────────────────────────────────────────────────────────────────────────────
# Odoo Model — CryptoBrain
# ─────────────────────────────────────────────────────────────────────────────

class CryptoBrain(models.Model):
    _name        = 'trading.crypto_brain'
    _description = 'Crypto Trading AI Brain'
    _order       = 'create_date desc'
    _inherit     = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Brain Name', default='New Crypto Brain', required=True)
    state = fields.Selection([
        ('draft',    'Not Trained'),
        ('training', 'Training…'),
        ('ready',    'Ready'),
        ('error',    'Error'),
    ], default='draft', string='Status', tracking=True)

    pair_to_analyse = fields.Selection(
        CRYPTO_PAIRS, string='Pair to Analyse',
        default='BTC/USDT', required=True,
        help='Which crypto pair to generate a signal for.'
    )
    exchange_id = fields.Selection(
        SUPPORTED_EXCHANGES, string='Exchange',
        default='binance', required=True,
        help='Which exchange to fetch price data from. Binance has the deepest history.'
    )
    fetch_days = fields.Integer(
        string='Fetch Days',
        default=30,
        help=(
            'How many days of 1-min history to download when clicking Fetch Data.\n'
            '7  days  =  ~10,000 bars  (fast,  ~1 min)\n'
            '30 days  =  ~43,000 bars  (good,  ~5 min)\n'
            '90 days  =  ~130,000 bars (better, ~15 min)\n'
            '365 days =  ~525,000 bars (best,   ~60 min)\n'
            'More days = better EMA 200 accuracy.'
        )
    )

    book_attachment_ids = fields.Many2many(
        'ir.attachment', 'crypto_brain_book_att_rel', 'brain_id', 'attachment_id',
        string='Books', help='Upload crypto PDF books or a ZIP of PDFs.',
    )
    stock_attachment_ids = fields.Many2many(
        'ir.attachment', 'crypto_brain_stock_att_rel', 'brain_id', 'attachment_id',
        string='Price Data Files',
        help='Upload CSV/ZIP from CryptoDataDownload.com, or use Fetch Live Data.',
    )

    books_processed   = fields.Integer(string='Books Processed',  readonly=True)
    knowledge_summary = fields.Text(string='Knowledge Summary',   readonly=True)
    book_titles       = fields.Text(string='Books Indexed',       readonly=True)
    data_rows_loaded  = fields.Integer(string='Price Rows',       readonly=True)
    last_data_date    = fields.Char(string='Latest Price Date',   readonly=True)
    training_log      = fields.Text(string='Training Log',        readonly=True)
    create_date       = fields.Datetime(string='Created',         readonly=True)
    lookback_hours    = fields.Integer(
        string='Lookback (hours)', default=24,
        help='Hours of 1-min bars used for indicator calculation. '
             'Crypto moves fast — 24h is usually sufficient.'
    )

    # ── Fetch Data via CCXT ───────────────────────────────────────────────────

    def action_fetch_live_data(self):
        """
        Fetch recent + historical 1-min OHLCV data using CCXT.
        Downloads `fetch_days` days of 1-min bars from the selected exchange.
        Replaces any previous CCXT-fetched attachment for this pair.

        Requires: pip install ccxt --break-system-packages
        """
        self.ensure_one()
        pair        = self.pair_to_analyse or 'BTC/USDT'
        exchange_id = self.exchange_id or 'binance'
        days        = max(1, self.fetch_days or 30)

        # Warn if this will take a while
        if days > 90:
            estimated_min = round(days / 6)
            _logger.info("Large fetch requested: %d days (~%d min)", days, estimated_min)

        try:
            rows, csv_bytes = _fetch_ccxt_ohlcv(pair, exchange_id, days)
        except RuntimeError as e:
            raise UserError(str(e))
        except Exception as e:
            raise UserError(
                f"Data fetch failed for {pair} on {exchange_id}:\n{e}\n\n"
                f"Make sure CCXT is installed:\n"
                f"  pip install ccxt --break-system-packages"
            )

        if len(rows) < 50:
            raise UserError(
                f"Only {len(rows)} bars returned for {pair} on {exchange_id}.\n"
                f"Try a different exchange or check the pair name."
            )

        # Replace previous CCXT attachment for this pair on this brain
        att_name    = f"CCXT_{exchange_id}_{pair.replace('/', '')}_{fields.Date.today()}.csv"
        old_pattern = f"CCXT_{exchange_id}_{pair.replace('/', '')}_"
        old_atts = self.stock_attachment_ids.filtered(
            lambda a: (a.name or '').startswith(old_pattern)
        )
        if old_atts:
            self.write({'stock_attachment_ids': [(3, a.id) for a in old_atts]})
            old_atts.unlink()

        new_att = self.env['ir.attachment'].create({
            'name':      att_name,
            'type':      'binary',
            'datas':     base64.b64encode(csv_bytes).decode(),
            'mimetype':  'text/csv',
            'res_model': 'trading.crypto_brain',
            'res_id':    self.id,
        })
        self.write({'stock_attachment_ids': [(4, new_att.id)]})

        # Refresh stats
        all_rows  = _collect_ohlc_from_attachments(self.stock_attachment_ids)
        last_date = all_rows[-1][0] if all_rows else 'N/A'
        first_date = all_rows[0][0] if all_rows else 'N/A'
        self.write({'data_rows_loaded': len(all_rows), 'last_data_date': last_date})

        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   f'✅ {pair} Data Fetched ({exchange_id})',
                'message': (
                    f"Downloaded {len(rows):,} 1-min bars over {days} days.\n"
                    f"From {first_date} to {last_date}."
                ),
                'sticky': False, 'type': 'success',
            },
        }

    # ── Train ─────────────────────────────────────────────────────────────────

    def action_train(self):
        self.ensure_one()
        cfg     = self.env['trading.config'].get_config()
        api_key = cfg.get('anthropic_api_key', '')

        if not api_key:
            raise UserError("Anthropic API key missing — go to Trading AI → Configuration.")
        if not self.book_attachment_ids:
            raise UserError("No books attached. Upload crypto PDF books or a Books.zip.")
        if not self.stock_attachment_ids:
            raise UserError(
                "No price data attached.\n"
                "Click 📥 Fetch Live Data (free, no key needed) or upload a CryptoDataDownload CSV."
            )

        self.write({'state': 'training', 'training_log': 'Starting crypto brain training…\n'})
        self.env.cr.commit()

        log_lines, summaries, titles = [], [], []

        # Step 1: collect PDFs
        pdf_collection = []
        for att in self.book_attachment_ids:
            pdf_collection.extend(_collect_pdfs_from_attachment(att))

        if not pdf_collection:
            self.write({'state': 'error',
                        'training_log': 'No PDFs found. Attach .pdf files or a .zip of PDFs.'})
            return

        log_lines.append(f"Found {len(pdf_collection)} PDF(s) across {len(self.book_attachment_ids)} attachment(s).")

        # Step 2: summarise each book
        failed_titles = []
        for idx, (title, pdf_bytes) in enumerate(pdf_collection, 1):
            log_lines.append(f"[{idx}/{len(pdf_collection)}] Processing: {title}")
            self.write({'training_log': '\n'.join(log_lines)})
            self.env.cr.commit()

            text = _pdf_text_from_bytes(pdf_bytes)
            if len(text) < 200:
                log_lines.append(f"  ⚠ Skipped — only {len(text)} chars (scanned PDF?).")
                continue

            summary = _summarise_book(title, text, api_key)
            if summary.startswith('[Summarisation unavailable'):
                failed_titles.append(title)
                log_lines.append("  ⚠ Failed — skipping this book.")
            else:
                summaries.append(f"=== {title} ===\n{summary}")
                titles.append(title)
                log_lines.append(f"  ✓ {len(summary)} chars summarised")
                self.write({
                    'training_log':   '\n'.join(log_lines),
                    'books_processed': len(titles),
                    'book_titles':    '\n'.join(titles),
                })
                self.env.cr.commit()

            if idx < len(pdf_collection):
                log_lines.append("  … waiting 8s …")
                self.write({'training_log': '\n'.join(log_lines)})
                self.env.cr.commit()
                time.sleep(8)

        if failed_titles:
            log_lines.append(f"⚠ {len(failed_titles)} book(s) failed: {', '.join(failed_titles)}")

        if not summaries:
            self.write({'state': 'error',
                        'training_log': '\n'.join(log_lines) + '\n\nAll PDFs skipped.'})
            return

        # Step 3: merge summaries
        log_lines.append("Merging into master crypto knowledge base…")
        self.write({'training_log': '\n'.join(log_lines)})
        self.env.cr.commit()

        combined     = "\n\n".join(summaries)[:20_000]
        master_prompt = (
            "Compile a master crypto trading brain from these book summaries. "
            "Merge into ONE concise knowledge base with sections: "
            "(1) Entry signals, (2) Exit signals, (3) Market cycles & halving, "
            "(4) Sentiment & on-chain signals, (5) Risk management, "
            "(6) When NOT to trade, (7) HODL vs active trading rules. "
            "Max 1500 words. Be precise and actionable.\n\n" + combined
        )
        try:
            data = _claude_post(api_key, {
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 2000,
                "messages":   [{"role": "user", "content": master_prompt}]
            }, timeout=90, max_retries=6)
            master_summary = data['content'][0]['text']
            log_lines.append("✓ Master crypto knowledge base compiled.")
        except Exception as exc:
            master_summary = combined[:30_000]
            log_lines.append(f"⚠ Master merge failed ({exc}) — using concatenation.")

        # Step 4: count price rows
        log_lines.append("Scanning price data attachments…")
        self.write({'training_log': '\n'.join(log_lines)})
        self.env.cr.commit()

        all_rows  = _collect_ohlc_from_attachments(self.stock_attachment_ids)
        last_date = all_rows[-1][0] if all_rows else 'N/A'
        log_lines.append(f"✓ {len(all_rows):,} price rows. Latest: {last_date}")
        log_lines.append("✅ Crypto brain training complete!")

        self.write({
            'state':             'ready',
            'books_processed':   len(titles),
            'knowledge_summary': master_summary,
            'book_titles':       '\n'.join(titles),
            'data_rows_loaded':  len(all_rows),
            'last_data_date':    last_date,
            'training_log':      '\n'.join(log_lines),
        })
        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   '✅ Crypto Brain Training Complete',
                'message': f'Processed {len(titles)} books. Ready to advise.',
                'sticky': False, 'type': 'success',
            },
        }

    # ── Get Signal ────────────────────────────────────────────────────────────

    def action_get_signal(self):
        self.ensure_one()
        if self.state != 'ready':
            raise UserError("Brain not trained yet. Click 🧠 Train Brain first.")

        cfg        = self.env['trading.config'].get_config()
        api_key    = cfg.get('anthropic_api_key', '')
        serper_key = cfg.get('serper_api_key', '')
        news_hours = cfg.get('news_hours', 10)
        pair       = self.pair_to_analyse or 'BTC/USDT'

        if not api_key:
            raise UserError("Anthropic API key missing.")

        all_rows = _collect_ohlc_from_attachments(self.stock_attachment_ids)
        if len(all_rows) < 50:
            raise UserError(
                f"Only {len(all_rows)} price rows for {pair}.\n"
                f"Click 📥 Fetch Live Data or attach a CryptoDataDownload CSV."
            )

        indicators = _compute_indicators(all_rows, self.lookback_hours)
        recent_20  = all_rows[-20:]
        ohlc_str   = "\n".join(
            f"{r[0]} | O:{r[1]:.4f} H:{r[2]:.4f} L:{r[3]:.4f} C:{r[4]:.4f} V:{r[5]:.2f}"
            for r in recent_20
        )
        news_items = _fetch_news(serper_key, news_hours, pair)

        result = _ask_claude_for_signal(
            brain_summary   = self.knowledge_summary,
            indicators      = indicators,
            recent_ohlc_str = ohlc_str,
            news_items      = news_items,
            api_key         = api_key,
            news_hours      = news_hours,
            pair            = pair,
        )

        signal = self.env['trading.crypto_signal'].create({
            'brain_id':       self.id,
            'pair':           pair,
            'signal':         result.get('signal', 'INSUFFICIENT DATA'),
            'confidence':     result.get('confidence', 'LOW'),
            'current_price':  indicators.get('current_price', 0),
            'entry_price':    result.get('entry_price') or indicators.get('current_price', 0),
            'stop_loss':      result.get('stop_loss'),
            'take_profit':    result.get('take_profit'),
            'rsi':            indicators.get('rsi_14'),
            'macd':           indicators.get('macd'),
            'ema_20':         indicators.get('ema_20'),
            'ema_50':         indicators.get('ema_50'),
            'ema_200':        indicators.get('ema_200'),
            'price_analysis': result.get('price_analysis', ''),
            'news_analysis':  result.get('news_analysis', ''),
            'book_wisdom':    result.get('book_wisdom', ''),
            'conflicts':      result.get('conflicts', ''),
            'reasoning':      result.get('reasoning', ''),
            'risk_warning':   result.get('risk_warning', ''),
            'news_count':     len(news_items),
            'bars_analysed':  indicators.get('bars_analysed', 0),
            'raw_response':   json.dumps(result, indent=2),
        })

        return {
            'type':      'ir.actions.act_window',
            'name':      f'{pair} Signal',
            'res_model': 'trading.crypto_signal',
            'res_id':    signal.id,
            'view_mode': 'form',
            'target':    'current',
        }
