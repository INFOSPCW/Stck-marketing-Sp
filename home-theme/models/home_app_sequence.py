# -*- coding: utf-8 -*-

from odoo import models, fields, api


class HomeAppSequence(models.Model):
    _name = 'home.app.sequence'
    _description = 'Home Screen App Sequence'
    _order = 'sequence, id'

    user_id = fields.Many2one('res.users', string='User', required=True, ondelete='cascade', index=True)
    menu_id = fields.Many2one('ir.ui.menu', string='Menu/App', required=True, ondelete='cascade')
    sequence = fields.Integer(string='Sequence', default=10)

    _sql_constraints = [
        ('user_menu_unique', 'unique(user_id, menu_id)', 'Each app can only have one sequence per user!')
    ]
