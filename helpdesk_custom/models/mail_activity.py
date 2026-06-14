from odoo import fields, models


class MailActivity(models.Model):
    _inherit = "mail.activity"

    ticket_id = fields.Many2one(
        comodel_name="helpdesk.ticket",
        help="Activity created from helpdesk ticket. After closing this activity, ticket is moved to done stage.",
    )

    def _action_done(self, feedback=False, attachment_ids=None):
        for ticket in self.ticket_id:
            if ticket.team_id and ticket.team_id.activity_stage_id:
                ticket.stage_id = ticket.team_id.activity_stage_id.id
        return super()._action_done(feedback, attachment_ids)
