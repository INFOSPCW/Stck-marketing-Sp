from odoo import fields, models


class ResUsersSettings(models.Model):
    _inherit = "res.users.settings"

    home_menu_layout = fields.Json(string="Home Menu Layout", readonly=True)
