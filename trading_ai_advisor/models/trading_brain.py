# -*- coding: utf-8 -*-
"""
trading_brain.py — PDF & data helper functions
================================================
Pure-Python helpers shared by daily_analysis.py and knowledge_library.py:
  • PDF text extraction  (_pdf_text_from_bytes, _collect_pdfs_from_attachment)
  • CSV/OHLC parsing     (_parse_ohlc_attachment, _compute_indicators, …)
  • Price fetching       (_fetch_twelve_data_ohlc, _fetch_binance_ohlc, …)
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

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PDF helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pdf_text_from_bytes(pdf_bytes, max_chars=80_000):
    """
    Extract text from raw PDF bytes.
    Tries (in order): pypdf → PyPDF2 → pdfminer.six
    Returns empty string if all extractors fail or find no text.
    """
    def _read_pages(reader):
        chunks, total = [], 0
        for page in reader.pages:
            t = page.extract_text() or ''
            chunks.append(t)
            total += len(t)
            if total >= max_chars:
                break
        return '\n'.join(chunks)[:max_chars]

    buf = io.BytesIO(pdf_bytes)

    try:
        from pypdf import PdfReader
        result = _read_pages(PdfReader(buf))
        if result.strip():
            return result
    except ImportError:
        pass
    except Exception as exc:
        _logger.warning("pypdf extraction failed: %s", exc)

    buf.seek(0)
    try:
        import PyPDF2
        result = _read_pages(PyPDF2.PdfReader(buf))
        if result.strip():
            return result
    except ImportError:
        pass
    except Exception as exc:
        _logger.warning("PyPDF2 extraction failed: %s", exc)

    buf.seek(0)
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        return (pdfminer_extract(buf) or '')[:max_chars]
    except ImportError:
        pass
    except Exception as exc:
        _logger.warning("pdfminer extraction failed: %s", exc)

    return ''


def _collect_pdfs_from_attachment(attachment):
    """
    Given a single ir.attachment, return list of (title, pdf_bytes).
    Handles:
      • application/pdf  → one entry
      • application/zip / application/x-zip-compressed
        or any .zip name → extract all PDFs inside
    """
    raw = base64.b64decode(attachment.datas or b'')
    name = attachment.name or ''
    mimetype = attachment.mimetype or ''

    if name.lower().endswith('.zip') or 'zip' in mimetype:
        results = []
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                pdf_names = [
                    n for n in zf.namelist()
                    if n.lower().endswith('.pdf')
                    and '__MACOSX' not in n
                ]
                for pname in sorted(pdf_names):
                    title = re.sub(r'\.pdf$', '', pname.split('/')[-1],
                                   flags=re.IGNORECASE)
                    try:
                        results.append((title, zf.read(pname)))
                    except Exception as e:
                        _logger.warning("Could not read %s from zip: %s", pname, e)
        except Exception as e:
            _logger.warning("Could not open zip attachment '%s': %s", name, e)
        return results

    # Plain PDF
    title = re.sub(r'\.pdf$', '', name, flags=re.IGNORECASE)
    return [(title, raw)]


# ─────────────────────────────────────────────────────────────────────────────
# Stock data helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_ohlc_content(text_content):
    """
    Parse HistData CSV into list of (dt, o, h, l, c, v).

    Handles two HistData formats:
      Format A (ASCII/generic):  20150101 130100,1.1234,1.1240,1.1230,1.1238,0
      Format B (MetaTrader MT):  2015.01.01,13:01,119.666,119.666,119.666,119.666,0
    Format B has date and time as TWO separate comma-separated fields before OHLC.
    """
    rows = []
    for line in text_content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = re.split(r'[;,]', line)
        if len(parts) < 5:
            continue
        try:
            # Detect Format B: parts[0] looks like "2015.01.01" and parts[1] like "13:01"
            if '.' in parts[0] and ':' in parts[1]:
                # MetaTrader format — date + time are separate fields
                dt = parts[0].replace('.', '-') + 'T' + parts[1]
                o, h, l, c = float(parts[2]), float(parts[3]), float(parts[4]), float(parts[5])
                v = float(parts[6]) if len(parts) > 6 else 0
            else:
                # ASCII format — datetime is single field
                dt = parts[0].replace(' ', 'T')
                o, h, l, c = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                v = float(parts[5]) if len(parts) > 5 else 0
            rows.append((dt, o, h, l, c, v))
        except (ValueError, IndexError):
            continue
    return rows


def _collect_ohlc_from_attachments(attachments):
    """
    Read all stock data attachments (CSV, TXT, or ZIP of CSV/TXT).
    Handles:
      - Flat ZIP of CSVs
      - ZIP of year-ZIPs (HistData ASCII style)
      - ZIP with subfolder containing year-ZIPs (HistData MT style:
          Stock_data.zip → Stock data/ → HISTDATA_COM_MT_USDJPY_M12015.zip → DAT_*.csv)
    Returns sorted, deduplicated list of (dt, o, h, l, c, v).
    """
    all_rows = []

    def _read_year_zip(year_zip_bytes, source_name):
        """Extract OHLC rows from a single year ZIP (contains DAT_*.csv + readme .txt)."""
        result = []
        try:
            with zipfile.ZipFile(io.BytesIO(year_zip_bytes)) as yz:
                # Prefer .csv over .txt — .txt files in HistData ZIPs are readmes
                csv_names = [n for n in yz.namelist() if n.lower().endswith('.csv')]
                txt_names = [n for n in yz.namelist() if n.lower().endswith('.txt')]
                targets = csv_names if csv_names else txt_names
                for fname in targets:
                    content = yz.read(fname).decode('utf-8', errors='ignore')
                    result.extend(_parse_ohlc_content(content))
        except Exception as e:
            _logger.warning("Could not read year zip %s: %s", source_name, e)
        return result

    for att in attachments:
        raw = base64.b64decode(att.datas or b'')
        name = (att.name or '').lower()
        mimetype = att.mimetype or ''

        if name.endswith('.zip') or 'zip' in mimetype:
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    for entry in zf.namelist():
                        entry_lower = entry.lower()

                        # Skip directory entries and Mac metadata
                        if entry.endswith('/') or '__MACOSX' in entry:
                            continue

                        if entry_lower.endswith('.zip'):
                            # Could be a year-zip directly or inside a subfolder
                            year_bytes = zf.read(entry)
                            all_rows.extend(_read_year_zip(year_bytes, entry))

                        elif entry_lower.endswith('.csv'):
                            content = zf.read(entry).decode('utf-8', errors='ignore')
                            all_rows.extend(_parse_ohlc_content(content))

                        # Skip .txt at top level — they're readmes in HistData ZIPs
            except Exception as e:
                _logger.warning("Could not open zip attachment '%s': %s", att.name, e)

        elif name.endswith(('.csv', '.txt')):
            content = raw.decode('utf-8', errors='ignore')
            all_rows.extend(_parse_ohlc_content(content))

    # Sort and deduplicate by datetime string
    all_rows.sort(key=lambda r: r[0])
    seen, deduped = set(), []
    for r in all_rows:
        if r[0] not in seen:
            seen.add(r[0])
            deduped.append(r)
    return deduped


# ─────────────────────────────────────────────────────────────────────────────
# Technical indicators (pure Python — no pandas/numpy required)
# ─────────────────────────────────────────────────────────────────────────────

def _ema(prices, period):
    k = 2 / (period + 1)
    ema = [prices[0]]
    for p in prices[1:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema


def _rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    if len(gains) < period:
        return 50.0
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))


def _macd(closes):
    if len(closes) < 26:
        return 0, 0
    e12 = _ema(closes, 12)
    e26 = _ema(closes, 26)
    line = [a - b for a, b in zip(e12, e26)]
    signal = _ema(line, 9)
    return line[-1], signal[-1]


def _bollinger(closes, period=20):
    if len(closes) < period:
        return closes[-1], closes[-1], closes[-1]
    w = closes[-period:]
    mid = sum(w) / period
    std = math.sqrt(sum((x - mid) ** 2 for x in w) / period)
    return mid + 2 * std, mid, mid - 2 * std


def _compute_indicators(rows, lookback_hours=48):
    """Compute all technical indicators from OHLC rows."""
    max_rows = lookback_hours * 60
    rows = rows[-max_rows:] if len(rows) > max_rows else rows
    if not rows:
        return {}

    closes = [r[4] for r in rows]
    highs  = [r[2] for r in rows]
    lows   = [r[3] for r in rows]
    opens  = [r[1] for r in rows]

    rsi_val             = _rsi(closes)
    macd_val, sig_val   = _macd(closes)
    bb_u, bb_m, bb_l    = _bollinger(closes)
    ema20  = _ema(closes, 20)[-1]  if len(closes) >= 20  else closes[-1]
    ema50  = _ema(closes, 50)[-1]  if len(closes) >= 50  else closes[-1]
    ema200 = _ema(closes, 200)[-1] if len(closes) >= 200 else closes[-1]
    current = closes[-1]
    h24 = max(highs[-1440:])  if len(highs)  >= 1440 else max(highs)
    l24 = min(lows[-1440:])   if len(lows)   >= 1440 else min(lows)
    slope = (closes[-1] - closes[-20]) / closes[-20] * 100 if len(closes) >= 20 else 0

    return {
        'current_price':        round(current, 5),
        'open_period':          round(opens[0], 5),
        'high_24h':             round(h24, 5),
        'low_24h':              round(l24, 5),
        'price_change_pct':     round((current - opens[0]) / opens[0] * 100, 4),
        'rsi_14':               round(rsi_val, 2),
        'macd':                 round(macd_val, 6),
        'macd_signal':          round(sig_val, 6),
        'macd_histogram':       round(macd_val - sig_val, 6),
        'ema_20':               round(ema20, 5),
        'ema_50':               round(ema50, 5),
        'ema_200':              round(ema200, 5),
        'bb_upper':             round(bb_u, 5),
        'bb_mid':               round(bb_m, 5),
        'bb_lower':             round(bb_l, 5),
        'price_vs_ema20':       'ABOVE' if current > ema20  else 'BELOW',
        'price_vs_ema50':       'ABOVE' if current > ema50  else 'BELOW',
        'price_vs_ema200':      'ABOVE' if current > ema200 else 'BELOW',
        'trend_slope_20bar_pct': round(slope, 4),
        'bars_analysed':        len(rows),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Claude API helpers
# ─────────────────────────────────────────────────────────────────────────────

def _claude_post(api_key, payload, timeout=120, max_retries=6):
    """
    POST to Claude API with exponential backoff retry.

    Retries on:
      • HTTP 529 — API overloaded
      • HTTP 503 — Service unavailable
      • HTTP 429 — Rate limited

    Respects Retry-After header if present.
    Backoff: 10s → 20s → 40s → 80s → 120s → 120s (capped at 120s)
    """
    from urllib.error import HTTPError

    body = json.dumps(payload).encode()
    delay = 10  # initial wait in seconds

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())

        except HTTPError as e:
            if e.code in (529, 503, 429) and attempt < max_retries:
                # Honour Retry-After header if the server provides one
                retry_after = e.headers.get('Retry-After')
                wait = int(retry_after) if retry_after and retry_after.isdigit() \
                       else delay
                _logger.warning(
                    "Claude API HTTP %s (attempt %d/%d) — waiting %ds…",
                    e.code, attempt, max_retries, wait
                )
                time.sleep(wait)
                delay = min(delay * 2, 120)
            else:
                raise

        except Exception:
            raise


def _summarise_book(title, text, api_key):
    """
    Ask Claude Haiku to compress one book into actionable trading wisdom.
    Uses Haiku (faster, lower quota usage) for batch summarisation.
    Keeps input to 8000 chars to minimise token usage and 529 errors.
    """
    # Take first 4000 + last 4000 chars to capture intro and conclusions
    if len(text) > 8000:
        text_sample = text[:4000] + "\n...\n" + text[-4000:]
    else:
        text_sample = text

    prompt = (
        f"Summarise the KEY trading/investing principles from '{title}' "
        f"in bullet points (max 400 words). Focus on: entry/exit signals, "
        f"contrarian signals, market psychology, risk management rules, "
        f"and when NOT to trade.\n\nEXTRACT:\n{text_sample}"
    )
    try:
        data = _claude_post(api_key, {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 600,
            "messages": [{"role": "user", "content": prompt}]
        }, timeout=45, max_retries=6)
        return data['content'][0]['text']
    except Exception as exc:
        _logger.error("Book summarisation failed for '%s': %s", title, exc)
        return f"[Summarisation unavailable for '{title}': {exc}]"


def _fetch_news(serper_key, hours=10, pair='EUR/USD'):
    """Fetch recent forex pair news via Serper.dev. Returns list of dicts."""
    if not serper_key:
        return []
    try:
        payload = json.dumps({
            "q": f"{pair} forex",
            "num": 10,
            "tbs": f"qdr:h{hours}",
        }).encode()
        req = urllib.request.Request(
            "https://google.serper.dev/news",
            data=payload,
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return [
                {
                    'title':   item.get('title', ''),
                    'snippet': item.get('snippet', ''),
                    'date':    item.get('date', ''),
                    'source':  item.get('source', ''),
                }
                for item in data.get('news', [])
            ]
    except Exception as exc:
        _logger.warning("News fetch failed: %s", exc)
        return []


def _repair_truncated_json(text):
    """
    Attempt to repair a truncated JSON string by closing open structures.
    Returns a valid dict or None if repair fails.
    """
    t = text.strip()
    if not t.startswith('{'):
        return None
    try:
        # Count open/close braces to see what's missing
        depth = 0
        in_string = False
        escape_next = False
        for ch in t:
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if not in_string:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1

        # If we're inside a string, close it
        if in_string:
            t += '"'
        # Close any unclosed values with a safe placeholder
        t = t.rstrip().rstrip(',')
        # Close missing braces
        t += '}' * max(depth, 0)

        return json.loads(t)
    except Exception:
        return None


def _extract_signal_from_partial(text):
    """
    Last-resort extractor — pull the signal value from partial JSON using regex.
    Returns a minimal dict with whatever could be extracted.
    """
    signal = 'INSUFFICIENT DATA'
    confidence = 'LOW'
    reasoning = 'Response was truncated. Please retry.'

    sig_match = re.search(r'"signal"\s*:\s*"([^"]+)"', text)
    if sig_match:
        signal = sig_match.group(1)

    conf_match = re.search(r'"confidence"\s*:\s*"([^"]+)"', text)
    if conf_match:
        confidence = conf_match.group(1)

    reason_match = re.search(r'"reasoning"\s*:\s*"([^"]*)"', text)
    if reason_match:
        reasoning = reason_match.group(1)

    price_match = re.search(r'"entry_price"\s*:\s*([0-9.]+)', text)
    stop_match  = re.search(r'"stop_loss"\s*:\s*([0-9.]+)', text)
    tp_match    = re.search(r'"take_profit"\s*:\s*([0-9.]+)', text)

    return {
        "signal":         signal,
        "confidence":     confidence,
        "reasoning":      reasoning + " (partial response recovered)",
        "entry_price":    float(price_match.group(1)) if price_match else None,
        "stop_loss":      float(stop_match.group(1))  if stop_match  else None,
        "take_profit":    float(tp_match.group(1))    if tp_match    else None,
        "price_analysis": "",
        "news_analysis":  "",
        "book_wisdom":    "",
        "conflicts":      "",
        "risk_warning":   "Partial response — verify before trading.",
    }


def _ask_claude_for_advice(brain_summary, indicators, recent_ohlc_str,
                            news_items, api_key, news_hours, pair='EUR/USD'):
    """Send everything to Claude Haiku and return the parsed signal dict."""
    news_block = (
        "\n".join(
            f"• [{i['date']}] {i['source']}: {i['title']} — {i['snippet']}"
            for i in news_items
        ) if news_items else
        f"No news data available for {pair}."
    )
    ind_block = "\n".join(f"  {k}: {v}" for k, v in indicators.items())

    # Cap brain summary to keep total payload small and avoid 529
    brain_capped = brain_summary[:6000] if brain_summary else "No brain summary available."

    system_prompt = f"""You are a quantitative trading AI. Analyse {pair} and return a signal.
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
Rules: HIGH confidence only if technicals+news+book wisdom all agree. MEDIUM if 2/3 agree. LOW or INSUFFICIENT DATA if conflicting or missing data."""

    user_prompt = f"""=== TRADING KNOWLEDGE (from books) ===
{brain_capped}

