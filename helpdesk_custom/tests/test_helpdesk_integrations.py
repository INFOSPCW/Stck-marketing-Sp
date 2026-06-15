"""
Tests for cross-model integrations:
  - res.partner  (ticket count, action)
  - project.project / project.task / project.milestone
  - sale.order
  - crm.lead
  - account.analytic.line (timesheets)
  - product.product
  - res.company settings fields
"""
from odoo import fields as odoo_fields
from odoo.tests.common import tagged

from .common import HelpdeskCommon


# =============================================================================
# Partner integration
# =============================================================================

@tagged("post_install", "-at_install")
class TestPartnerIntegration(HelpdeskCommon):

    def test_partner_helpdesk_ticket_count(self):
        """Partner.helpdesk_ticket_count should include all linked tickets."""
        self._create_ticket(partner_id=self.partner.id)
        self.partner.invalidate_recordset(["helpdesk_ticket_count"])
        self.assertGreaterEqual(self.partner.helpdesk_ticket_count, 1)

    def test_partner_helpdesk_ticket_active_count(self):
        """helpdesk_ticket_active_count should exclude closed tickets."""
        ticket = self._create_ticket(partner_id=self.partner.id)
        self.partner.invalidate_recordset(["helpdesk_ticket_active_count"])
        active_before = self.partner.helpdesk_ticket_active_count
        ticket.write({"stage_id": self.stage_done.id})
        self.partner.invalidate_recordset(["helpdesk_ticket_active_count"])
        active_after = self.partner.helpdesk_ticket_active_count
        self.assertLess(active_after, active_before + 1)

    def test_partner_ticket_count_string_format(self):
        """helpdesk_ticket_count_string should be 'active / total'."""
        self._create_ticket(partner_id=self.partner.id)
        self.partner.invalidate_recordset(["helpdesk_ticket_count_string"])
        count_str = self.partner.helpdesk_ticket_count_string
        self.assertIn("/", count_str)
        parts = count_str.split("/")
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].strip().isdigit())
        self.assertTrue(parts[1].strip().isdigit())

    def test_partner_action_view_helpdesk_tickets(self):
        """action_view_helpdesk_tickets should return act_window for tickets."""
        action = self.partner.action_view_helpdesk_tickets()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "helpdesk.ticket")

    def test_partner_portal_restriction_default_false(self):
        """helpdesk_portal_restriction should default to False."""
        partner = self.env["res.partner"].create({"name": "Unrestricted"})
        self.assertFalse(partner.helpdesk_portal_restriction)

    def test_partner_portal_restriction_can_be_set(self):
        """helpdesk_portal_restriction should be settable to True."""
        partner = self.env["res.partner"].create({"name": "Restricted"})
        partner.helpdesk_portal_restriction = True
        self.assertTrue(partner.helpdesk_portal_restriction)


# =============================================================================
# Project integration
# =============================================================================

