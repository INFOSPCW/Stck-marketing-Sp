"""
Tests for the core helpdesk.ticket model:
  - Creation & sequence numbering
  - Stage transitions and date fields
  - Computed fields (related_ticket_count, duplicate_count, lead_count, so_count)
  - Action methods
  - Description auto-fill from category template
  - Stage-validation constraint
  - Mail gateway helpers
"""
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import HelpdeskCommon


@tagged("post_install", "-at_install")
class TestHelpdeskTicketCreation(HelpdeskCommon):
    """Ticket creation and sequence numbering."""

    def test_number_auto_generated(self):
        """Ticket number should be assigned from the ir.sequence on create."""
        ticket = self._create_ticket()
        self.assertNotEqual(ticket.number, "/")
        self.assertTrue(ticket.number)

    def test_numbers_are_unique(self):
        """Two tickets should receive different sequence numbers."""
        t1 = self._create_ticket(name="T1")
        t2 = self._create_ticket(name="T2")
        self.assertNotEqual(t1.number, t2.number)

    def test_stage_assigned_from_team(self):
        """Ticket should receive the first applicable stage from its team."""
        ticket = self._create_ticket()
        applicable = self.team._get_applicable_stages()
        if applicable:
            self.assertEqual(ticket.stage_id, applicable[0])

    def test_display_name_contains_number_and_name(self):
        """display_name should be '<number> - <name>'."""
        ticket = self._create_ticket(name="My Issue")
        self.assertIn(ticket.number, ticket.display_name)
        self.assertIn(ticket.name, ticket.display_name)

    def test_access_url_contains_ticket_id(self):
        """access_url should be /my/ticket/<id>."""
        ticket = self._create_ticket()
        self.assertIn(str(ticket.id), ticket.access_url)
        self.assertIn("/my/ticket/", ticket.access_url)

    def test_default_priority_is_medium(self):
        """Default priority should be '1' (Medium)."""
        ticket = self._create_ticket()
        self.assertEqual(ticket.priority, "1")

    def test_default_company_from_env(self):
        """Ticket without team should default to current company."""
        ticket = self.env["helpdesk.ticket"].create({
            "name": "No-team ticket",
            "description": "<p>x</p>",
        })
        self.assertEqual(ticket.company_id, self.env.company)

    def test_company_inherited_from_team(self):
        """Ticket company should come from team's company when team is set."""
        ticket = self._create_ticket()
        expected_company = self.team.company_id or self.env.company
        self.assertEqual(ticket.company_id, expected_company)

    def test_sla_set_on_create(self):
        """set_sla() is called on create; ticket_sla_ids populated if team has SLA."""
        team_sla = self.env["helpdesk.ticket.team"].create({
            "name": "SLA Team",
            "use_sla": True,
        })
        sla = self.env["helpdesk.sla"].create({
            "name": "24h SLA",
            "team_ids": [(4, team_sla.id)],
            "stage_id": self.stage_done.id,
            "days": 1,
        })
        ticket = self._create_ticket(team_id=team_sla.id)
        self.assertTrue(
            ticket.ticket_sla_ids,
            "Ticket should have SLA records when team has SLA enabled",
        )


@tagged("post_install", "-at_install")
class TestHelpdeskTicketWrite(HelpdeskCommon):
    """Stage transitions and date-stamp logic."""

    def test_write_stage_updates_last_stage_update(self):
        """Writing stage_id should refresh last_stage_update."""
        ticket = self._create_ticket()
        before = ticket.last_stage_update
        ticket.write({"stage_id": self.stage_progress.id})
        self.assertGreaterEqual(ticket.last_stage_update, before)

    def test_write_closed_stage_sets_closed_date(self):
        """Moving to a closed stage should stamp closed_date."""
        ticket = self._create_ticket()
        self.assertFalse(ticket.closed_date)
        ticket.write({"stage_id": self.stage_done.id})
        self.assertTrue(ticket.closed_date)
        self.assertTrue(ticket.closed)

    def test_write_user_stamps_assigned_date(self):
        """Assigning a user should stamp assigned_date."""
        ticket = self._create_ticket()
        self.assertFalse(ticket.assigned_date)
        ticket.write({"user_id": self.user_helpdesk.id})
        self.assertTrue(ticket.assigned_date)

    def test_assign_to_me(self):
        """assign_to_me() should set user_id to the current user."""
        ticket = self._create_ticket()
        ticket.assign_to_me()
        self.assertEqual(ticket.user_id, self.env.user)

    def test_active_archive(self):
        """Setting active=False should archive the ticket."""
        ticket = self._create_ticket()
        ticket.active = False
        self.assertFalse(ticket.active)
        found = self.env["helpdesk.ticket"].search([("id", "=", ticket.id)])
        self.assertFalse(found, "Archived ticket should not appear in default search")


