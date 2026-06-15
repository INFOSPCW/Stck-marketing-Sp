"""
Tests for all four transient (wizard) models:
  - helpdesk.ticket.merge
  - helpdesk.ticket.duplicate.wizard
  - helpdesk.ticket.create.lead
  - helpdesk.ticket.link.sale.order.wizard
"""
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import HelpdeskCommon


# =============================================================================
# Merge wizard
# =============================================================================

@tagged("post_install", "-at_install")
class TestHelpdeskMergeWizard(HelpdeskCommon):

    def test_merge_into_existing_archives_source(self):
        """Source tickets should be archived after merging into an existing ticket."""
        t1 = self._create_ticket(name="Merge Dst")
        t2 = self._create_ticket(name="Merge Src")
        wizard = self.env["helpdesk.ticket.merge"].with_context(active_ids=[], active_model="helpdesk.ticket").create({
            "ticket_ids": [(4, t1.id), (4, t2.id)],
            "create_new_ticket": False,
            "dst_ticket_id": t1.id,
        })
        result = wizard.merge_tickets()
        # t2 (source) should be archived
        self.assertFalse(t2.active)
        # t1 (destination) should remain
        self.assertTrue(t1.active)
        # Return action navigates to destination
        self.assertEqual(result["res_id"], t1.id)

    def test_merge_consolidates_tags_from_all_sources(self):
        """Merged destination ticket should have tags from all source tickets."""
        tag2 = self.env["helpdesk.ticket.tag"].create({"name": "MergeTag2"})
        t1 = self._create_ticket(name="Dst Tags", tag_ids=[(4, self.tag.id)])
        t2 = self._create_ticket(name="Src Tags", tag_ids=[(4, tag2.id)])
        wizard = self.env["helpdesk.ticket.merge"].with_context(active_ids=[], active_model="helpdesk.ticket").create({
            "ticket_ids": [(4, t1.id), (4, t2.id)],
            "create_new_ticket": False,
            "dst_ticket_id": t1.id,
        })
        wizard.merge_tickets()
        self.assertIn(self.tag, t1.tag_ids)
        self.assertIn(tag2, t1.tag_ids)

    def test_merge_description_combines_sources(self):
        """Destination description should include descriptions from source tickets."""
        t1 = self._create_ticket(name="Dst Desc", description="<p>Dst body</p>")
        t2 = self._create_ticket(name="Src Desc", description="<p>Src body</p>")
        wizard = self.env["helpdesk.ticket.merge"].with_context(active_ids=[], active_model="helpdesk.ticket").create({
            "ticket_ids": [(4, t1.id), (4, t2.id)],
            "create_new_ticket": False,
            "dst_ticket_id": t1.id,
        })
        wizard.merge_tickets()
        self.assertIn("Src body", t1.description)

    def test_merge_creates_new_ticket(self):
        """create_new_ticket=True should produce a fresh ticket with the given name."""
        t1 = self._create_ticket(name="Src New 1")
        t2 = self._create_ticket(name="Src New 2")
        wizard = self.env["helpdesk.ticket.merge"].with_context(active_ids=[], active_model="helpdesk.ticket").create({
            "ticket_ids": [(4, t1.id), (4, t2.id)],
            "create_new_ticket": True,
            "dst_ticket_name": "Brand New Merged",
            "dst_helpdesk_team_id": self.team.id,
        })
        result = wizard.merge_tickets()
        new_ticket = self.env["helpdesk.ticket"].browse(result["res_id"])
        # New ticket exists and has the right name
        self.assertTrue(new_ticket.exists())
        self.assertEqual(new_ticket.name, "Brand New Merged")
        # Source tickets are archived
        self.assertFalse(t1.active)
        self.assertFalse(t2.active)

    def test_merge_creates_new_ticket_archives_both_sources(self):
        """When creating new ticket, both source tickets should be archived."""
        t1 = self._create_ticket(name="Arc Src 1")
        t2 = self._create_ticket(name="Arc Src 2")
        wizard = self.env["helpdesk.ticket.merge"].with_context(active_ids=[], active_model="helpdesk.ticket").create({
            "ticket_ids": [(4, t1.id), (4, t2.id)],
            "create_new_ticket": True,
            "dst_ticket_name": "New After Merge",
            "dst_helpdesk_team_id": self.team.id,
        })
        wizard.merge_tickets()
        self.assertFalse(t1.active)
        self.assertFalse(t2.active)

    def test_merge_inherits_partner_when_all_same(self):
        """New ticket should inherit partner when all sources share one partner."""
        t1 = self._create_ticket(name="Same Partner 1", partner_id=self.partner.id)
        t2 = self._create_ticket(name="Same Partner 2", partner_id=self.partner.id)
        wizard = self.env["helpdesk.ticket.merge"].with_context(active_ids=[], active_model="helpdesk.ticket").create({
            "ticket_ids": [(4, t1.id), (4, t2.id)],
            "create_new_ticket": True,
            "dst_ticket_name": "Same Partner Merge",
            "dst_helpdesk_team_id": self.team.id,
        })
        result = wizard.merge_tickets()
        new_ticket = self.env["helpdesk.ticket"].browse(result["res_id"])
        self.assertEqual(new_ticket.partner_id, self.partner)

    def test_merge_no_partner_when_sources_differ(self):
        """New ticket partner should be empty when source partners differ."""
        partner2 = self.env["res.partner"].create({"name": "Other Partner"})
        t1 = self._create_ticket(name="P1 Ticket", partner_id=self.partner.id)
        t2 = self._create_ticket(name="P2 Ticket", partner_id=partner2.id)
        wizard = self.env["helpdesk.ticket.merge"].with_context(active_ids=[], active_model="helpdesk.ticket").create({
            "ticket_ids": [(4, t1.id), (4, t2.id)],
            "create_new_ticket": True,
            "dst_ticket_name": "Different Partners",
            "dst_helpdesk_team_id": self.team.id,
        })
        result = wizard.merge_tickets()
        new_ticket = self.env["helpdesk.ticket"].browse(result["res_id"])
        self.assertFalse(new_ticket.partner_id)

    def test_merge_posts_message_on_destination_ticket(self):
        """Destination ticket should receive a 'merged from' notification in chatter."""
        t1 = self._create_ticket(name="Msg Dst")
        t2 = self._create_ticket(name="Msg Src")
        msg_count_before = len(t1.message_ids)
        wizard = self.env["helpdesk.ticket.merge"].with_context(active_ids=[], active_model="helpdesk.ticket").create({
            "ticket_ids": [(4, t1.id), (4, t2.id)],
            "create_new_ticket": False,
            "dst_ticket_id": t1.id,
        })
        wizard.merge_tickets()
        # The destination ticket receives a "merged from" message
        self.assertGreater(len(t1.message_ids), msg_count_before)

    def test_merge_default_get_uses_active_ids(self):
        """default_get should populate ticket_ids and dst_ticket_id from context."""
        t1 = self._create_ticket(name="DG T1")
        t2 = self._create_ticket(name="DG T2")
        vals = self.env["helpdesk.ticket.merge"].with_context(
            active_ids=[t1.id, t2.id]
        ).default_get(["ticket_ids", "dst_ticket_id", "dst_helpdesk_team_id"])
        # ticket_ids is a Command list; check that both ids are in the set
        set_command = vals.get("ticket_ids", [None])[0]
        self.assertIn(t1.id, set_command[2])
        self.assertIn(t2.id, set_command[2])

    def test_merge_onchange_dst_ticket_sets_user(self):
        """Changing dst_ticket_id should pull the assigned user."""
        t1 = self._create_ticket(name="User Ticket", user_id=self.user_helpdesk.id)
        t2 = self._create_ticket(name="Other Ticket")
        wizard = self.env["helpdesk.ticket.merge"].new({
            "ticket_ids": [(4, t1.id), (4, t2.id)],
            "create_new_ticket": False,
            "dst_ticket_id": t2.id,
        })
        wizard.dst_ticket_id = t1
        wizard._onchange_dst_ticket_id()
        self.assertEqual(wizard.user_id, self.user_helpdesk)