=== TECHNICAL INDICATORS ({pair}) ===
{ind_block}

=== RECENT OHLC — {pair} (last 20 bars, 1-min) ===
{recent_ohlc_str}

=== {pair} NEWS (last {news_hours} hours) ===
{news_block}

Return JSON signal for {pair} now."""

    try:
        data = _claude_post(api_key, {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1200,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}]
        }, timeout=60, max_retries=6)
        raw = data['content'][0]['text']
        clean = re.sub(r'^```[a-z]*\s*|\s*```$', '', raw.strip())

        # Try parsing as-is first
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            # Response was truncated — try to repair by closing open strings/braces
            repaired = _repair_truncated_json(clean)
            if repaired:
                return repaired
            # Last resort: extract just the signal field if visible
            return _extract_signal_from_partial(clean)

    except json.JSONDecodeError as e:
        _logger.error("Signal JSON parse failed: %s\nRaw: %s", e, raw[:500] if 'raw' in dir() else 'N/A')
        return {
            "signal": "INSUFFICIENT DATA", "confidence": "LOW",
            "reasoning": f"AI returned malformed JSON: {e}",
            "risk_warning": "Do not trade on this signal.",
        }
    except Exception as exc:
        _logger.error("Signal API call failed after retries: %s", exc)
        return {
            "signal": "INSUFFICIENT DATA", "confidence": "LOW",
            "reasoning": (
                f"API call failed after retries: {exc}\n\n"
                f"This is usually a temporary Anthropic overload (HTTP 529). "
                f"Please wait 1-2 minutes and try again."
            ),
            "risk_warning": "Do not trade on this signal. Retry in a few minutes.",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Alpha Vantage — live forex data fetch
# ─────────────────────────────────────────────────────────────────────────────

# Maps our pair selection values → Alpha Vantage from/to symbols
_AV_PAIR_MAP = {
    'EUR/USD': ('EUR', 'USD'),
    'USD/JPY': ('USD', 'JPY'),
    'GBP/USD': ('GBP', 'USD'),
    'USD/CNY': ('USD', 'CNY'),
    'AUD/USD': ('AUD', 'USD'),
    'USD/CAD': ('USD', 'CAD'),
    'USD/CHF': ('USD', 'CHF'),
    'USD/HKD': ('USD', 'HKD'),
    'EUR/JPY': ('EUR', 'JPY'),
    'GBP/JPY': ('GBP', 'JPY'),
}


def _fetch_av_ohlc(pair, av_key):
    """
    Fetch 1-minute intraday OHLC from Alpha Vantage FX_INTRADAY.

    Uses outputsize=full which returns ~30 days of 1-min bars.
    Free tier: 25 calls/day, 5/min — one call per pair fetch.

    Returns list of (datetime_str, open, high, low, close, volume) tuples,
    sorted oldest-first, and a CSV bytes string suitable for ir.attachment.
    """
    from urllib.error import HTTPError

    symbols = _AV_PAIR_MAP.get(pair)
    if not symbols:
        raise ValueError(f"Unsupported pair: {pair}")

    from_sym, to_sym = symbols
    url = (
        f"https://www.alphavantage.co/query"
        f"?function=FX_INTRADAY"
        f"&from_symbol={from_sym}"
        f"&to_symbol={to_sym}"
        f"&interval=1min"
        f"&outputsize=full"
        f"&datatype=json"
        f"&apikey={av_key}"
    )

    req = urllib.request.Request(url, headers={"User-Agent": "TradingAI/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        raise RuntimeError(f"Alpha Vantage HTTP error {e.code}: {e.reason}")

    # Check for API error messages
    if "Error Message" in data:
        raise RuntimeError(f"Alpha Vantage error: {data['Error Message']}")
    if "Information" in data:
        raise RuntimeError(
            f"Alpha Vantage rate limit hit: {data['Information'][:200]}"
        )
    if "Note" in data:
        raise RuntimeError(
            f"Alpha Vantage limit: {data['Note'][:200]}"
        )

    ts_key = "Time Series FX (1min)"
    if ts_key not in data:
        raise RuntimeError(
            f"Unexpected Alpha Vantage response — keys: {list(data.keys())}"
        )

    series = data[ts_key]  # dict: { "2025-04-13 17:00:00": {open,high,low,close} }

    rows = []
    for dt_str, bar in series.items():
        try:
            o = float(bar["1. open"])
            h = float(bar["2. high"])
            l = float(bar["3. low"])
            c = float(bar["4. close"])
            rows.append((dt_str, o, h, l, c, 0.0))
        except (KeyError, ValueError):
            continue

    # Sort oldest → newest
    rows.sort(key=lambda r: r[0])

    # Build CSV content matching HistData format so existing parser works
    csv_lines = [f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}" for r in rows]
    csv_bytes = "\n".join(csv_lines).encode("utf-8")

    return rows, csv_bytes