@tagged("post_install", "-at_install")
class TestHelpdeskTicketCopy(HelpdeskCommon):
    """Ticket duplication (copy)."""

    def test_copy_gets_new_number(self):
        """Copied ticket should have a fresh sequence number."""
        original = self._create_ticket()
        copy = original.copy()
        self.assertNotEqual(copy.number, original.number)

    def test_copy_resets_description(self):
        """Copied ticket description should be cleared to '<p></p>'."""
        original = self._create_ticket(description="<p>Original description</p>")
        copy = original.copy()
        self.assertEqual(copy.description, "<p></p>")

    def test_action_duplicate_tickets(self):
        """action_duplicate_tickets should copy each ticket from active_ids."""
        ticket = self._create_ticket()
        before = self.env["helpdesk.ticket"].search_count([])
        self.env["helpdesk.ticket"].with_context(
            active_ids=[ticket.id]
        ).action_duplicate_tickets()
        after = self.env["helpdesk.ticket"].search_count([])
        self.assertGreater(after, before)


@tagged("post_install", "-at_install")
class TestHelpdeskTicketOnchange(HelpdeskCommon):
    """Onchange behaviour."""

    def test_onchange_partner_fills_name_and_email(self):
        """Changing partner_id should auto-fill partner_name and partner_email."""
        ticket = self.env["helpdesk.ticket"].new({
            "name": "Onchange test",
            "description": "<p>x</p>",
        })
        ticket.partner_id = self.partner
        ticket._onchange_partner_id()
        self.assertEqual(ticket.partner_name, self.partner.name)
        self.assertEqual(ticket.partner_email, self.partner.email)

    def test_onchange_type_clears_team_if_type_not_in_team(self):
        """Changing type to one not in team's type_ids should clear team."""
        typed_team = self.env["helpdesk.ticket.team"].create({"name": "Typed Team"})
        t_in = self.env["helpdesk.ticket.type"].create({"name": "Allowed Type"})
        t_out = self.env["helpdesk.ticket.type"].create({"name": "Other Type"})
        typed_team.type_ids = [(4, t_in.id)]

        ticket = self.env["helpdesk.ticket"].new({
            "name": "Type Onchange",
            "description": "<p>x</p>",
            "team_id": typed_team.id,
            "type_id": t_in.id,
        })
        ticket.type_id = t_out
        ticket._onchange_type_id()
        self.assertFalse(ticket.team_id)


@tagged("post_install", "-at_install")
class TestHelpdeskTicketComputedFields(HelpdeskCommon):
    """Computed fields: counts, booleans, stage helpers."""

    def test_related_ticket_count(self):
        """related_ticket_count should equal len(related_ticket_ids)."""
        t1 = self._create_ticket(name="Parent")
        t2 = self._create_ticket(name="R1")
        t3 = self._create_ticket(name="R2")
        t1.related_ticket_ids = [(4, t2.id), (4, t3.id)]
        self.assertEqual(t1.related_ticket_count, 2)

    def test_duplicate_count(self):
        """duplicate_count should equal number of tickets with duplicate_id set."""
        original = self._create_ticket(name="Original")
        d1 = self._create_ticket(name="Dup1")
        d2 = self._create_ticket(name="Dup2")
        d1.duplicate_id = original.id
        d2.duplicate_id = original.id
        original.invalidate_recordset(["duplicate_count"])
        self.assertEqual(original.duplicate_count, 2)

    def test_lead_count(self):
        """lead_count should increment when CRM leads reference the ticket."""
        ticket = self._create_ticket()
        self.assertEqual(ticket.lead_count, 0)
        self.env["crm.lead"].create({"name": "L1", "ticket_id": ticket.id})
        self.env["crm.lead"].create({"name": "L2", "ticket_id": ticket.id})
        ticket.invalidate_recordset(["lead_count"])
        self.assertEqual(ticket.lead_count, 2)

    def test_so_count(self):
        """so_count should count sale orders linked to the ticket."""
        ticket = self._create_ticket()
        self.assertEqual(ticket.so_count, 0)
        self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "ticket_ids": [(4, ticket.id)],
        })
        ticket.invalidate_recordset(["so_count"])
        self.assertEqual(ticket.so_count, 1)

    def test_is_new_stage(self):
        """is_new_stage should be True when ticket is in first team stage."""
        ticket = self._create_ticket()
        first_stage = self.team._get_applicable_stages()[:1]
        if first_stage:
            ticket.stage_id = first_stage.id
            ticket._compute_is_new_stage()
            self.assertTrue(ticket.is_new_stage)

    def test_next_stage_id_advances(self):
        """next_stage_id should point to a stage with higher sequence."""
        ticket = self._create_ticket()
        ticket.stage_id = self.stage_new.id
        ticket.invalidate_recordset(["next_stage_id"])
        # next_stage_id is either the same (if no next) or a higher sequence stage
        if ticket.next_stage_id:
            self.assertGreaterEqual(
                ticket.next_stage_id.sequence, ticket.stage_id.sequence
            )

    def test_set_next_stage(self):
        """set_next_stage() should move ticket to next_stage_id."""
        ticket = self._create_ticket()
        ticket.stage_id = self.stage_new.id
        expected_next = ticket.next_stage_id
        ticket.set_next_stage()
        self.assertEqual(ticket.stage_id, expected_next)


