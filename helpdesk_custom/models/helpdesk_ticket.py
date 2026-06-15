import ast
import logging

from odoo import _, api, fields, models, tools
from odoo.exceptions import AccessError, ValidationError
from odoo.osv import expression
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class HelpdeskTicket(models.Model):
    _name = "helpdesk.ticket"
    _description = "Helpdesk Ticket"
    _rec_name = "number"
    _rec_names_search = ["number", "name"]
    _order = "priority desc, sequence, number desc, id desc"
    _mail_post_access = "read"
    _inherit = [
        "mail.thread.cc",
        "mail.activity.mixin",
        "portal.mixin",
        "mail.tracking.duration.mixin",
        "rating.mixin",
    ]
    _track_duration_field = "stage_id"

    # -------------------------------------------------------------------------
    # Core fields
    # -------------------------------------------------------------------------
    number = fields.Char(string="Ticket number", default="/", readonly=True)
    name = fields.Char(string="Title", required=True)
    description = fields.Html(
        required=True,
        sanitize_style=True,
        compute="_compute_description",
        store=True,
        readonly=False,
    )
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Assigned user",
        tracking=True,
        index=True,
        compute="_compute_user_id",
        store=True,
        readonly=False,
        domain="team_id and [('share', '=', False),('id', 'in', user_ids)] or [('share', '=', False)]",
    )
    user_ids = fields.Many2many(comodel_name="res.users", related="team_id.user_ids", string="Users")
    stage_id = fields.Many2one(
        comodel_name="helpdesk.ticket.stage",
        string="Stage",
        compute="_compute_stage_id",
        store=True,
        readonly=False,
        ondelete="restrict",
        tracking=True,
        group_expand="_read_group_stage_ids",
        copy=False,
        index=True,
        domain="['|',('team_ids', '=', team_id),('team_ids','=',False)]",
    )
    partner_id = fields.Many2one(comodel_name="res.partner", string="Contact")
    commercial_partner_id = fields.Many2one(
        string="Commercial Partner",
        store=True,
        related="partner_id.commercial_partner_id",
    )
    partner_name = fields.Char()
    partner_email = fields.Char(string="Email")
    partner_phone = fields.Char(string="Phone")
    last_stage_update = fields.Datetime(default=fields.Datetime.now)
    assigned_date = fields.Datetime(copy=False)
    closed_date = fields.Datetime(copy=False)
    closed = fields.Boolean(related="stage_id.closed")
    unattended = fields.Boolean(related="stage_id.unattended", store=True)
    tag_ids = fields.Many2many(comodel_name="helpdesk.ticket.tag", string="Tags")
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
    )
    channel_id = fields.Many2one(
        comodel_name="helpdesk.ticket.channel",
        string="Channel",
        help="Channel indicates where the source of a ticket comes from (phone call, email, ...)",
    )
    category_id = fields.Many2one(comodel_name="helpdesk.ticket.category", string="Category")
    team_id = fields.Many2one(
        comodel_name="helpdesk.ticket.team",
        string="Team",
        index=True,
        compute="_compute_team_id",
        store=True,
        readonly=False,
    )
    priority = fields.Selection(
        selection=[("0", "Low"), ("1", "Medium"), ("2", "High"), ("3", "Very High")],
        default="1",
    )
    attachment_ids = fields.One2many(
        comodel_name="ir.attachment",
        inverse_name="res_id",
        domain=[("res_model", "=", "helpdesk.ticket")],
        string="Media Attachments",
    )
    color = fields.Integer(string="Color Index")
    kanban_state = fields.Selection(
        selection=[("normal", "Default"), ("done", "Ready for next stage"), ("blocked", "Blocked")],
    )
    sequence = fields.Integer(index=True, default=10)
    active = fields.Boolean(default=True)
    duplicate_id = fields.Many2one("helpdesk.ticket", string="Duplicate of", tracking=True, copy=False)
    duplicate_ids = fields.One2many("helpdesk.ticket", "duplicate_id", string="Duplicate tickets")
    duplicate_count = fields.Integer(compute="_compute_duplicate_count")
    duplicate_tracking_enabled = fields.Boolean(related="company_id.helpdesk_mgmt_duplicate_tracking")

    # -------------------------------------------------------------------------
    # From helpdesk_mgmt_activity
    # -------------------------------------------------------------------------
    can_create_activity = fields.Boolean(related="team_id.allow_set_activity")
    res_model = fields.Char(string="Source Document Model", index=True)
    res_id = fields.Integer(string="Source Document", index=True)
    record_ref = fields.Reference(
        selection="_selection_record_ref",
        compute="_compute_record_ref",
        inverse="_inverse_record_ref",
        string="Source Record",
    )
    source_activity_type_id = fields.Many2one(comodel_name="mail.activity.type")
    date_deadline = fields.Date(string="Due Date", default=fields.Date.today)
    next_stage_id = fields.Many2one(
        comodel_name="helpdesk.ticket.stage",
        compute="_compute_next_stage_id",
        store=True,
        index=True,
    )
    assigned_user_id = fields.Many2one(comodel_name="res.users")
    is_new_stage = fields.Boolean(compute="_compute_is_new_stage")

    # -------------------------------------------------------------------------
    # From helpdesk_mgmt_sla
    # -------------------------------------------------------------------------
    team_sla = fields.Boolean(string="Team SLA", related="team_id.use_sla")
    ticket_sla_ids = fields.One2many("helpdesk.ticket.sla", inverse_name="ticket_id", readonly=True)
    sla_ids = fields.Many2many(comodel_name="helpdesk.sla", string="Applicable SLAs", compute="_compute_sla_ids")
    sla_expired = fields.Boolean(string="SLA expired", compute="_compute_sla_data", search="_search_sla_expired")
    sla_deadline = fields.Datetime(string="SLA deadline", compute="_compute_sla_data")
    sla_fits = fields.Boolean(compute="_compute_sla_fits")

    # -------------------------------------------------------------------------
    # From helpdesk_mgmt_template
    # -------------------------------------------------------------------------
    helpdesk_ticket_category_ids = fields.Many2many(
        comodel_name="helpdesk.ticket.category",
        compute="_compute_helpdesk_ticket_category",
        string="Available Categories",
    )

    # -------------------------------------------------------------------------
    # From helpdesk_mgmt_rating
    # -------------------------------------------------------------------------
    positive_rate_percentage = fields.Integer(
        string="Positive Rates Percentage",
        compute="_compute_percentage",
        store=True,
        default=-1,
    )
    rating_status = fields.Selection(
        selection=[("stage_change", "Rating when changing stage"), ("no_rate", "No rating")],
        string="Customer Rating",
        default="stage_change",
        required=True,
    )

    # -------------------------------------------------------------------------
    # From helpdesk_type
    # -------------------------------------------------------------------------
    type_id = fields.Many2one(comodel_name="helpdesk.ticket.type", string="Type")

    # -------------------------------------------------------------------------
    # From helpdesk_motive
    # -------------------------------------------------------------------------
    motive_id = fields.Many2one(
        comodel_name="helpdesk.ticket.motive",
        string="Motive",
        compute="_compute_motive_id",
        store=True,
        readonly=False,
    )

    # -------------------------------------------------------------------------
    # From helpdesk_product
    # -------------------------------------------------------------------------
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        domain=[("ticket_active", "=", True)],
    )

    # -------------------------------------------------------------------------
    # From helpdesk_ticket_related
    # -------------------------------------------------------------------------
    related_ticket_ids = fields.Many2many(
        comodel_name="helpdesk.ticket",
        relation="ticket_relationship_table",
        column1="ticket_id1",
        column2="ticket_id2",
        string="Related Tickets",
    )
    related_ticket_count = fields.Integer(compute="_compute_related_ticket_count")

    # -------------------------------------------------------------------------
    # From helpdesk_mgmt_project
    # -------------------------------------------------------------------------
    project_id = fields.Many2one(comodel_name="project.project", string="Project", tracking=True)
    task_id = fields.Many2one(
        comodel_name="project.task",
        string="Task",
        compute="_compute_task_id",
        readonly=False,
        store=True,
        tracking=True,
    )
    milestone_id = fields.Many2one(
        "project.milestone",
        store=True,
        tracking=True,
        readonly=False,
        compute="_compute_milestone_id",
    )

    # -------------------------------------------------------------------------
    # From helpdesk_mgmt_project_domain
    # -------------------------------------------------------------------------
    project_domain_ids = fields.Many2many(
        "project.project",
        string="Available Projects",
        compute="_compute_project_domain_ids",
    )
    task_domain_ids = fields.Many2many(
        "project.task",
        string="Available Tasks",
        compute="_compute_task_domain_ids",
    )

    # -------------------------------------------------------------------------
    # From helpdesk_mgmt_crm
    # -------------------------------------------------------------------------
    lead_ids = fields.One2many(comodel_name="crm.lead", inverse_name="ticket_id", string="Opportunity(ies)")
    lead_count = fields.Integer(compute="_compute_lead_count", string="Opportunity Count")

    # -------------------------------------------------------------------------
    # From helpdesk_mgmt_sale
    # -------------------------------------------------------------------------
    sale_order_ids = fields.Many2many("sale.order", string="Sales Orders")
    so_count = fields.Integer(string="Sale Order Count", compute="_compute_so_count")

    # -------------------------------------------------------------------------
    # From helpdesk_mgmt_timesheet
    # -------------------------------------------------------------------------
    allow_timesheet = fields.Boolean(string="Allow Timesheet", related="team_id.allow_timesheet")
    planned_hours = fields.Float(tracking=True)
    progress = fields.Float(compute="_compute_progress_hours", aggregator="avg", store=True)
    remaining_hours = fields.Float(compute="_compute_progress_hours", readonly=True, store=True)
    timesheet_ids = fields.One2many(
        comodel_name="account.analytic.line",
        inverse_name="ticket_id",
        string="Timesheet",
    )
    total_hours = fields.Float(compute="_compute_total_hours", readonly=True, store=True)
    last_timesheet_activity = fields.Date(compute="_compute_last_timesheet_activity", readonly=True, store=True)

    # =========================================================================
    # Computes
    # =========================================================================

    @api.depends("team_id")
    def _compute_stage_id(self):
        for ticket in self:
            ticket.stage_id = ticket.team_id._get_applicable_stages()[:1]

    @api.depends("team_id")
    def _compute_user_id(self):
        for ticket in self:
            if ticket.team_id and ticket.user_id not in ticket.team_id.user_ids:
                ticket.user_id = False

    @api.depends("user_id")
    def _compute_team_id(self):
        for ticket in self:
            if not ticket.team_id and ticket.user_id.helpdesk_team_ids:
                ticket.team_id = ticket.user_id.helpdesk_team_ids[0]

    @api.model
    def _read_group_stage_ids(self, stages, domain):
        search_domain = ["|", ("id", "in", stages.ids), ("team_ids", "=", False)]
        default_team_id = self.default_get(["team_id"])
        if default_team_id:
            search_domain = ["|", ("team_ids", "=", default_team_id["team_id"])] + search_domain
        return stages.search(search_domain)

    @api.depends("related_ticket_ids")
    def _compute_related_ticket_count(self):
        for record in self:
            record.related_ticket_count = len(record.related_ticket_ids)

    @api.depends("duplicate_ids")
    def _compute_duplicate_count(self):
        for record in self:
            record.duplicate_count = len(record.duplicate_ids)

    @api.depends("name")
    def _compute_display_name(self):
        for ticket in self:
            ticket.display_name = f"{ticket.number} - {ticket.name}"

    # --- helpdesk_mgmt_activity ---

    @api.model
    def _selection_record_ref(self):
        model_ids_str = (
            self.env["ir.config_parameter"].sudo()
            .get_param("helpdesk_custom.helpdesk_available_model_ids", "[]")
        )
        model_ids = ast.literal_eval(model_ids_str)
        if not model_ids:
            return []
        IrModelAccess = self.env["ir.model.access"].with_user(self.env.user.id)
        available_models = self.env["ir.model"].search_read(
            [("id", "in", model_ids)], fields=["model", "name"]
        )
        return [
            (model.get("model"), model.get("name"))
            for model in available_models
            if IrModelAccess.check(model.get("model"), "read", False)
        ]

    def _compute_is_new_stage(self):
        for ticket in self:
            new_stage = ticket.team_id._get_applicable_stages()[:1]
            ticket.is_new_stage = ticket.stage_id == new_stage

    @api.depends("stage_id")
    def _compute_next_stage_id(self):
        team_stages = {team.id: team._get_applicable_stages() for team in self.team_id}
        stage_obj = self.env["helpdesk.ticket.stage"]
        for record in self:
            current_stage = record.stage_id
            stages = team_stages.get(record.team_id.id, stage_obj)
            next_stage = (
                stages.filtered(lambda s, cur=current_stage: s.sequence > cur.sequence)[:1]
                or current_stage
            )
            record.next_stage_id = next_stage

    @api.depends("res_model", "res_id")
    def _compute_record_ref(self):
        for rec in self:
            if not rec.res_model or not rec.res_id:
                rec.record_ref = None
                continue
            try:
                record = self.env[rec.res_model].browse(rec.res_id)
                record.check_access("read")
                rec.record_ref = f"{rec.res_model},{rec.res_id}"
            except Exception:
                rec.record_ref = None

    def _inverse_record_ref(self):
        for record in self:
            record_ref = record.record_ref
            record.write({
                "res_id": record_ref and record_ref.id or False,
                "res_model": record_ref and record_ref._name or False,
            })

    # --- helpdesk_mgmt_sla ---

    def _compute_sla_fits(self):
        for ticket in self:
            ticket.sla_fits = ticket.sla_ids == ticket._get_sla()

    @api.depends("ticket_sla_ids", "ticket_sla_ids.state", "ticket_sla_ids.deadline")
    def _compute_sla_data(self):
        now = fields.Datetime.now()
        for ticket in self:
            ticket.sla_expired = any(
                ticket.ticket_sla_ids.filtered(
                    lambda sla: sla.state == "expired" or (
                        sla.state == "in_progress" and sla.deadline and sla.deadline < now
                    )
                )
            )
            ticket.sla_deadline = min(
                ticket.ticket_sla_ids.filtered(lambda r: r.state == "in_progress").mapped("deadline"),
                default=False,
            )

    @api.depends("ticket_sla_ids")
    def _compute_sla_ids(self):
        for ticket in self:
            ticket.sla_ids = ticket.ticket_sla_ids.sla_id

    # --- helpdesk_mgmt_template ---

    @api.depends("team_id")
    def _compute_helpdesk_ticket_category(self):
        for ticket in self:
            ticket.helpdesk_ticket_category_ids = ticket.team_id.category_ids

    @api.depends("category_id")
    def _compute_description(self):
        for ticket in self:
            if ticket.category_id and ticket.category_id.template_description:
                if not ticket.description or ticket.description == "<p></p>":
                    ticket.description = ticket.category_id.template_description

    # --- helpdesk_mgmt_rating ---

    @api.depends("rating_ids.rating")
    def _compute_percentage(self):
        for ticket in self:
            activity = ticket.rating_get_grades()
            ticket.positive_rate_percentage = (
                activity["great"] * 100 / sum(activity.values())
                if sum(activity.values()) else -1
            )

    # --- helpdesk_motive ---

    @api.depends("team_id", "user_id")
    def _compute_motive_id(self):
        for ticket in self:
            if ticket.motive_id and ticket.motive_id.team_id != ticket.team_id:
                ticket.motive_id = False

    # --- helpdesk_mgmt_project ---

    @api.depends("task_id")
    def _compute_milestone_id(self):
        for record in self:
            if record.task_id:
                record.milestone_id = record.task_id.milestone_id

    @api.depends("project_id")
    def _compute_task_id(self):
        for record in self:
            if record.task_id.project_id != record.project_id:
                record.task_id = False

    # --- helpdesk_mgmt_project_domain ---

    @api.depends("team_id", "partner_id", "category_id", "priority", "company_id")
    def _compute_project_domain_ids(self):
        for record in self:
            domain = record._get_project_domain_dynamic()
            if domain:
                record.project_domain_ids = self.env["project.project"].search(domain)
            else:
                record.project_domain_ids = self.env["project.project"]

    @api.depends("team_id", "partner_id", "category_id", "priority", "company_id", "project_id")
    def _compute_task_domain_ids(self):
        for record in self:
            domain = record._get_task_domain_dynamic()
            if domain:
                record.task_domain_ids = self.env["project.task"].search(domain)
            else:
                record.task_domain_ids = self.env["project.task"]

    # --- helpdesk_mgmt_crm ---

    @api.depends("lead_ids")
    def _compute_lead_count(self):
        mapped_data = {
            ticket.id: count
            for ticket, count in self.env["crm.lead"]._read_group(
                domain=[("ticket_id", "in", self.ids)],
                groupby=["ticket_id"],
                aggregates=["__count"],
            )
        }
        for item in self:
            item.lead_count = mapped_data.get(item.id, 0)

    # --- helpdesk_mgmt_sale ---

    @api.depends("sale_order_ids")
    def _compute_so_count(self):
        group_data = self.env["sale.order"]._read_group(
            domain=[("ticket_ids", "in", self.ids)],
            groupby=["ticket_ids"],
            aggregates=["__count"],
        )
        mapped_data = {ticket.id: count for ticket, count in group_data}
        for ticket in self:
            ticket.so_count = mapped_data.get(ticket.id, 0)

    # --- helpdesk_mgmt_timesheet ---

    @api.depends("timesheet_ids.unit_amount")
    def _compute_total_hours(self):
        for record in self:
            record.total_hours = sum(record.timesheet_ids.mapped("unit_amount"))

    @api.depends("planned_hours", "total_hours")
    def _compute_progress_hours(self):
        for ticket in self:
            ticket.progress = 0.0
            if ticket.planned_hours > 0.0:
                ticket.progress = min(round(100.0 * ticket.total_hours / ticket.planned_hours, 2), 100)
            ticket.remaining_hours = ticket.planned_hours - ticket.total_hours

    @api.depends("timesheet_ids.date")
    def _compute_last_timesheet_activity(self):
        for record in self:
            record.last_timesheet_activity = (
                record.timesheet_ids and record.timesheet_ids.sorted(key="date", reverse=True)[0].date
            ) or False

    # --- helpdesk_mgmt_stage_validation ---

    def _check_ticket_has_empty_fields(self):
        self.ensure_one()
        empty_fields = []
        for field in self.stage_id.validate_field_ids:
            if not self[field.name]:
                empty_fields.append(field.field_description)
        return empty_fields

    # =========================================================================
    # Onchange
    # =========================================================================

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id:
            self.partner_name = self.partner_id.name
            self.partner_email = self.partner_id.email

    @api.onchange("type_id")
    def _onchange_type_id(self):
        if self.type_id and self.team_id and self.type_id not in self.team_id.type_ids:
            self.team_id = False
            self.user_id = False

    @api.onchange("team_id")
    def _onchange_team_id_timesheet(self):
        for record in self.filtered(lambda a: a.team_id and a.team_id.allow_timesheet):
            record.project_id = record.team_id.default_project_id

    @api.onchange("team_id", "partner_id", "category_id", "priority")
    def _onchange_project_domain(self):
        self.ensure_one()
        domain = self._get_project_domain_dynamic()
        return {"domain": {"project_id": domain}}

    @api.onchange("team_id", "partner_id", "category_id", "priority", "project_id")
    def _onchange_task_domain(self):
        self.ensure_one()
        domain = self._get_task_domain_dynamic()
        return {"domain": {"task_id": domain}}

    # =========================================================================
    # Constraints
    # =========================================================================

    @api.constrains("stage_id")
    def _validate_stage_fields(self):
        for ticket in self:
            if not ticket.stage_id.validate_field_ids:
                continue
            empty_fields = ticket._check_ticket_has_empty_fields()
            if empty_fields:
                raise ValidationError(
                    self.env._("The following fields are required to reach stage '%(stage)s': %(fields)s")
                    % {"stage": ticket.stage_id.name, "fields": ", ".join(empty_fields)}
                )

    @api.constrains("project_id")
    def _constrains_project_timesheets(self):
        for record in self:
            record.timesheet_ids.update({"project_id": record.project_id.id})

    # =========================================================================
    # Actions
    # =========================================================================

    def assign_to_me(self):
        self.write({"user_id": self.env.user.id})

    def action_open_duplicate_wizard(self):
        self.ensure_one()
        target_stage = self.env.company.helpdesk_mgmt_duplicate_ticket_stage_id
        return {
            "name": "Mark as Duplicate",
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket.duplicate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_ticket_id": self.id,
                "default_target_stage_id": target_stage.id,
            },
        }

    def action_view_duplicates(self):
        self.ensure_one()
        return {
            "name": "Duplicates",
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "view_mode": "list",
            "target": "new",
            "domain": [("duplicate_id", "=", self.id)],
        }

    def action_duplicate_tickets(self):
        for ticket in self.browse(self.env.context["active_ids"]):
            ticket.copy()

    def set_next_stage(self):
        for record in self:
            record.stage_id = record.next_stage_id

    def perform_action(self):
        self.ensure_one()
        self._check_activity_values()
        try:
            self.record_ref.activity_schedule(
                summary=self.name,
                note=self.description,
                date_deadline=self.date_deadline,
                activity_type_id=self.source_activity_type_id.id,
                ticket_id=self.id,
            )
            self.set_next_stage()
        except Exception as e:
            raise models.UserError from e
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": _("Activity has been created!"),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _check_activity_values(self):
        if not self.can_create_activity:
            raise models.UserError(_("You cannot create activity!"))
        if not (self.res_id and self.res_model):
            raise models.UserError(_("Source Record is not set!"))
        if not self.source_activity_type_id:
            raise models.UserError(_("Activity Type is not set!"))
        if not self.date_deadline:
            raise models.UserError(_("Date Deadline is not set!"))
        if not self.assigned_user_id:
            raise models.UserError(_("Assigned User is not set!"))

    def refresh_sla(self):
        self.ensure_one()
        slas = self._get_sla()
        self.ticket_sla_ids.filtered(lambda r: r.sla_id not in slas).unlink()
        for sla in slas - self.sla_ids:
            self.env["helpdesk.ticket.sla"].create({"ticket_id": self.id, "sla_id": sla.id})

    def _send_ticket_rating_mail(self, force_send=False):
        for ticket in self:
            if ticket.rating_status == "stage_change":
                survey_template = ticket.stage_id.rating_mail_template_id
                if survey_template:
                    ticket.rating_send_request(survey_template, lang=ticket.partner_id.lang, force_send=force_send)

    def _rating_apply_get_default_subtype_id(self):
        return self.env["ir.model.data"]._xmlid_to_res_id("helpdesk_custom.mt_ticket_rating")

    def action_view_ticket_rating(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("helpdesk_custom.helpdesk_ticket_rating_action")
        action["name"] = _("Ticket Rating")
        action_context = safe_eval(action["context"]) if action.get("context") else {}
        action_context.update(self.env.context)
        action_context.pop("group_by", None)
        if not action_context.get("id"):
            action_context["id"] = self.id
        action["context"] = action_context
        return action

    def action_view_related_tickets(self):
        self.ensure_one()
        return {
            "name": _("Related Tickets"),
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "view_mode": "list,form",
            "domain": [("id", "in", self.related_ticket_ids.ids)],
        }

    def action_view_timesheets(self):
        self.ensure_one()
        return {
            "name": _("Timesheets"),
            "type": "ir.actions.act_window",
            "res_model": "account.analytic.line",
            "view_mode": "list,form",
            "domain": [("ticket_id", "=", self.id)],
            "context": {"default_ticket_id": self.id, "default_project_id": self.project_id.id},
        }

    def action_open_leads(self):
        result = self.env["ir.actions.act_window"]._for_xml_id("crm.crm_lead_action_pipeline")
        if len(self.lead_ids) == 1:
            res = self.env.ref("crm.crm_lead_view_form", False)
            result["views"] = [(res and res.id or False, "form")]
            result["res_id"] = self.lead_ids.id
        else:
            result["domain"] = [("id", "in", self.lead_ids.ids)]
            ctx = dict(self.env.context)
            ctx.update({"default_ticket_id": self.id, "search_default_ticket_id": self.id})
            result["context"] = ctx
        return result

    def action_view_sale_orders(self):
        from odoo import Command
        action = self.env["ir.actions.actions"]._for_xml_id("sale.action_orders")
        action["domain"] = [("ticket_ids", "in", [self.id])]
        action["context"] = {
            "default_ticket_ids": [Command.link([self.id])],
            "default_partner_id": self.partner_id.id,
        }
        return action

    def action_open_link_sale_order(self):
        self.ensure_one()
        commercial_partner = self.partner_id.commercial_partner_id
        sale_orders = self.env["sale.order"].search([
            ("partner_id.commercial_partner_id", "=", commercial_partner.id),
            ("ticket_ids", "=", False),
        ])
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket.link.sale.order.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_ticket_id": self.id,
                "default_commercial_partner_id": commercial_partner.id,
                "default_sale_orders_ids": sale_orders.ids,
            },
        }

    # =========================================================================
    # SLA helpers
    # =========================================================================

    def _get_sla_ticket_domain(self):
        domain = ["|", ("team_ids", "=", False), ("team_ids", "=", self.team_id.id)]
        if self.tag_ids:
            domain += ["|", ("tag_ids", "=", False), ("tag_ids", "in", self.tag_ids.ids)]
        else:
            domain += [("tag_ids", "=", False)]
        if self.category_id:
            domain += ["|", ("category_ids", "=", False), ("category_ids", "=", self.category_id.id)]
        else:
            domain += [("category_ids", "=", False)]
        return domain

    def _get_sla(self):
        slas = self.env["helpdesk.sla"]
        for sla in self.env["helpdesk.sla"].search(self._get_sla_ticket_domain()):
            if not sla.domain or self.filtered_domain(safe_eval(sla.domain)):
                slas |= sla
        return slas

    def set_sla(self):
        for ticket in self:
            ticket.ticket_sla_ids.unlink()
            if ticket.team_id.use_sla:
                for sla in ticket._get_sla():
                    self.env["helpdesk.ticket.sla"].create({"ticket_id": ticket.id, "sla_id": sla.id})

    def _search_sla_expired(self, operator, value):
        return [("ticket_sla_ids.expired", operator, value)]

    # =========================================================================
    # Project domain helpers
    # =========================================================================

    def _safe_eval_domain_text(self, expr):
        if not expr:
            return []
        try:
            dom = safe_eval(expr, {"uid": self.env.uid})
            if isinstance(dom, (list, tuple)):
                return expression.normalize_domain(list(dom))
        except Exception as e:
            _logger.error("Failed to evaluate static domain (expr=%s): %s", expr, e)
        return []

    def _compute_project_domain_from_sources(self, team=None, company=None):
        team = team or (self.team_id if self else None)
        company = company or (self.company_id if self else self.env.company)
        domains = []
        if company and getattr(company, "helpdesk_mgmt_project_domain", False):
            d = self._safe_eval_domain_text(company.helpdesk_mgmt_project_domain)
            if d:
                domains.append(d)
        if team and getattr(team, "project_domain", False):
            d = self._safe_eval_domain_text(team.project_domain)
            if d:
                domains.append(d)
        if team and getattr(team, "project_domain_python", False):
            d = team._execute_python_domain_code(team.project_domain_python, ticket=self)
            if d:
                domains.append(d)
        return expression.AND(domains) if domains else []

    def _compute_task_domain_from_sources(self, team=None, company=None):
        team = team or (self.team_id if self else None)
        company = company or (self.company_id if self else self.env.company)
        domains = []
        if company and getattr(company, "helpdesk_mgmt_task_domain", False):
            d = self._safe_eval_domain_text(company.helpdesk_mgmt_task_domain)
            if d:
                domains.append(d)
        if team and getattr(team, "task_domain", False):
            d = self._safe_eval_domain_text(team.task_domain)
            if d:
                domains.append(d)
        if team and getattr(team, "task_domain_python", False):
            d = team._execute_python_domain_code(team.task_domain_python, ticket=self)
            if d:
                domains.append(d)
        if self and self.project_id:
            domains.append([("project_id", "=", self.project_id.id)])
        return expression.AND(domains) if domains else []

    def _get_project_domain_dynamic(self):
        self.ensure_one()
        return self._compute_project_domain_from_sources()

    def _get_task_domain_dynamic(self):
        self.ensure_one()
        return self._compute_task_domain_from_sources()

    # =========================================================================
    # CRUD
    # =========================================================================

    def _creation_subtype(self):
        return self.env.ref("helpdesk_custom.hlp_tck_created")

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        company_id = defaults.get("company_id") or self.env.company.id
        if "user_id" in fields_list and not defaults.get("user_id"):
            company = self.env["res.company"].browse(company_id)
            if company.helpdesk_mgmt_ticket_auto_assign:
                if defaults.get("team_id"):
                    team = self.env["helpdesk.ticket.team"].browse(defaults.get("team_id"))
                    if self.env.user in team.user_ids:
                        defaults["user_id"] = self.env.user.id
                else:
                    defaults["user_id"] = self.env.user.id
        return defaults

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("number", "/") == "/":
                vals["number"] = self._prepare_ticket_number(vals)
            if vals.get("user_id") and not vals.get("assigned_date"):
                vals["assigned_date"] = fields.Datetime.now()
            if vals.get("team_id"):
                team = self.env["helpdesk.ticket.team"].browse([vals["team_id"]])
                if team.company_id:
                    vals["company_id"] = team.company_id.id
                if "stage_id" not in vals:
                    vals["stage_id"] = team._get_applicable_stages()[:1].id
            if self.env.context.get("fetchmail_cron_running") and not vals.get("channel_id"):
                channel_email_id = self.env.ref(
                    "helpdesk_custom.helpdesk_ticket_channel_email", raise_if_not_found=False
                )
                if channel_email_id:
                    vals["channel_id"] = channel_email_id.id
        tickets = super().create(vals_list)
        tickets.set_sla()
        return tickets

    def copy(self, default=None):
        self.ensure_one()
        if default is None:
            default = {}
        if "number" not in default:
            default["number"] = self._prepare_ticket_number(default)
        if "description" not in default:
            default["description"] = "<p></p>"
        return super().copy(default)

    def write(self, vals):
        now = fields.Datetime.now()
        if vals.get("stage_id"):
            stage = self.env["helpdesk.ticket.stage"].browse([vals["stage_id"]])
            vals["last_stage_update"] = now
            if stage.closed:
                vals["closed_date"] = now
        if vals.get("user_id"):
            vals["assigned_date"] = now
        res = super().write(vals)
        if "stage_id" in vals:
            # SLA recompute
            for ticket_sla in self.ticket_sla_ids:
                ticket_sla._stage_recompute()
            # Rating mail
            stage = self.env["helpdesk.ticket.stage"].browse(vals["stage_id"])
            if stage.rating_mail_template_id:
                self._send_ticket_rating_mail(force_send=True)
        return res

    def _prepare_ticket_number(self, values):
        seq = self.env["ir.sequence"]
        if "company_id" in values:
            seq = seq.with_company(values["company_id"])
        return seq.next_by_code("helpdesk.ticket.sequence") or "/"

    def _compute_access_url(self):
        res = super()._compute_access_url()
        for item in self:
            item.access_url = f"/my/ticket/{item.id}"
        return res

    # =========================================================================
    # Mail gateway
    # =========================================================================

    def _track_template(self, tracking):
        res = super()._track_template(tracking)
        ticket = self[0]
        if "stage_id" in tracking and ticket.stage_id.mail_template_id:
            res["stage_id"] = (
                ticket.stage_id.mail_template_id,
                {
                    "composition_mode": "mass_mail",
                    "auto_delete_keep_log": False,
                    "subtype_id": self.env["ir.model.data"]._xmlid_to_res_id("mail.mt_note"),
                    "email_layout_xmlid": "mail.mail_notification_light",
                },
            )
        return res

    @api.model
    def message_new(self, msg, custom_values=None):
        if custom_values is None:
            custom_values = {}
        defaults = {
            "name": msg.get("subject") or self.env._("No Subject"),
            "number": "/",
            "description": msg.get("body"),
            "partner_email": msg.get("from"),
            "partner_id": msg.get("author_id"),
        }
        defaults.update(custom_values)
        ticket = super().message_new(msg, custom_values=defaults)
        email_list = tools.email_split((msg.get("to") or "") + "," + (msg.get("cc") or ""))
        partner_ids = [
            p.id
            for p in self.env["mail.thread"]._mail_find_partner_from_emails(email_list, records=ticket, force_create=False)
            if p
        ]
        ticket.message_subscribe(partner_ids)
        return ticket

    def message_update(self, msg, update_vals=None):
        email_list = tools.email_split((msg.get("to") or "") + "," + (msg.get("cc") or ""))
        partner_ids = [
            p.id
            for p in self.env["mail.thread"]._mail_find_partner_from_emails(email_list, records=self, force_create=False)
            if p
        ]
        self.message_subscribe(partner_ids)
        return super().message_update(msg, update_vals=update_vals)

    def _message_get_suggested_recipients(self):
        recipients = super()._message_get_suggested_recipients()
        try:
            for ticket in self:
                if ticket.partner_id:
                    ticket._message_add_suggested_recipient(recipients, partner=ticket.partner_id, reason=self.env._("Customer"))
                elif ticket.partner_email:
                    ticket._message_add_suggested_recipient(recipients, email=ticket.partner_email, reason=self.env._("Customer Email"))
        except AccessError:
            return recipients
        return recipients

    def _notify_get_reply_to(self, default=None, **kwargs):
        aliases = self.sudo().mapped("team_id")._notify_get_reply_to(default=default, **kwargs)
        res = {ticket.id: aliases.get(ticket.team_id.id) for ticket in self}
        leftover = self.filtered(lambda rec: not rec.team_id)
        if leftover:
            res.update(super(HelpdeskTicket, leftover)._notify_get_reply_to(default=default, **kwargs))
        return res

    def _message_post_after_hook(self, message, msg_vals):
        public_user = self.env.ref("base.public_user")
        if (
            self
            and self.env.user.partner_id.id in (self.partner_id.id, public_user.partner_id.id)
            and self.team_id.autoupdate_ticket_stage
            and self.stage_id in self.team_id.autopupdate_src_stage_ids
        ):
            self.sudo().stage_id = self.team_id.autopupdate_dest_stage_id.id
        return super()._message_post_after_hook(message, msg_vals)
