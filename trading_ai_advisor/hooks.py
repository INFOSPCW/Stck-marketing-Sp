# -*- coding: utf-8 -*-
"""
hooks.py
========
post_init_hook  — runs on fresh module install (-i)
_load_seed_data — loads bundled JSON data from seed_data/; also called
                  from the 19.0.25.33.0 migration script on upgrade (-u)
"""
import os
import json
import logging

_logger = logging.getLogger(__name__)

VALID_KEYS = [
    'EUR/USD','GBP/USD','USD/JPY','AUD/USD','USD/CAD','USD/CHF','NZD/USD','USD/SGD',
    'GBP/JPY','EUR/JPY','AUD/JPY','EUR/GBP','USD/NOK','GBP/CHF','USD/ZAR','USD/MXN',
    'XAU/USD','EUR/CAD',
    'DIA','SPY','QQQ','EWG',
    'BTC/USDT','ETH/USDT','SOL/USDT','XRP/USDT','BNB/USDT',
    'AAPL','TSLA','NVDA','MSFT','AMZN','META','GOOGL',
    'CL=F','BZ=F','NG=F','SI=F','GC=F','HG=F','PL=F','ZW=F','ZC=F','KC=F',
]

_SEED_DIR = os.path.join(os.path.dirname(__file__), 'seed_data')


def post_init_hook(env):
    """Runs after fresh module install."""
    _cleanup_stale_instruments(env)
    _load_seed_data(env)
    _check_yfinance()


# ─────────────────────────────────────────────────────────────────────────────
# Seed data loader  (called from both hook and migration script)
# ─────────────────────────────────────────────────────────────────────────────

def _load_seed_data(env):
    """Load bundled JSON files from seed_data/ if the database is empty."""
    if not os.path.isdir(_SEED_DIR):
        _logger.info("seed_data/ not found — skipping seed load")
        return

    # Guard: skip if the trade log already has records (data already migrated)
    if env['trading.trade_log'].sudo().search_count([]) > 0:
        _logger.info("Seed data: trade log already populated — skipping")
        return

    _logger.info("Seed data: loading bundled data into empty database…")

    def _read(filename):
        path = os.path.join(_SEED_DIR, filename)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    # Instruments
    data = _read('instruments.json')
    if data:
        _logger.info("Seed: loading instruments…")
        _seed_instruments(env, data)

    # Trade journal
    data = _read('trade_log.json')
    if data:
        _logger.info("Seed: loading %d trade log entries…", len(data))
        _seed_trade_log(env, data)

    # Daily results
    data = _read('daily_results.json')
    if data:
        _logger.info("Seed: loading %d daily results…", len(data))
        _seed_daily_results(env, data)

    # Analysis sessions
    data = _read('daily_analyses.json')
    if data:
        _logger.info("Seed: loading %d analysis sessions…", len(data))
        _seed_daily_analyses(env, data)

    # AI Rulebook
    data = _read('rulebook.json')
    if data:
        _logger.info("Seed: loading %d rulebook entries…", len(data))
        _seed_rulebook(env, data)

    # Cortex
    data = _read('cortex.json')
    if data:
        _logger.info("Seed: loading cortex…")
        _seed_cortex(env, data)

    # Simulator
    data = _read('simulator.json')
    if data:
        _logger.info("Seed: loading simulator…")
        _seed_simulator(env, data)

    # System logs
    data = _read('system_logs.json')
    if data:
        _logger.info("Seed: loading %d system log entries…", len(data))
        _seed_system_logs(env, data)

    # Knowledge library
    data = _read('library_summaries.json')
    if data:
        _logger.info("Seed: loading knowledge library…")
        _seed_library(env, data)

    # Config
    data = _read('config.json')
    if data:
        _logger.info("Seed: loading automation config…")
        _seed_config(env, data)

    _logger.info("Seed data load complete.")


# ─────────────────────────────────────────────────────────────────────────────
# Dataset loaders
# ─────────────────────────────────────────────────────────────────────────────

