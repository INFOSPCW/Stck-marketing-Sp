from odoo import fields, models


class HelpdeskTicketMotive(models.Model):
    _name = "helpdesk.ticket.motive"
    _description = "Helpdesk Ticket Motive"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    team_id = fields.Many2one(
        comodel_name="helpdesk.ticket.team",
        string="Team",
        ondelete="cascade",
    )