# =============================================================================
# Duplicate wizard
# =============================================================================

@tagged("post_install", "-at_install")
class TestHelpdeskDuplicateWizard(HelpdeskCommon):

    def test_mark_as_duplicate_sets_duplicate_id(self):
        """action_confirm should set ticket.duplicate_id to duplicate_of_id."""
        original = self._create_ticket(name="Original Dup")
        dup = self._create_ticket(name="Dup Copy")
        wizard = self.env["helpdesk.ticket.duplicate.wizard"].create({
            "ticket_id": dup.id,
            "duplicate_of_id": original.id,
        })
        wizard.action_confirm()
        self.assertEqual(dup.duplicate_id, original)

    def test_mark_as_duplicate_with_stage_change(self):
        """action_confirm with target_stage_id should move the ticket to that stage."""
        original = self._create_ticket(name="Dup Original Stage")
        dup = self._create_ticket(name="Dup Stage Change")
        wizard = self.env["helpdesk.ticket.duplicate.wizard"].create({
            "ticket_id": dup.id,
            "duplicate_of_id": original.id,
            "target_stage_id": self.stage_done.id,
        })
        wizard.action_confirm()
        self.assertEqual(dup.stage_id, self.stage_done)

    def test_duplicate_of_itself_raises_user_error(self):
        """A ticket cannot be marked as a duplicate of itself."""
        ticket = self._create_ticket(name="Self Dup")
        wizard = self.env["helpdesk.ticket.duplicate.wizard"].create({
            "ticket_id": ticket.id,
            "duplicate_of_id": ticket.id,
        })
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_duplicate_of_already_duplicated_ticket_raises(self):
        """Cannot set duplicate_of_id to a ticket that already has a duplicate_id."""
        original = self._create_ticket(name="Dup Chain Original")
        dup1 = self._create_ticket(name="Dup Chain 1")
        dup1.duplicate_id = original.id  # dup1 is already a duplicate

        dup2 = self._create_ticket(name="Dup Chain 2")
        wizard = self.env["helpdesk.ticket.duplicate.wizard"].create({
            "ticket_id": dup2.id,
            "duplicate_of_id": dup1.id,  # dup1 has duplicate_id set → invalid
        })
        with self.assertRaises(UserError):
            wizard.action_confirm()

    def test_action_open_duplicate_wizard_returns_correct_model(self):
        """action_open_duplicate_wizard should return a wizard act_window."""
        ticket = self._create_ticket()
        action = ticket.action_open_duplicate_wizard()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "helpdesk.ticket.duplicate.wizard")
        self.assertEqual(action["context"]["default_ticket_id"], ticket.id)

    def test_duplicate_count_after_marking(self):
        """original.duplicate_count should increment after marking a duplicate."""
        original = self._create_ticket(name="Dup Count Original")
        dup = self._create_ticket(name="Dup Count Dup")
        wizard = self.env["helpdesk.ticket.duplicate.wizard"].create({
            "ticket_id": dup.id,
            "duplicate_of_id": original.id,
        })
        wizard.action_confirm()
        original.invalidate_recordset(["duplicate_count"])
        self.assertEqual(original.duplicate_count, 1)