def _seed_instruments(env, records):
    existing = {r.instrument_key for r in env['trading.daily_instrument'].sudo().search([])}
    for r in records:
        key = r.get('instrument_key', '')
        if not key or key in existing:
            continue
        try:
            env['trading.daily_instrument'].sudo().create({
                'instrument_key': key,
                'active':         bool(r.get('active', True)),
                'sequence':       int(r.get('sequence') or 10),
            })
        except Exception as e:
            _logger.warning("Seed instrument skipped %s: %s", key, e)


def _seed_trade_log(env, records):
    created = 0
    for r in records:
        try:
            env['trading.trade_log'].sudo().create({
                'trade_date':       r.get('trade_date'),
                'instrument':       (r.get('instrument') or '')[:50],
                'direction':        r.get('direction', 'BUY'),
                'entry_price':      float(r.get('entry_price') or 0),
                'exit_price':       float(r.get('exit_price') or 0),
                'stop_loss':        float(r.get('stop_loss') or 0),
                'take_profit':      float(r.get('take_profit') or 0),
                'lot_size':         float(r.get('lot_size') or 0),
                'outcome':          r.get('outcome', 'LOSS'),
                'pnl':              float(r.get('pnl') or 0),
                'mistake_category': r.get('mistake_category', 'other'),
                'what_went_wrong':  r.get('what_went_wrong', ''),
                'lesson_learned':   r.get('lesson_learned', ''),
            })
            created += 1
        except Exception as e:
            _logger.warning("Seed trade_log row skipped: %s", e)
    _logger.info("Seed: %d trade log records created", created)


def _seed_daily_results(env, records):
    created = 0
    # Batch insert — skip individual savepoints for speed; wrap whole set
    for r in records:
        try:
            env['trading.daily_result'].sudo().create({
                'analysis_date':      r.get('analysis_date'),
                'instrument':         r.get('instrument', ''),
                'inst_type':          r.get('inst_type', ''),
                'signal':             r.get('signal', 'WAIT'),
                'score':              float(r.get('score') or 0),
                'confidence':         r.get('confidence', ''),
                'current_price':      float(r.get('current_price') or 0),
                'entry_price':        float(r.get('entry_price') or 0),
                'stop_loss':          float(r.get('stop_loss') or 0),
                'take_profit':        float(r.get('take_profit') or 0),
                'r_r_ratio':          float(r.get('r_r_ratio') or 0),
                'hold_overnight_ai':  bool(r.get('hold_overnight_ai', False)),
                'rsi':                float(r.get('rsi') or 0),
                'macd':               float(r.get('macd') or 0),
                'ema_20':             float(r.get('ema_20') or 0),
                'ema_50':             float(r.get('ema_50') or 0),
                'ema_200':            float(r.get('ema_200') or 0),
                'best_open_time':     r.get('best_open_time', ''),
                'best_open_time_nl':  r.get('best_open_time_nl', ''),
                'best_close_time':    r.get('best_close_time', ''),
                'best_close_time_nl': r.get('best_close_time_nl', ''),
                'session_advice':     r.get('session_advice', ''),
                'reasoning':          r.get('reasoning', ''),
                'risk_warning':       r.get('risk_warning', ''),
                'raw_response':       r.get('raw_response', ''),
                'fib_signal':         r.get('fib_signal', ''),
                'fib_strength':       float(r.get('fib_strength') or 0),
                'fib_up_1618':        float(r.get('fib_up_1618') or 0),
                'fib_dn_1618':        float(r.get('fib_dn_1618') or 0),
                'fib_up_2618':        float(r.get('fib_up_2618') or 0),
                'fib_dn_2618':        float(r.get('fib_dn_2618') or 0),
                'fib_range_bound':    bool(r.get('fib_range_bound', False)),
                'fib_setup':          r.get('fib_setup', ''),
            })
            created += 1
        except Exception as e:
            _logger.warning("Seed daily_result row skipped: %s", e)
    _logger.info("Seed: %d daily result records created", created)


def _seed_daily_analyses(env, records):
    created = 0
    for r in records:
        try:
            vals = {
                'name':          r.get('name', ''),
                'analysis_date': r.get('analysis_date'),
                'state':         r.get('state', 'done'),
                'run_log':       r.get('run_log', ''),
            }
            for opt in ('session_label', 'top_opportunity'):
                if r.get(opt):
                    vals[opt] = r[opt]
            env['trading.daily_analysis'].sudo().create(vals)
            created += 1
        except Exception as e:
            _logger.warning("Seed daily_analysis row skipped: %s", e)
    _logger.info("Seed: %d analysis session records created", created)


