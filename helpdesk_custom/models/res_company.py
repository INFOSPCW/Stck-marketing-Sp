from odoo import fields, models


class Company(models.Model):
    _inherit = "res.company"

    # From helpdesk_mgmt
    helpdesk_mgmt_portal_select_team = fields.Boolean(string="Select team in Helpdesk portal")
    helpdesk_mgmt_portal_team_id_required = fields.Boolean(
        string="Required Team field in Helpdesk portal", default=True
    )
    helpdesk_mgmt_portal_select_category = fields.Boolean(string="Select category in Helpdesk portal")
    helpdesk_mgmt_portal_category_id_required = fields.Boolean(
        string="Required Category field in Helpdesk portal", default=True
    )
    helpdesk_mgmt_duplicate_tracking = fields.Boolean(
        string="Enable duplicate ticket tracking", default=False
    )
    helpdesk_mgmt_duplicate_ticket_stage_id = fields.Many2one(
        comodel_name="helpdesk.ticket.stage",
        string="Move duplicate tickets to this stage",
        default=False,
    )
    helpdesk_mgmt_ticket_auto_assign = fields.Boolean(
        string="Auto assign tickets", default=True
    )
    # From helpdesk_type
    helpdesk_mgmt_portal_type = fields.Boolean(string="Show ticket type in portal")
    helpdesk_mgmt_portal_type_id_required = fields.Boolean(string="Require ticket type in portal")
    # From helpdesk_mgmt_project_domain
    helpdesk_mgmt_project_domain = fields.Char(
        string="Global Project Domain",
        help="Global domain to filter projects available for selection in tickets.",
    )
    helpdesk_mgmt_task_domain = fields.Char(
        string="Global Task Domain",
        help="Global domain to filter tasks available for selection in tickets.",
    )