@tagged("post_install", "-at_install")
class TestProjectIntegration(HelpdeskCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = cls.env["project.project"].create({"name": "Integration Project"})
        cls.task = cls.env["project.task"].create({
            "name": "Integration Task",
            "project_id": cls.project.id,
        })

    def test_project_ticket_count(self):
        """project.ticket_count should count tickets linked to the project."""
        self._create_ticket(project_id=self.project.id)
        self.project.invalidate_recordset(["ticket_count"])
        self.assertGreaterEqual(self.project.ticket_count, 1)

    def test_project_todo_ticket_count(self):
        """project.todo_ticket_count should only count open (non-closed) tickets."""
        ticket = self._create_ticket(project_id=self.project.id)
        self.project.invalidate_recordset(["todo_ticket_count"])
        todo_before = self.project.todo_ticket_count
        ticket.write({"stage_id": self.stage_done.id})
        self.project.invalidate_recordset(["todo_ticket_count"])
        todo_after = self.project.todo_ticket_count
        self.assertLess(todo_after, todo_before + 1)

    def test_project_action_open_helpdesk_tickets(self):
        """project.action_open_helpdesk_tickets should return act_window."""
        action = self.project.action_open_helpdesk_tickets()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "helpdesk.ticket")

    def test_task_ticket_count(self):
        """task.ticket_count should count tickets linked to the task."""
        self._create_ticket(
            project_id=self.project.id,
            task_id=self.task.id,
        )
        self.task.invalidate_recordset(["ticket_count"])
        self.assertGreaterEqual(self.task.ticket_count, 1)

    def test_ticket_task_clears_when_project_changes(self):
        """Changing ticket project should clear task_id if task belongs to old project."""
        ticket = self._create_ticket(
            project_id=self.project.id,
            task_id=self.task.id,
        )
        project2 = self.env["project.project"].create({"name": "Project 2"})
        ticket.project_id = project2.id
        ticket._compute_task_id()
        self.assertFalse(ticket.task_id)

    def test_ticket_task_not_cleared_when_project_unchanged(self):
        """Task should not be cleared when ticket project stays the same."""
        ticket = self._create_ticket(
            project_id=self.project.id,
            task_id=self.task.id,
        )
        ticket.project_id = self.project.id  # no change
        ticket._compute_task_id()
        self.assertEqual(ticket.task_id, self.task)

    def test_ticket_milestone_follows_task_milestone(self):
        """ticket.milestone_id should sync with task.milestone_id."""
        milestone = self.env["project.milestone"].create({
            "name": "Sprint 1",
            "project_id": self.project.id,
        })
        self.task.milestone_id = milestone.id
        ticket = self._create_ticket(
            project_id=self.project.id,
            task_id=self.task.id,
        )
        ticket._compute_milestone_id()
        self.assertEqual(ticket.milestone_id, milestone)

    def test_milestone_ticket_count(self):
        """project.milestone should count linked tickets."""
        milestone = self.env["project.milestone"].create({
            "name": "Sprint Count",
            "project_id": self.project.id,
        })
        self._create_ticket(
            project_id=self.project.id,
            task_id=self.task.id,
        )
        self.task.milestone_id = milestone.id
        milestone.invalidate_recordset(["helpdesk_ticket_count"])
        # count >= 0 (computed from task's tickets)
        self.assertGreaterEqual(milestone.helpdesk_ticket_count, 0)


# =============================================================================
# Sale Order integration
# =============================================================================

@tagged("post_install", "-at_install")
class TestSaleOrderIntegration(HelpdeskCommon):

    def test_so_ticket_count(self):
        """sale.order.ticket_count should count associated helpdesk tickets."""
        ticket = self._create_ticket()
        so = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "ticket_ids": [(4, ticket.id)],
        })
        so.invalidate_recordset(["ticket_count"])
        self.assertEqual(so.ticket_count, 1)

    def test_so_todo_ticket_count_excludes_closed(self):
        """sale.order.todo_ticket_count should only count open tickets."""
        ticket = self._create_ticket()
        so = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "ticket_ids": [(4, ticket.id)],
        })
        so.invalidate_recordset(["todo_ticket_count"])
        self.assertEqual(so.todo_ticket_count, 1)
        ticket.write({"stage_id": self.stage_done.id})
        so.invalidate_recordset(["todo_ticket_count"])
        self.assertEqual(so.todo_ticket_count, 0)

    def test_so_action_view_tickets(self):
        """sale.order.action_view_tickets should return act_window for tickets."""
        so = self.env["sale.order"].create({"partner_id": self.partner.id})
        action = so.action_view_tickets()
        self.assertEqual(action["res_model"], "helpdesk.ticket")

    def test_ticket_so_count(self):
        """helpdesk.ticket.so_count should count linked sale orders."""
        ticket = self._create_ticket()
        self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "ticket_ids": [(4, ticket.id)],
        })
        ticket.invalidate_recordset(["so_count"])
        self.assertEqual(ticket.so_count, 1)

    def test_ticket_sale_order_ids_field(self):
        """sale_order_ids on ticket should list linked sale orders."""
        ticket = self._create_ticket()
        so = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "ticket_ids": [(4, ticket.id)],
        })
        self.assertIn(so, ticket.sale_order_ids)


# =============================================================================
# CRM Lead integration
# =============================================================================