def _seed_rulebook(env, records):
    created = 0
    for r in records:
        rule_text = r.get('rule_text', '')
        if not rule_text:
            continue
        try:
            env['trading.ai_rulebook'].sudo().create({
                'name':            r.get('name', rule_text[:80]),
                'rule_type':       r.get('rule_type', 'global'),
                'instrument':      r.get('instrument', ''),
                'category':        r.get('category', 'technical'),
                'rule_text':       rule_text,
                'confidence':      int(r.get('confidence') or 5),
                'times_triggered': int(r.get('times_triggered') or 0),
                'active':          bool(r.get('active', True)),
            })
            created += 1
        except Exception as e:
            _logger.warning("Seed rulebook row skipped: %s", e)
    _logger.info("Seed: %d rulebook records created", created)


def _seed_cortex(env, data):
    try:
        cortex = env['trading.cortex'].sudo().get_singleton()
        for l in data.get('lessons', []):
            lt = l.get('lesson_text', '')
            if not lt:
                continue
            try:
                env['trading.cortex.lesson'].sudo().create({
                    'cortex_id':    cortex.id,
                    'lesson_type':  l.get('lesson_type', 'global'),
                    'instrument':   l.get('instrument', ''),
                    'session':      l.get('session', ''),
                    'lesson_text':  lt[:500],
                    'evidence':     l.get('evidence', 'Imported'),
                    'confidence':   int(l.get('confidence') or 5),
                    'active':       bool(l.get('active', True)),
                })
            except Exception as e:
                _logger.warning("Seed cortex lesson skipped: %s", e)
    except Exception as e:
        _logger.warning("Seed cortex skipped: %s", e)


def _seed_simulator(env, data):
    if not data or data.get('error'):
        return
    try:
        sim = env['trading.simulator'].sudo().search([('state', '=', 'active')], limit=1)
        if not sim:
            sim = env['trading.simulator'].sudo().create({
                'name':             data.get('simulator_name', 'Simulator'),
                'starting_balance': float(data.get('starting_balance') or 10000),
                'current_balance':  float(data.get('current_balance') or 10000),
                'risk_per_trade':   float(data.get('risk_per_trade') or 1.0),
                'state':            'active',
            })
        created = 0
        for p in data.get('positions', []):
            try:
                env['trading.simulator.position'].sudo().create({
                    'simulator_id':       sim.id,
                    'instrument':         p.get('instrument', ''),
                    'inst_type':          p.get('inst_type', ''),
                    'direction':          p.get('direction', 'BUY'),
                    'entry_price':        float(p.get('entry_price') or 0),
                    'current_price':      float(p.get('current_price') or 0),
                    'exit_price':         float(p.get('exit_price') or 0),
                    'stop_loss':          float(p.get('stop_loss') or 0),
                    'take_profit':        float(p.get('take_profit') or 0),
                    'position_size_usd':  float(p.get('position_size_usd') or 0),
                    'open_time':          p.get('open_time'),
                    'exit_time':          p.get('exit_time') or False,
                    'state':              p.get('state', 'closed'),
                    'outcome':            p.get('outcome', ''),
                    'pnl_usd':            float(p.get('pnl_usd') or 0),
                    'pnl_pct':            float(p.get('pnl_pct') or 0),
                    'risk_reward_actual': float(p.get('risk_reward_actual') or 0),
                    'close_reason':       p.get('close_reason', ''),
                    'ai_score':           float(p.get('ai_score') or 0),
                    'ai_confidence':      p.get('ai_confidence', ''),
                    'ai_reasoning':       p.get('ai_reasoning', ''),
                })
                created += 1
            except Exception as e:
                _logger.warning("Seed simulator position skipped: %s", e)
        _logger.info("Seed: %d simulator positions created", created)
    except Exception as e:
        _logger.warning("Seed simulator skipped: %s", e)


