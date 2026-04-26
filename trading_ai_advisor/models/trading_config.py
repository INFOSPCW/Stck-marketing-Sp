# -*- coding: utf-8 -*-
from odoo import models, fields, api


class TradingConfig(models.Model):
    _name = 'trading.config'
    _description = 'Trading AI Advisor — Configuration'

    anthropic_api_key     = fields.Char(string='Anthropic API Key',
                                         help='From console.anthropic.com')
    serper_api_key        = fields.Char(string='Serper API Key (News)',
                                         help='Optional — from serper.dev')
    alpha_vantage_api_key = fields.Char(string='Alpha Vantage API Key',
                                         help='Free key from alphavantage.co (used by Forex Brain live fetch)')
    twelve_data_api_key   = fields.Char(string='Twelve Data API Key',
                                         help='Free key from twelvedata.com — used by Daily Analysis for forex. '
                                              '800 calls/day free (vs 25 for Alpha Vantage).')
    news_hours = fields.Integer(string='News Lookback (hours)', default=10)

    @api.model
    def _get_icp(self):
        return self.env['ir.config_parameter'].sudo()

    @api.model
    def get_singleton(self):
        icp  = self._get_icp()
        vals = {
            'anthropic_api_key':     icp.get_param('trading_ai.anthropic_key', ''),
            'serper_api_key':        icp.get_param('trading_ai.serper_key', ''),
            'alpha_vantage_api_key': icp.get_param('trading_ai.alpha_vantage_key', ''),
            'twelve_data_api_key':   icp.get_param('trading_ai.twelve_data_key', ''),
            'news_hours':            int(icp.get_param('trading_ai.news_hours', 10)),
        }
        record = self.sudo().search([], limit=1)
        if record:
            record.sudo().write(vals)
        else:
            record = self.sudo().create(vals)
        return record

    @api.model
    def get_config(self):
        icp = self._get_icp()
        return {
            'anthropic_api_key':     icp.get_param('trading_ai.anthropic_key', ''),
            'serper_api_key':        icp.get_param('trading_ai.serper_key', ''),
            'alpha_vantage_api_key': icp.get_param('trading_ai.alpha_vantage_key', ''),
            'twelve_data_api_key':   icp.get_param('trading_ai.twelve_data_key', ''),
            'news_hours':            int(icp.get_param('trading_ai.news_hours', 10)),
        }

    def action_save_config(self):
        self.ensure_one()
        icp = self._get_icp()
        icp.set_param('trading_ai.anthropic_key',      self.anthropic_api_key or '')
        icp.set_param('trading_ai.serper_key',         self.serper_api_key or '')
        icp.set_param('trading_ai.alpha_vantage_key',  self.alpha_vantage_api_key or '')
        icp.set_param('trading_ai.twelve_data_key',    self.twelve_data_api_key or '')
        icp.set_param('trading_ai.news_hours',         str(self.news_hours or 10))
        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   '✅ Configuration Saved',
                'message': 'All API keys stored permanently.',
                'sticky':  False, 'type': 'success',
            },
        }
