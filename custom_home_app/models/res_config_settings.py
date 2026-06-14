from odoo import models, fields, api

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    is_enterprise_installed = fields.Boolean(string='Is Enterprise Installed', config_parameter='custom_home_app.is_enterprise_installed')

    home_menu_time_format = fields.Selection([
        ('12', '12 Hour (AM/PM)'),
        ('24', '24 Hour')
    ], string='Time Format', config_parameter='custom_home_app.time_format', default='24')
    
    home_menu_clock_design = fields.Selection([
        ('classic', 'Classic (Default)'),
        ('flip', 'Flip Clock'),
        ('modern', 'Modern Stacked')
    ], string='Clock Design', config_parameter='custom_home_app.clock_design', default='classic')

    color = fields.Integer(related='company_id.color', readonly=False, string="Clock Color")

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        enterprise_module = self.env['ir.module.module'].search([('name', '=', 'web_enterprise'), ('state', '=', 'installed')])
        res.update(
            is_enterprise_installed=bool(enterprise_module),
        )
        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()
