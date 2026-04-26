# Trading AI Advisor — Odoo 18 Module

## Overview

This module creates an AI-powered EUR/USD trading advisor inside Odoo 18.
It works in three stages:

```
📚 Books  →  🧠 Brain  →  📊 Price Data + 📰 News  →  ⚡ BUY / SELL / HOLD
```

1. **Brain Training** — reads all PDF books, extracts key trading wisdom via Claude AI
2. **Price Analysis** — loads your EUR/USD 1-minute historical data and computes
   RSI, MACD, EMA-20/50/200, Bollinger Bands
3. **News Analysis** — fetches the last 10 hours of EUR/USD news headlines
4. **Signal** — Claude synthesises all three and returns BUY / SELL / HOLD /
   INSUFFICIENT DATA with full reasoning, entry price, stop loss and take profit

---

## Installation

### 1. Copy the module

```bash
cp -r trading_ai_advisor /opt/odoo/custom/addons/
```

### 2. Copy your data files

```bash
# Books (PDFs)
mkdir -p /opt/odoo/custom/trading_ai_advisor/books
cp /path/to/your/Books/*.pdf /opt/odoo/custom/trading_ai_advisor/books/

# Stock data — extract the outer zip first, then place all inner zips or CSVs
mkdir -p /opt/odoo/custom/trading_ai_advisor/stock_data
# Option A: place the raw inner zips (module will unzip them automatically)
cp /path/to/Stock_data/HISTDATA_COM_ASCII_EURUSD_M1*.zip \
   /opt/odoo/custom/trading_ai_advisor/stock_data/
# Option B: extract them to CSV first (also works)
# cp DAT_ASCII_EURUSD_M1_*.csv /opt/odoo/custom/trading_ai_advisor/stock_data/
```

### 3. Install Python dependency

```bash
pip install pypdf --break-system-packages
# pypdf is the only extra dependency — pandas/numpy are NOT required
```

### 4. Update addons path in odoo.conf

```ini
[options]
addons_path = /opt/odoo/addons,/opt/odoo/custom/addons
```

### 5. Install in Odoo

```
Settings → Apps → Update App List → search "Trading AI Advisor" → Install
```

---

## Configuration

Go to **Trading AI → Configuration** (admin only):

| Setting | Description | Example |
|---|---|---|
| Anthropic API Key | From console.anthropic.com | sk-ant-... |
| Books Folder | Path to PDF books | /opt/odoo/custom/trading_ai_advisor/books |
| Stock Data Folder | Path to CSV/ZIP price files | /opt/odoo/custom/trading_ai_advisor/stock_data |
| Serper API Key | Optional — from serper.dev for news | abc123... |
| Price Lookback (hours) | How many hours of OHLC to analyse | 48 |
| News Lookback (hours) | How recent the news should be | 10 |

---

## Usage

### Step 1 — Train the Brain

1. Go to **Trading AI → AI Brain**
2. Click **New**
3. Click **🧠 Train Brain**
4. Wait 2–5 minutes while Claude reads all books
5. Status changes to **Ready** ✅

You only need to do this once (or when you add new books).

### Step 2 — Get a Signal

1. Open a trained brain (status = Ready)
2. Click **⚡ Get EUR/USD Signal**
3. The module will:
   - Load the latest price data from your CSV files
   - Compute all technical indicators
   - Fetch live EUR/USD news (if Serper key is configured)
   - Ask Claude to synthesise everything
4. A signal record opens showing **BUY / SELL / HOLD / INSUFFICIENT DATA**

### Step 3 — Read the Signal

Each signal shows:
- **Big coloured banner** — BUY (green), SELL (red), HOLD (yellow)
- **Confidence** — HIGH / MEDIUM / LOW
- **Entry Price, Stop Loss, Take Profit**
- **Risk/Reward Ratio**
- **Technical Indicators** — RSI, MACD, EMA 20/50/200
- **Price Analysis** — what the technicals say
- **News Analysis** — what the news says
- **Book Wisdom** — which book principles apply
- **Conflicts** — where sources disagree
- **Full Reasoning** — complete AI rationale
- **Risk Warning** — always read this

---

## Signal Confidence Rules

| Confidence | Meaning |
|---|---|
| HIGH | Technicals + News + Book principles all agree |
| MEDIUM | 2 out of 3 sources agree |
| LOW | Sources conflict, or limited data |
| INSUFFICIENT DATA | Cannot determine a signal — do not trade |

---

## Data Sources

### Books indexed (your uploads)
- The Intelligent Investor — Benjamin Graham
- Contrarian Investment Strategies — David Dreman
- Thinking, Fast and Slow — Daniel Kahneman
- The Most Important Thing — Howard Marks
- The Big Short — Michael Lewis
- Margin of Safety (30 ideas) — Seth Klarman
- The Acquirer's Multiple — Tobias Carlisle
- Beat the Market
- Michael Mauboussin Research Articles
- Contrarian strategy report

### Price data
- HistData.com 1-minute OHLC data
- 2015–2026 (your uploads cover this range)
- Format: `YYYYMMDD HHMMSS;Open;High;Low;Close;Volume`

### News
- Serper.dev Google News API
- Searches "EUR/USD forex" for last N hours
- Optional — module works without it (lower confidence)

---

## Indicators Computed

| Indicator | Period | Use |
|---|---|---|
| RSI | 14 bars | Overbought (>70) / Oversold (<30) |
| MACD | 12/26/9 | Momentum and trend direction |
| EMA | 20, 50, 200 | Short/medium/long trend |
| Bollinger Bands | 20, 2σ | Volatility and mean reversion |
| 24h High/Low | 1440 bars | Range context |
| Trend Slope | 20 bars | Recent momentum % |

---

## Architecture

```
trading_ai_advisor/
├── __manifest__.py          # Module metadata
├── __init__.py
├── models/
│   ├── trading_brain.py     # Core AI engine
│   │   ├── _extract_pdf_text()      — pypdf text extraction
│   │   ├── _summarise_book()        — Claude per-book summary
│   │   ├── _compute_indicators()    — Pure Python TA
│   │   ├── _load_eurusd_rows()      — CSV/ZIP data loader
│   │   ├── _fetch_news()            — Serper news API
│   │   └── _ask_claude_for_advice() — Final BUY/SELL call
│   ├── trading_signal.py    # Signal storage model
│   └── trading_config.py    # Configuration (ir.config_parameter)
├── views/
│   ├── trading_brain_views.xml
│   ├── trading_signal_views.xml
│   └── menus.xml
├── security/
│   └── ir.model.access.csv
└── static/src/
    ├── css/trading.css
    └── js/trading_dashboard.js
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| "No PDF files found" | Check books_folder path is correct and contains .pdf files |
| "Insufficient price data" | Check stock_data_folder; module reads .csv, .txt, and .zip files recursively |
| "API call failed" | Check Anthropic API key is valid and has credits |
| Brain stuck in "Training" | Refresh page; check Odoo logs for errors |
| No news shown | Serper API key not configured — signal still works but uses LOW confidence |
| Signal is INSUFFICIENT DATA | Not enough price bars, or all 3 sources conflict — do not trade |

---

## Risk Disclaimer

⚠️ **This module is for educational and research purposes only.**
Trading forex involves substantial risk of loss. Past performance does not guarantee
future results. Never risk money you cannot afford to lose.
Always consult a qualified financial advisor before making trading decisions.