def _seed_system_logs(env, records):
    created = 0
    for r in records:
        if not r.get('message'):
            continue
        try:
            env['trading.system_log'].sudo().create({
                'level':      r.get('level', 'info'),
                'category':   r.get('category', 'system'),
                'message':    r.get('message', ''),
                'detail':     r.get('detail', ''),
                'instrument': r.get('instrument', ''),
            })
            created += 1
        except Exception as e:
            _logger.warning("Seed system_log row skipped: %s", e)
    _logger.info("Seed: %d system log records created", created)


def _seed_library(env, records):
    created = 0
    for lib_data in records:
        cat  = lib_data.get('category')
        name = lib_data.get('name', '')
        if not cat or not name:
            continue
        try:
            library = env['trading.knowledge.library'].sudo().search([
                ('category', '=', cat), ('name', '=', name),
            ], limit=1)
            if not library:
                library = env['trading.knowledge.library'].sudo().create({
                    'name':             name,
                    'category':         cat,
                    'description':      lib_data.get('description', ''),
                    'combined_summary': lib_data.get('combined_summary', ''),
                })
                created += 1
            for book_data in lib_data.get('books', []):
                bname = book_data.get('name', '')
                if not bname or not book_data.get('summary'):
                    continue
                exists = env['trading.knowledge.book'].sudo().search(
                    [('library_id', '=', library.id), ('name', '=', bname)], limit=1)
                if not exists:
                    env['trading.knowledge.book'].sudo().create({
                        'library_id':      library.id,
                        'name':            bname,
                        'summary':         book_data.get('summary', ''),
                        'key_concepts':    book_data.get('key_concepts', ''),
                        'applicable_to':   book_data.get('applicable_to', ''),
                        'indexed':         bool(book_data.get('indexed', False)),
                        'pages_processed': int(book_data.get('pages_processed') or 0),
                    })
        except Exception as e:
            _logger.warning("Seed library row skipped: %s", e)
    _logger.info("Seed: %d library records created", created)


def _seed_config(env, data):
    try:
        config = env['trading.automation'].sudo().search([], limit=1)
        if config:
            vals = {}
            for field in ('skip_weekends', 'min_score_to_trade', 'risk_per_trade'):
                if field in data:
                    vals[field] = data[field]
            if vals:
                config.write(vals)
    except Exception as e:
        _logger.warning("Seed config skipped: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Cleanup helpers (pre-existing logic)
# ─────────────────────────────────────────────────────────────────────────────

def _cleanup_stale_instruments(env):
    REMAP = {
        'DAX': 'EWG', 'DJI': 'DIA', 'SPX': 'SPY', 'NDX': 'QQQ',
        'GER40': 'EWG', 'US30': 'DIA', 'SPX500': 'SPY', 'NAS100': 'QQQ',
        'XAG/USD': 'XAU/USD', 'WTI/USD': 'XAU/USD', 'XNG/USD': 'XAU/USD',
        'EUR/CHF': 'USD/NOK', 'NZD/JPY': 'USD/ZAR', 'AUD/CAD': 'EUR/CAD',
    }
    for old_sym, new_sym in REMAP.items():
        env.cr.execute(
            "UPDATE trading_sim_position SET instrument = %s WHERE instrument = %s",
            (new_sym, old_sym))
        env.cr.execute(
            "UPDATE trading_trade_log SET instrument = %s WHERE instrument = %s",
            (new_sym, old_sym))

    stale = env['trading.daily_instrument'].sudo().search(
        [('instrument_key', 'not in', VALID_KEYS)])
    if stale:
        _logger.info("Removing %d stale instrument records", len(stale))
        stale.unlink()

    try:
        for analysis in env['trading.daily_analysis'].sudo().search([]):
            stale_linked = analysis.instrument_ids.filtered(
                lambda i: i.instrument_key not in VALID_KEYS)
            if stale_linked:
                analysis.instrument_ids = [(3, inst.id) for inst in stale_linked]
    except Exception as e:
        _logger.warning("Could not clean analysis instrument_ids: %s", e)


def _check_yfinance():
    try:
        import yfinance  # noqa
        _logger.info("yfinance is ready.")
    except ImportError:
        _logger.warning(
            "yfinance is NOT installed. US stock signals will fail. "
            "Fix: pip install yfinance --break-system-packages")