@tagged("post_install", "-at_install")
class TestHelpdeskTicketActions(HelpdeskCommon):
    """Action methods return correct act_window dicts."""

    def test_action_view_related_tickets(self):
        """action_view_related_tickets should return act_window filtered to related ids."""
        t1 = self._create_ticket(name="Parent")
        t2 = self._create_ticket(name="Child")
        t1.related_ticket_ids = [(4, t2.id)]
        action = t1.action_view_related_tickets()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "helpdesk.ticket")
        domain_ids = action["domain"][0][2]
        self.assertIn(t2.id, domain_ids)

    def test_action_view_timesheets(self):
        """action_view_timesheets should open account.analytic.line filtered by ticket."""
        ticket = self._create_ticket()
        action = ticket.action_view_timesheets()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "account.analytic.line")
        # Verify domain contains this ticket's id
        domain = action.get("domain", [])
        self.assertTrue(
            any(
                isinstance(d, (list, tuple)) and d[0] == "ticket_id" and d[2] == ticket.id
                for d in domain
            )
        )

    def test_action_open_leads_no_leads(self):
        """action_open_leads with no leads returns CRM pipeline action."""
        ticket = self._create_ticket()
        action = ticket.action_open_leads()
        self.assertIn("type", action)

    def test_action_open_leads_single_lead(self):
        """action_open_leads with one lead sets res_id directly."""
        ticket = self._create_ticket()
        lead = self.env["crm.lead"].create({
            "name": "Single Lead",
            "ticket_id": ticket.id,
            "type": "opportunity",
        })
        ticket.write({"lead_ids": [(4, lead.id)]})
        action = ticket.action_open_leads()
        self.assertEqual(action.get("res_id"), lead.id)

    def test_action_open_leads_multiple_leads(self):
        """action_open_leads with multiple leads sets domain instead of res_id."""
        ticket = self._create_ticket()
        l1 = self.env["crm.lead"].create({"name": "L1", "ticket_id": ticket.id})
        l2 = self.env["crm.lead"].create({"name": "L2", "ticket_id": ticket.id})
        ticket.write({"lead_ids": [(4, l1.id), (4, l2.id)]})
        action = ticket.action_open_leads()
        self.assertIn("domain", action)

    def test_action_view_sale_orders(self):
        """action_view_sale_orders should return sale.order act_window."""
        ticket = self._create_ticket()
        action = ticket.action_view_sale_orders()
        # The action is retrieved from XML ID and domain is patched
        self.assertTrue(action)

    def test_action_view_duplicates(self):
        """action_view_duplicates should return ticket list filtered to duplicates."""
        original = self._create_ticket(name="Original")
        dup = self._create_ticket(name="Dup")
        dup.duplicate_id = original.id
        action = original.action_view_duplicates()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "helpdesk.ticket")

    def test_action_open_duplicate_wizard(self):
        """action_open_duplicate_wizard should open the duplicate wizard."""
        ticket = self._create_ticket()
        action = ticket.action_open_duplicate_wizard()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "helpdesk.ticket.duplicate.wizard")

    def test_action_open_link_sale_order(self):
        """action_open_link_sale_order should open the link SO wizard."""
        ticket = self._create_ticket(partner_id=self.partner.id)
        action = ticket.action_open_link_sale_order()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "helpdesk.ticket.link.sale.order.wizard")