# =============================================================================
# Create Lead wizard
# =============================================================================

@tagged("post_install", "-at_install")
class TestHelpdeskCreateLeadWizard(HelpdeskCommon):

    def test_create_lead_returns_act_window(self):
        """action_helpdesk_ticket_to_lead should return an act_window to the new lead."""
        ticket = self._create_ticket()
        crm_team = self.env["crm.team"].create({"name": "CRM Team Lead"})
        wizard = self.env["helpdesk.ticket.create.lead"].create({
            "ticket_id": ticket.id,
            "team_id": crm_team.id,
        })
        result = wizard.action_helpdesk_ticket_to_lead()
        self.assertEqual(result["type"], "ir.actions.act_window")

    def test_created_lead_inherits_ticket_name(self):
        """Lead name should match the ticket name."""
        ticket = self._create_ticket(name="Need Pricing Info")
        crm_team = self.env["crm.team"].create({"name": "Pricing CRM"})
        wizard = self.env["helpdesk.ticket.create.lead"].create({
            "ticket_id": ticket.id,
            "team_id": crm_team.id,
        })
        wizard.action_helpdesk_ticket_to_lead()
        lead = ticket.lead_ids[:1]
        self.assertTrue(lead)
        self.assertEqual(lead.name, ticket.name)

    def test_created_lead_inherits_ticket_partner(self):
        """Lead partner should match the ticket partner."""
        ticket = self._create_ticket(partner_id=self.partner.id)
        crm_team = self.env["crm.team"].create({"name": "Partner CRM"})
        wizard = self.env["helpdesk.ticket.create.lead"].create({
            "ticket_id": ticket.id,
            "team_id": crm_team.id,
        })
        wizard.action_helpdesk_ticket_to_lead()
        lead = ticket.lead_ids[:1]
        self.assertEqual(lead.partner_id, self.partner)

    def test_created_lead_is_type_opportunity(self):
        """Lead created from ticket should be of type 'opportunity'."""
        ticket = self._create_ticket()
        crm_team = self.env["crm.team"].create({"name": "Opp CRM"})
        wizard = self.env["helpdesk.ticket.create.lead"].create({
            "ticket_id": ticket.id,
            "team_id": crm_team.id,
        })
        wizard.action_helpdesk_ticket_to_lead()
        lead = ticket.lead_ids[:1]
        self.assertEqual(lead.type, "opportunity")

    def test_create_lead_links_lead_to_ticket(self):
        """After wizard runs, ticket.lead_ids should contain the new lead."""
        ticket = self._create_ticket()
        crm_team = self.env["crm.team"].create({"name": "Link CRM"})
        self.assertEqual(ticket.lead_count, 0)
        wizard = self.env["helpdesk.ticket.create.lead"].create({
            "ticket_id": ticket.id,
            "team_id": crm_team.id,
        })
        wizard.action_helpdesk_ticket_to_lead()
        ticket.invalidate_recordset(["lead_count"])
        self.assertEqual(ticket.lead_count, 1)

    def test_create_lead_posts_message_on_ticket(self):
        """Wizard should post a confirmation message in the ticket chatter."""
        ticket = self._create_ticket()
        crm_team = self.env["crm.team"].create({"name": "Msg CRM"})
        msg_count_before = len(ticket.message_ids)
        wizard = self.env["helpdesk.ticket.create.lead"].create({
            "ticket_id": ticket.id,
            "team_id": crm_team.id,
        })
        wizard.action_helpdesk_ticket_to_lead()
        self.assertGreater(len(ticket.message_ids), msg_count_before)

    def test_default_get_sets_ticket_from_active_id(self):
        """default_get should populate ticket_id from active_id context."""
        ticket = self._create_ticket()
        vals = self.env["helpdesk.ticket.create.lead"].with_context(
            active_id=ticket.id
        ).default_get(["ticket_id"])
        self.assertEqual(vals.get("ticket_id"), ticket.id)


