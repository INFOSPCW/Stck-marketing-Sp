# -*- coding: utf-8 -*-
from odoo import models, fields, api

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

CONFIDENCE_COLORS = {'HIGH': 10, 'MEDIUM': 2, 'LOW': 1}


class CryptoSignal(models.Model):
    _name        = 'trading.crypto_signal'
    _description = 'Crypto Trading Signal'
    _order       = 'create_date desc'
    _inherit     = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Reference', compute='_compute_name', store=True)
    pair = fields.Selection(CRYPTO_PAIRS, string='Pair', required=True, default='BTC/USDT')
    brain_id    = fields.Many2one('trading.crypto_brain', string='Brain Used', ondelete='set null')
    create_date = fields.Datetime(string='Generated At', readonly=True)

    signal = fields.Selection([
        ('BUY',               '⬆ BUY'),
        ('SELL',              '⬇ SELL'),
        ('HOLD',              '➡ HOLD'),
        ('INSUFFICIENT DATA', '? Insufficient Data'),
    ], string='Signal', required=True, default='INSUFFICIENT DATA')

    confidence = fields.Selection([
        ('HIGH', 'HIGH'), ('MEDIUM', 'MEDIUM'), ('LOW', 'LOW'),
    ], string='Confidence', default='LOW')

    color = fields.Integer(compute='_compute_color', store=True)

    # Price
    current_price = fields.Float(string='Price at Signal', digits=(16, 6))
    entry_price   = fields.Float(string='Suggested Entry', digits=(16, 6))
    stop_loss     = fields.Float(string='Stop Loss',       digits=(16, 6))
    take_profit   = fields.Float(string='Take Profit',     digits=(16, 6))
    risk_reward   = fields.Float(string='Risk/Reward', compute='_compute_risk_reward', store=True)

    # Indicators
    rsi           = fields.Float(string='RSI (14)',    digits=(6, 2))
    macd          = fields.Float(string='MACD',        digits=(16, 8))
    ema_20        = fields.Float(string='EMA 20',      digits=(16, 6))
    ema_50        = fields.Float(string='EMA 50',      digits=(16, 6))
    ema_200       = fields.Float(string='EMA 200',     digits=(16, 6))
    bars_analysed = fields.Integer(string='Bars Analysed')
    news_count    = fields.Integer(string='News Articles')

    # AI analysis
    price_analysis = fields.Text(string='Price / Technical Analysis')
    news_analysis  = fields.Text(string='News Sentiment Analysis')
    book_wisdom    = fields.Text(string='Book Wisdom Applied')
    conflicts      = fields.Text(string='Conflicting Signals')
    reasoning      = fields.Text(string='Full Reasoning')
    risk_warning   = fields.Text(string='Risk Warning')
    raw_response   = fields.Text(string='Raw AI Response (JSON)')

    @api.depends('signal', 'pair', 'create_date')
    def _compute_name(self):
        for rec in self:
            ts   = fields.Datetime.to_string(rec.create_date) if rec.create_date else 'Draft'
            rec.name = f"{rec.pair or 'N/A'} {rec.signal} — {ts}"

    @api.depends('confidence')
    def _compute_color(self):
        for rec in self:
            rec.color = CONFIDENCE_COLORS.get(rec.confidence, 0)

    @api.depends('entry_price', 'stop_loss', 'take_profit')
    def _compute_risk_reward(self):
        for rec in self:
            if rec.entry_price and rec.stop_loss and rec.take_profit:
                risk   = abs(rec.entry_price - rec.stop_loss)
                reward = abs(rec.take_profit - rec.entry_price)
                rec.risk_reward = round(reward / risk, 2) if risk > 0 else 0
            else:
                rec.risk_reward = 0
