import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.osv import expression
from odoo.tools.safe_eval import safe_eval, test_python_expr
from odoo.tools.safe_eval import safe_eval as _safe_eval

_logger = logging.getLogger(__name__)

DEFAULT_PYTHON_CODE = """# Available variables:
#  - ticket: Current helpdesk ticket record
#  - env: Odoo Environment
#  - user: Current user
#  - company: Current company
#  - AND, OR: Domain operators from odoo.osv.expression
#  - normalize: Function to normalize domain lists
#  - _: Translation function
#
# Example:
# if ticket.partner_id:
#     domain = [('commercial_partner_id', '=', ticket.commercial_partner_id.id)]

"""

DEFAULT_TASK_PYTHON_CODE = """# Available variables:
#  - ticket: Current helpdesk ticket record
#  - env: Odoo Environment
#  - user: Current user
#  - company: Current company
#  - AND, OR: Domain operators from odoo.osv.expression
#  - normalize: Function to normalize domain lists
#  - _: Translation function
#
# Example:
# if ticket.project_id:
#     domain = [('project_id', '=', ticket.project_id.id)]

"""


class HelpdeskTeam(models.Model):
    _name = "helpdesk.ticket.team"
    _description = "Helpdesk Ticket Team"
    _inherit = ["mail.thread", "mail.alias.mixin"]
    _order = "sequence, id"
    _parent_name = "parent_id"
    _parent_store = True
    _parent_order = "name"
    _rec_name = "complete_name"

    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, translate=True)
    user_ids = fields.Many2many(
        comodel_name="res.users",
        string="Members",
        relation="helpdesk_ticket_team_res_users_rel",
        column1="helpdesk_ticket_team_id",
        column2="res_users_id",
    )
    active = fields.Boolean(default=True)
    category_ids = fields.Many2many(comodel_name="helpdesk.ticket.category", string="Category")
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    user_id = fields.Many2one(comodel_name="res.users", string="Team Leader", check_company=True)
    alias_id = fields.Many2one(
        comodel_name="mail.alias",
        string="Email",
        ondelete="restrict",
        required=True,
        help="The email address associated with this channel. New emails received will automatically create new tickets assigned to the channel.",
    )
    color = fields.Integer(string="Color Index", default=0)
    ticket_ids = fields.One2many(comodel_name="helpdesk.ticket", inverse_name="team_id", string="Tickets")
    todo_ticket_count = fields.Integer(string="Number of tickets", compute="_compute_todo_tickets")
    todo_ticket_count_unassigned = fields.Integer(string="Number of tickets unassigned", compute="_compute_todo_tickets")
    todo_ticket_count_unattended = fields.Integer(string="Number of tickets unattended", compute="_compute_todo_tickets")
    todo_ticket_count_high_priority = fields.Integer(string="Number of tickets in high priority", compute="_compute_todo_tickets")
    show_in_portal = fields.Boolean(
        string="Show in portal form",
        default=True,
        help="Allow to select this team when creating a new ticket in the portal.",
    )
    parent_id = fields.Many2one("helpdesk.ticket.team", string="Parent Team", index=True)
    complete_name = fields.Char(compute="_compute_complete_name", recursive=True, search="_search_complete_name")
    parent_path = fields.Char(index=True)

    # From helpdesk_mgmt_activity
    allow_set_activity = fields.Boolean(
        string="Set Activities",
        help="Available to set activity on source record from ticket",
    )
    activity_stage_id = fields.Many2one(
        comodel_name="helpdesk.ticket.stage",
        string="Done Activity Stage",
        help="Move the ticket when the activity in source record is done",
    )

    # From helpdesk_mgmt_sla
    use_sla = fields.Boolean(string="Use SLA")
    resource_calendar_id = fields.Many2one(
        "resource.calendar",
        "Working Hours",
        default=lambda self: self.env.company.resource_calendar_id,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
    )

    # From helpdesk_type
    type_ids = fields.Many2many(comodel_name="helpdesk.ticket.type", string="Ticket Types")

    # From helpdesk_motive
    motive_ids = fields.One2many(
        comodel_name="helpdesk.ticket.motive",
        inverse_name="team_id",
        string="Motives",
    )

    # From helpdesk_ticket_close_inactive
    close_inactive_tickets = fields.Boolean(string="Auto-close Inactive Tickets")
    ticket_stage_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.stage",
        relation="helpdesk_team_close_stage_rel",
        string="Stages to Monitor",
    )
    ticket_category_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.category",
        relation="helpdesk_team_close_category_rel",
        string="Categories to Monitor",
    )
    inactive_tickets_day_limit_warning = fields.Integer(
        string="Warning after (days)", default=7
    )
    warning_inactive_mail_template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Warning Email Template",
        domain=[("model", "=", "helpdesk.ticket")],
    )
    inactive_tickets_day_limit_closing = fields.Integer(
        string="Close after (days)", default=14
    )
    close_inactive_mail_template_id = fields.Many2one(
        comodel_name="mail.template",
        string="Closing Email Template",
        domain=[("model", "=", "helpdesk.ticket")],
    )
    closing_ticket_stage = fields.Many2one(
        comodel_name="helpdesk.ticket.stage",
        string="Closing Stage",
    )

    # From helpdesk_ticket_partner_response
    autoupdate_ticket_stage = fields.Boolean(
        string="Auto Update Ticket Stage",
        help="Update ticket stage when a new message is registered by the partner.",
        default=False,
    )
    autopupdate_src_stage_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.stage",
        relation="change_stage_partner_response",
        string="Autoupdate Source Stages",
    )
    autopupdate_dest_stage_id = fields.Many2one(
        "helpdesk.ticket.stage",
        string="Autoupdate Destination Stage",
    )

    # From helpdesk_mgmt_project_domain
    project_domain = fields.Char(
        help="Domain to filter projects available for selection in tickets.",
    )
    project_domain_python = fields.Text(
        string="Project Domain Python Code",
        default=DEFAULT_PYTHON_CODE,
    )
    task_domain = fields.Char(
        help="Domain to filter tasks available for selection in tickets.",
    )
    task_domain_python = fields.Text(
        string="Task Domain Python Code",
        default=DEFAULT_TASK_PYTHON_CODE,
    )

    # From helpdesk_mgmt_timesheet
    allow_timesheet = fields.Boolean(string="Allow Timesheets")
    show_timesheet_portal = fields.Boolean(help="Show spent time of tickets in the portal")
    default_project_id = fields.Many2one(
        comodel_name="project.project",
        string="Default Project",
    )

    def _search_complete_name(self, operator, value):
        records = self.search_fetch([], ["complete_name"]).filtered_domain(
            [("complete_name", operator, value)]
        )
        return [("id", "in", records.ids)]

    @api.depends("name", "parent_id.complete_name")
    @api.depends_context("lang")
    def _compute_complete_name(self):
        for record in self:
            if record.parent_id:
                record.complete_name = f"{record.parent_id.complete_name} / {record.name}"
            else:
                record.complete_name = record.name

    def _get_applicable_stages(self):
        if self:
            domain = [
                ("company_id", "in", [False, self.company_id.id]),
                "|",
                ("team_ids", "=", False),
                ("team_ids", "=", self.id),
            ]
        else:
            domain = [
                ("company_id", "in", [False, self.env.company.id]),
                ("team_ids", "=", False),
            ]
        return self.env["helpdesk.ticket.stage"].search(domain)

    @api.depends("ticket_ids", "ticket_ids.stage_id")
    def _compute_todo_tickets(self):
        ticket_model = self.env["helpdesk.ticket"]
        fetch_data = ticket_model._read_group(
            domain=[("team_id", "in", self.ids), ("closed", "=", False)],
            groupby=["team_id", "user_id", "unattended", "priority"],
            aggregates=["__count"],
        )
        result = [
            [team.id, user.id if user else False, unattended, priority, count]
            for team, user, unattended, priority, count in fetch_data
        ]
        for team in self:
            team.todo_ticket_count = sum(r[4] for r in result if r[0] == team.id)
            team.todo_ticket_count_unassigned = sum(r[4] for r in result if r[0] == team.id and not r[1])
            team.todo_ticket_count_unattended = sum(r[4] for r in result if r[0] == team.id and r[2])
            team.todo_ticket_count_high_priority = sum(r[4] for r in result if r[0] == team.id and r[3] == "3")

    def _alias_get_creation_values(self):
        values = super()._alias_get_creation_values()
        values["alias_model_id"] = self.env.ref("helpdesk_custom.model_helpdesk_ticket").id
        values["alias_defaults"] = defaults = _safe_eval(self.alias_defaults or "{}")
        defaults["team_id"] = self.id
        return values

    @api.model
    def retrieve_dashboard(self):
        return sorted(self._retrieve_dashboard(), key=lambda d: d.get("sequence", 99))

    def _retrieve_dashboard(self):
        no_team_tickets = self.env["helpdesk.ticket"].search_count(
            [("team_id", "=", False), ("stage_id.closed", "=", False)]
        )
        return [
            {
                "name": self.env._("Open Tickets without team"),
                "value": no_team_tickets,
                "sequence": 1,
                "icon": "fa-exclamation-circle",
                "show": no_team_tickets > 0,
                "action": "helpdesk_custom.helpdesk_ticket_action_unassigned",
            },
            {
                "name": self.env._("Open Tickets"),
                "value": self.env["helpdesk.ticket"].search_count([("stage_id.closed", "=", False)]),
                "sequence": 2,
                "icon": "fa-life-ring",
                "show": True,
                "action": "helpdesk_custom.helpdesk_ticket_action_opened",
            },
        ]

    # From helpdesk_ticket_close_inactive
    def close_team_inactive_tickets(self):
        from datetime import timedelta
        now = fields.Datetime.now()
        for team in self.search([("close_inactive_tickets", "=", True)]):
            domain = [("team_id", "=", team.id), ("closed", "=", False)]
            if team.ticket_stage_ids:
                domain.append(("stage_id", "in", team.ticket_stage_ids.ids))
            if team.ticket_category_ids:
                domain.append(("category_id", "in", team.ticket_category_ids.ids))
            tickets = self.env["helpdesk.ticket"].search(domain)
            for ticket in tickets:
                last_update = ticket.last_stage_update or ticket.create_date
                days_inactive = (now - last_update).days
                if days_inactive >= team.inactive_tickets_day_limit_closing and team.closing_ticket_stage:
                    if team.close_inactive_mail_template_id:
                        team.close_inactive_mail_template_id.send_mail(ticket.id)
                    ticket.stage_id = team.closing_ticket_stage
                    ticket.message_post(body=self.env._("Ticket automatically closed due to inactivity."))
                elif days_inactive >= team.inactive_tickets_day_limit_warning and team.warning_inactive_mail_template_id:
                    team.warning_inactive_mail_template_id.send_mail(ticket.id)

    # From helpdesk_mgmt_timesheet
    @api.constrains("allow_timesheet")
    def _constrains_allow_timesheet(self):
        if not self.allow_timesheet:
            self.default_project_id = False

    # From helpdesk_mgmt_project_domain
    @api.constrains("project_domain_python", "task_domain_python")
    def _check_python_code(self):
        for record in self:
            if record.project_domain_python:
                msg = test_python_expr(expr=record.project_domain_python.strip(), mode="exec")
                if msg:
                    raise ValidationError(_("Project Domain Python Code: %s") % msg)
            if record.task_domain_python:
                msg = test_python_expr(expr=record.task_domain_python.strip(), mode="exec")
                if msg:
                    raise ValidationError(_("Task Domain Python Code: %s") % msg)

    def _get_eval_context(self, ticket=None):
        def normalize(domain_list):
            return expression.normalize_domain(domain_list)
        return {
            "ticket": ticket,
            "env": self.env,
            "user": self.env.user,
            "company": self.env.company,
            "AND": expression.AND,
            "OR": expression.OR,
            "normalize": normalize,
            "_": _,
        }

    def _execute_python_domain_code(self, python_code, ticket=None):
        if not python_code or not python_code.strip():
            return []
        eval_context = self._get_eval_context(ticket)
        try:
            safe_eval(python_code.strip(), eval_context, mode="exec", nocopy=True)
            return eval_context.get("domain", [])
        except Exception as e:
            _logger.info("Ignoring invalid Python domain code for team %s: %s", self.name, str(e))
            return []