# =============================================================================
# Link Sale Order wizard
# =============================================================================

@tagged("post_install", "-at_install")
class TestHelpdeskLinkSaleOrderWizard(HelpdeskCommon):

    def test_link_sale_order_adds_to_ticket(self):
        """action_confirm should add selected sale orders to ticket.sale_order_ids."""
        ticket = self._create_ticket(partner_id=self.partner.id)
        so = self.env["sale.order"].create({"partner_id": self.partner.id})
        wizard = self.env["helpdesk.ticket.link.sale.order.wizard"].create({
            "ticket_id": ticket.id,
            "commercial_partner_id": self.partner.id,
            "sale_orders_ids": [(4, so.id)],
        })
        wizard.action_confirm()
        self.assertIn(so, ticket.sale_order_ids)

    def test_link_multiple_sale_orders(self):
        """Multiple sale orders should all be linked after action_confirm."""
        ticket = self._create_ticket(partner_id=self.partner.id)
        so1 = self.env["sale.order"].create({"partner_id": self.partner.id})
        so2 = self.env["sale.order"].create({"partner_id": self.partner.id})
        wizard = self.env["helpdesk.ticket.link.sale.order.wizard"].create({
            "ticket_id": ticket.id,
            "commercial_partner_id": self.partner.id,
            "sale_orders_ids": [(4, so1.id), (4, so2.id)],
        })
        wizard.action_confirm()
        self.assertIn(so1, ticket.sale_order_ids)
        self.assertIn(so2, ticket.sale_order_ids)

    def test_action_open_link_sale_order_returns_wizard(self):
        """action_open_link_sale_order on ticket should return act_window for wizard."""
        ticket = self._create_ticket(partner_id=self.partner.id)
        action = ticket.action_open_link_sale_order()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "helpdesk.ticket.link.sale.order.wizard")

    def test_wizard_ticket_sale_orders_related(self):
        """ticket_sale_order_ids on wizard should reflect ticket's existing SOs."""
        ticket = self._create_ticket(partner_id=self.partner.id)
        so = self.env["sale.order"].create({
            "partner_id": self.partner.id,
            "ticket_ids": [(4, ticket.id)],
        })
        wizard = self.env["helpdesk.ticket.link.sale.order.wizard"].create({
            "ticket_id": ticket.id,
            "commercial_partner_id": self.partner.id,
            "sale_orders_ids": [],
        })
        self.assertIn(so, wizard.ticket_sale_order_ids)
