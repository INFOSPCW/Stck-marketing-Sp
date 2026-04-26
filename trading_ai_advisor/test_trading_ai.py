#!/usr/bin/env python3
"""
test_trading_ai.py
==================
Standalone test — run this OUTSIDE Odoo to verify the AI engine
works with your books and stock data before installing the module.

Usage:
    python3 test_trading_ai.py \
        --api-key sk-ant-YOUR_KEY \
        --books  /path/to/books \
        --data   /path/to/stock_data \
        --serper YOUR_SERPER_KEY   # optional
"""

import sys
import os
import argparse
import json

# ── Make the models importable without Odoo ──────────────────────────────────
# We import only the pure-Python helper functions from trading_brain.py
sys.path.insert(0, os.path.dirname(__file__))

# Re-implement lightweight stubs so the imports work without Odoo
class _FakeOdoo:
    pass

# Directly import helpers by exec-ing just the function definitions
import ast, types

def _load_helpers(path):
    """Load only the module-level functions from trading_brain.py."""
    with open(path) as f:
        src = f.read()
    mod = types.ModuleType('trading_brain_helpers')
    # Strip the Odoo model class (keep only top-level functions)
    lines = []
    in_class = False
    for line in src.splitlines():
        if line.startswith('class Trading'):
            in_class = True
        if in_class and (line.startswith('class ') and not line.startswith('class Trading')):
            in_class = False
        if not in_class:
            lines.append(line)
    clean = '\n'.join(lines)
    exec(compile(clean, path, 'exec'), mod.__dict__)
    return mod

HERE = os.path.dirname(os.path.abspath(__file__))
helpers_path = os.path.join(HERE, 'models', 'trading_brain.py')

try:
    h = _load_helpers(helpers_path)
    print("✅ Helpers loaded successfully")
except Exception as e:
    print(f"❌ Failed to load helpers: {e}")
    sys.exit(1)


def test_price_data(data_folder, lookback=48):
    print(f"\n{'='*60}")
    print("TEST: Loading EUR/USD price data")
    print(f"{'='*60}")
    rows = h._load_eurusd_rows(data_folder, lookback_hours=lookback)
    print(f"  Rows loaded: {len(rows)}")
    if rows:
        print(f"  Earliest bar: {rows[0][0]}")
        print(f"  Latest bar:   {rows[-1][0]}")
        print(f"  Last close:   {rows[-1][4]}")

        indicators = h._compute_indicators(rows)
        print(f"\n  Technical Indicators:")
        for k, v in indicators.items():
            print(f"    {k:30s}: {v}")
        return rows, indicators
    else:
        print("  ⚠  No data loaded — check folder path")
        return [], {}


def test_news(serper_key, hours=10):
    print(f"\n{'='*60}")
    print("TEST: Fetching EUR/USD news")
    print(f"{'='*60}")
    if not serper_key:
        print("  ℹ  No Serper key provided — skipping")
        return []
    items = h._fetch_news("EUR/USD forex", hours, serper_key)
    print(f"  Found {len(items)} articles:")
    for item in items[:5]:
        print(f"  • [{item['date']}] {item['title']}")
    return items


def test_books(books_folder, api_key):
    print(f"\n{'='*60}")
    print("TEST: Summarising books")
    print(f"{'='*60}")
    import glob
    pdfs = glob.glob(os.path.join(books_folder, '**', '*.pdf'), recursive=True)
    print(f"  Found {len(pdfs)} PDFs")

    summaries = []
    for fpath in pdfs[:2]:  # Test with first 2 only to save tokens
        title = os.path.splitext(os.path.basename(fpath))[0]
        print(f"  Extracting: {title}")
        text = h._extract_pdf_text(fpath)
        print(f"    Extracted {len(text)} chars")
        if len(text) > 200:
            print(f"    Summarising with Claude…")
            summary = h._summarise_book(title, text, api_key)
            print(f"    Summary ({len(summary)} chars): {summary[:200]}…")
            summaries.append(f"=== {title} ===\n{summary}")
    return "\n\n".join(summaries)


def test_full_signal(api_key, books_folder, data_folder, serper_key):
    print(f"\n{'='*60}")
    print("FULL TEST: Complete signal generation")
    print(f"{'='*60}")

    # 1. Brain (abbreviated — just 1 book for speed)
    import glob
    pdfs = glob.glob(os.path.join(books_folder, '**', '*.pdf'), recursive=True)
    brain_parts = []
    for fpath in pdfs[:3]:
        title = os.path.splitext(os.path.basename(fpath))[0]
        text = h._extract_pdf_text(fpath)
        if len(text) > 200:
            s = h._summarise_book(title, text, api_key)
            brain_parts.append(f"=== {title} ===\n{s}")
    brain = "\n\n".join(brain_parts)
    print(f"  Brain built from {len(brain_parts)} books ({len(brain)} chars)")

    # 2. Price data + indicators
    rows, indicators = test_price_data(data_folder, lookback=48)
    if not rows:
        print("  ❌ No price data — aborting signal test")
        return

    recent_20 = rows[-20:]
    ohlc_str = "\n".join(
        f"{r[0]} | O:{r[1]:.5f} H:{r[2]:.5f} L:{r[3]:.5f} C:{r[4]:.5f}"
        for r in recent_20
    )

    # 3. News
    news = h._fetch_news("EUR/USD forex", 10, serper_key) if serper_key else []

    # 4. Ask Claude
    print(f"\n  Asking Claude for signal…")
    result = h._ask_claude_for_advice(
        brain_summary=brain,
        indicators=indicators,
        recent_ohlc_str=ohlc_str,
        news_items=news,
        api_key=api_key,
        news_hours=10,
    )

    print(f"\n{'='*60}")
    print("  ⚡ SIGNAL RESULT")
    print(f"{'='*60}")
    print(f"  Signal:     {result.get('signal')}")
    print(f"  Confidence: {result.get('confidence')}")
    print(f"  Entry:      {result.get('entry_price')}")
    print(f"  Stop Loss:  {result.get('stop_loss')}")
    print(f"  Take Profit:{result.get('take_profit')}")
    print(f"\n  Reasoning:\n{result.get('reasoning', '')}")
    print(f"\n  Risk Warning:\n{result.get('risk_warning', '')}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Test Trading AI Advisor')
    parser.add_argument('--api-key', required=True, help='Anthropic API key')
    parser.add_argument('--books', required=True, help='Path to books folder')
    parser.add_argument('--data', required=True, help='Path to stock data folder')
    parser.add_argument('--serper', default='', help='Serper API key (optional)')
    parser.add_argument('--test', choices=['price', 'news', 'books', 'full'],
                        default='full', help='Which test to run')
    args = parser.parse_args()

    if args.test == 'price':
        test_price_data(args.data)
    elif args.test == 'news':
        test_news(args.serper)
    elif args.test == 'books':
        test_books(args.books, args.api_key)
    else:
        test_full_signal(args.api_key, args.books, args.data, args.serper)
