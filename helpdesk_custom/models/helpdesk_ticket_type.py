from odoo import fields, models


class HelpdeskTicketType(models.Model):
    _name = "helpdesk.ticket.type"
    _description = "Helpdesk Ticket Type"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    show_in_portal = fields.Boolean(default=True)
    team_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.team",
        string="Teams",
    )