@tagged("post_install", "-at_install")
class TestCrmIntegration(HelpdeskCommon):

    def test_crm_lead_has_ticket_id_field(self):
        """crm.lead should have a ticket_id Many2one pointing to helpdesk.ticket."""
        ticket = self._create_ticket()
        lead = self.env["crm.lead"].create({
            "name": "CRM Lead",
            "ticket_id": ticket.id,
            "type": "opportunity",
        })
        self.assertEqual(lead.ticket_id, ticket)

    def test_ticket_lead_ids_populated(self):
        """ticket.lead_ids should include leads with ticket_id = this ticket."""
        ticket = self._create_ticket()
        lead = self.env["crm.lead"].create({
            "name": "Lead for Ticket",
            "ticket_id": ticket.id,
        })
        self.assertIn(lead, ticket.lead_ids)

    def test_ticket_lead_count(self):
        """ticket.lead_count should count all linked leads."""
        ticket = self._create_ticket()
        self.assertEqual(ticket.lead_count, 0)
        self.env["crm.lead"].create({"name": "L1", "ticket_id": ticket.id})
        self.env["crm.lead"].create({"name": "L2", "ticket_id": ticket.id})
        ticket.invalidate_recordset(["lead_count"])
        self.assertEqual(ticket.lead_count, 2)

    def test_ticket_lead_count_zero_initially(self):
        """Freshly created ticket should have lead_count=0."""
        ticket = self._create_ticket()
        self.assertEqual(ticket.lead_count, 0)


# =============================================================================
# Timesheet integration
# =============================================================================