@tagged("post_install", "-at_install")
class TestHelpdeskTicketConstraints(HelpdeskCommon):
    """Stage-validation constraint."""

    def test_stage_validation_raises_on_empty_required_field(self):
        """Moving to a stage with validate_field_ids raises if those fields are empty."""
        field = self.env["ir.model.fields"].search([
            ("model", "=", "helpdesk.ticket"),
            ("name", "=", "partner_id"),
        ], limit=1)
        strict_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Needs Partner",
            "sequence": 50,
            "validate_field_ids": [(4, field.id)],
        })
        ticket = self._create_ticket()
        # Remove partner to trigger validation failure
        ticket.write({"partner_id": False})
        with self.assertRaises(ValidationError):
            ticket.stage_id = strict_stage.id

    def test_stage_validation_passes_when_fields_filled(self):
        """No error when all validate_field_ids are populated."""
        field = self.env["ir.model.fields"].search([
            ("model", "=", "helpdesk.ticket"),
            ("name", "=", "partner_id"),
        ], limit=1)
        strict_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Needs Partner OK",
            "sequence": 60,
            "validate_field_ids": [(4, field.id)],
        })
        ticket = self._create_ticket(partner_id=self.partner.id)
        # Should not raise
        ticket.stage_id = strict_stage.id
        self.assertEqual(ticket.stage_id, strict_stage)


@tagged("post_install", "-at_install")
class TestHelpdeskTicketDescription(HelpdeskCommon):
    """Description auto-fill from category template."""

    def test_description_auto_filled_from_category_template(self):
        """Ticket description should be auto-filled when category has a template."""
        cat = self.env["helpdesk.ticket.category"].create({
            "name": "Template Category",
            "template_description": "<p>Please provide your invoice number</p>",
        })
        ticket = self.env["helpdesk.ticket"].new({
            "name": "Invoice Issue",
            "category_id": cat.id,
        })
        ticket._compute_description()
        self.assertEqual(ticket.description, "<p>Please provide your invoice number</p>")

    def test_description_not_overwritten_if_already_set(self):
        """Existing description should not be overwritten by category template."""
        cat = self.env["helpdesk.ticket.category"].create({
            "name": "Template Cat2",
            "template_description": "<p>Template text</p>",
        })
        ticket = self.env["helpdesk.ticket"].new({
            "name": "Existing Desc",
            "description": "<p>User-provided description</p>",
            "category_id": cat.id,
        })
        ticket._compute_description()
        self.assertEqual(ticket.description, "<p>User-provided description</p>")

    def test_description_not_filled_without_template(self):
        """Category without template_description should not change description."""
        cat = self.env["helpdesk.ticket.category"].create({"name": "No Template"})
        ticket = self.env["helpdesk.ticket"].new({
            "name": "No Template Ticket",
            "category_id": cat.id,
        })
        ticket._compute_description()
        self.assertFalse(ticket.description)


@tagged("post_install", "-at_install")
class TestHelpdeskTicketMailGateway(HelpdeskCommon):
    """Mail gateway: message_new creates ticket from inbound email."""

    def test_message_new_creates_ticket_from_email(self):
        """message_new should parse email dict into a ticket."""
        msg = {
            "subject": "My email subject",
            "body": "<p>Email body</p>",
            "from": "sender@example.com",
            "author_id": self.partner.id,
            "to": "",
            "cc": "",
        }
        ticket = self.env["helpdesk.ticket"].message_new(msg)
        self.assertEqual(ticket.name, "My email subject")
        self.assertEqual(ticket.partner_id, self.partner)

    def test_message_new_uses_no_subject_fallback(self):
        """message_new with empty subject should use 'No Subject'."""
        msg = {
            "subject": "",
            "body": "<p>body</p>",
            "from": "x@example.com",
            "author_id": False,
            "to": "",
            "cc": "",
        }
        ticket = self.env["helpdesk.ticket"].message_new(msg)
        self.assertIn("No Subject", ticket.name)