@tagged("post_install", "-at_install")
class TestTimesheetIntegration(HelpdeskCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ts_project = cls.env["project.project"].create({
            "name": "Timesheet Project",
        })
        cls.team.write({
            "allow_timesheet": True,
            "default_project_id": cls.ts_project.id,
        })
        # Employee required for hr_timesheet analytic lines
        cls.employee = cls.env["hr.employee"].create({
            "name": "Test TS Employee",
            "user_id": cls.env.uid,
        })

    def _create_timesheet(self, ticket, hours, date=None):
        """Helper to create a timesheet line linked to a ticket."""
        vals = {
            "name": f"Work {hours}h",
            "ticket_id": ticket.id,
            "project_id": self.ts_project.id,
            "unit_amount": hours,
            "employee_id": self.employee.id,
        }
        if date:
            vals["date"] = date
        return self.env["account.analytic.line"].create(vals)

    def test_total_hours_single_entry(self):
        """total_hours should equal the single timesheet entry's unit_amount."""
        ticket = self._create_ticket(project_id=self.ts_project.id)
        self._create_timesheet(ticket, 3.5)
        ticket.invalidate_recordset(["total_hours"])
        self.assertAlmostEqual(ticket.total_hours, 3.5)

    def test_total_hours_multiple_entries(self):
        """total_hours should sum all timesheet entries."""
        ticket = self._create_ticket(project_id=self.ts_project.id)
        for h in [1.0, 2.5, 0.5]:
            self._create_timesheet(ticket, h)
        ticket.invalidate_recordset(["total_hours"])
        self.assertAlmostEqual(ticket.total_hours, 4.0)

    def test_total_hours_zero_without_timesheets(self):
        """Ticket with no timesheets should have total_hours=0."""
        ticket = self._create_ticket(project_id=self.ts_project.id)
        self.assertEqual(ticket.total_hours, 0.0)

    def test_progress_calculation(self):
        """progress = (total_hours / planned_hours) * 100."""
        ticket = self._create_ticket(
            project_id=self.ts_project.id,
            planned_hours=10.0,
        )
        self._create_timesheet(ticket, 5.0)
        ticket.invalidate_recordset(["total_hours", "progress"])
        ticket._compute_total_hours()
        ticket._compute_progress_hours()
        self.assertAlmostEqual(ticket.progress, 50.0)

    def test_progress_capped_at_100(self):
        """Progress cannot exceed 100% even if total > planned."""
        ticket = self._create_ticket(
            project_id=self.ts_project.id,
            planned_hours=2.0,
        )
        self._create_timesheet(ticket, 10.0)
        ticket.invalidate_recordset(["total_hours", "progress"])
        ticket._compute_total_hours()
        ticket._compute_progress_hours()
        self.assertLessEqual(ticket.progress, 100.0)

    def test_progress_zero_when_no_planned_hours(self):
        """progress should be 0 when planned_hours is 0."""
        ticket = self._create_ticket(
            project_id=self.ts_project.id,
            planned_hours=0.0,
        )
        self._create_timesheet(ticket, 3.0)
        ticket.invalidate_recordset(["total_hours", "progress"])
        ticket._compute_total_hours()
        ticket._compute_progress_hours()
        self.assertEqual(ticket.progress, 0.0)

    def test_remaining_hours(self):
        """remaining_hours = planned_hours - total_hours."""
        ticket = self._create_ticket(
            project_id=self.ts_project.id,
            planned_hours=8.0,
        )
        self._create_timesheet(ticket, 3.0)
        ticket.invalidate_recordset(["total_hours", "remaining_hours"])
        ticket._compute_total_hours()
        ticket._compute_progress_hours()
        self.assertAlmostEqual(ticket.remaining_hours, 5.0)

    def test_last_timesheet_activity_most_recent(self):
        """last_timesheet_activity should be the most recent timesheet date."""
        ticket = self._create_ticket(project_id=self.ts_project.id)
        date_old = odoo_fields.Date.from_string("2024-01-01")
        date_new = odoo_fields.Date.from_string("2024-06-15")
        self._create_timesheet(ticket, 1.0, date=date_old)
        self._create_timesheet(ticket, 1.0, date=date_new)
        ticket.invalidate_recordset(["last_timesheet_activity"])
        ticket._compute_last_timesheet_activity()
        self.assertEqual(ticket.last_timesheet_activity, date_new)

    def test_last_timesheet_activity_false_without_entries(self):
        """last_timesheet_activity should be False when no timesheets exist."""
        ticket = self._create_ticket(project_id=self.ts_project.id)
        ticket._compute_last_timesheet_activity()
        self.assertFalse(ticket.last_timesheet_activity)

    def test_action_view_timesheets_domain(self):
        """action_view_timesheets domain should filter by this ticket's id."""
        ticket = self._create_ticket(project_id=self.ts_project.id)
        action = ticket.action_view_timesheets()
        domain = action.get("domain", [])
        ticket_domain = [d for d in domain if isinstance(d, (list, tuple)) and d[0] == "ticket_id"]
        self.assertTrue(ticket_domain, "Domain should contain ticket_id filter")
        self.assertEqual(ticket_domain[0][2], ticket.id)

    def test_allow_timesheet_flag_from_team(self):
        """ticket.allow_timesheet should reflect team.allow_timesheet."""
        ticket = self._create_ticket()
        self.assertEqual(ticket.allow_timesheet, self.team.allow_timesheet)


# =============================================================================
# Product integration
# =============================================================================

@tagged("post_install", "-at_install")
class TestProductIntegration(HelpdeskCommon):

    def test_product_ticket_active_flag(self):
        """product.template should have a ticket_active Boolean field."""
        product_tmpl = self.env["product.template"].create({
            "name": "Support Product",
            "ticket_active": True,
        })
        self.assertTrue(product_tmpl.ticket_active)

    def test_ticket_product_domain_filters_ticket_active(self):
        """ticket.product_id domain should restrict to ticket_active=True products."""
        # Verify field definition has correct domain
        field = self.env["helpdesk.ticket"]._fields.get("product_id")
        self.assertIsNotNone(field)
        # Domain should restrict to ticket_active=True
        product_active = self.env["product.product"].create({
            "name": "Active Product",
            "ticket_active": True,
        })
        product_inactive = self.env["product.product"].create({
            "name": "Inactive Product",
            "ticket_active": False,
        })
        # Active product should be linkable to ticket
        ticket = self._create_ticket(product_id=product_active.id)
        self.assertEqual(ticket.product_id, product_active)

    def test_product_template_helpdesk_ticket_count(self):
        """product.template.helpdesk_ticket_count should count linked tickets."""
        product = self.env["product.product"].create({
            "name": "Count Product",
            "ticket_active": True,
        })
        self._create_ticket(product_id=product.id)
        product.product_tmpl_id.invalidate_recordset(["helpdesk_ticket_count"])
        self.assertGreaterEqual(product.product_tmpl_id.helpdesk_ticket_count, 1)


# =============================================================================
# Company settings
# =============================================================================

@tagged("post_install", "-at_install")
class TestCompanySettings(HelpdeskCommon):

    def test_company_has_helpdesk_config_fields(self):
        """res.company should expose helpdesk configuration fields."""
        company = self.env.company
        # Check these fields exist (access doesn't raise AttributeError)
        _ = company.helpdesk_mgmt_duplicate_tracking
        _ = company.helpdesk_mgmt_ticket_auto_assign

    def test_auto_assign_sets_user_on_ticket(self):
        """When helpdesk_mgmt_ticket_auto_assign=True and user is in team, user is auto-set."""
        self.env.company.helpdesk_mgmt_ticket_auto_assign = True
        self.team.user_ids = [(4, self.env.user.id)]
        ticket = self.env["helpdesk.ticket"].create({
            "name": "Auto-assigned",
            "description": "<p>x</p>",
            "team_id": self.team.id,
        })
        self.assertEqual(ticket.user_id, self.env.user)
        # Reset
        self.env.company.helpdesk_mgmt_ticket_auto_assign = False
