"""
Tests for helpdesk.ticket.stage:
  - Creation and field defaults
  - Sequence-based ordering
  - closed onchange clears close_from_portal
  - validate_field_ids enforces required fields on stage transitions
  - Company and team scope
"""
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import HelpdeskCommon


@tagged("post_install", "-at_install")
class TestHelpdeskStage(HelpdeskCommon):

    # ── Creation / defaults ───────────────────────────────────────────────────

    def test_stage_creation_defaults(self):
        """Stage should be created with active=True, closed=False, unattended=False."""
        stage = self.env["helpdesk.ticket.stage"].create({"name": "Open"})
        self.assertTrue(stage.id)
        self.assertTrue(stage.active)
        self.assertFalse(stage.closed)
        self.assertFalse(stage.unattended)

    def test_stage_company_defaults_to_current(self):
        """Stage company should default to env.company."""
        stage = self.env["helpdesk.ticket.stage"].create({"name": "Company Stage"})
        self.assertEqual(stage.company_id, self.env.company)

    # ── Ordering ──────────────────────────────────────────────────────────────

    def test_stages_ordered_by_sequence(self):
        """Stages should be returned in ascending sequence order."""
        s_hi = self.env["helpdesk.ticket.stage"].create({"name": "Hi Seq", "sequence": 99})
        s_lo = self.env["helpdesk.ticket.stage"].create({"name": "Lo Seq", "sequence": 1})
        stages = self.env["helpdesk.ticket.stage"].search([
            ("id", "in", [s_hi.id, s_lo.id])
        ])
        self.assertEqual(stages[0], s_lo, "Lower sequence should come first")

    # ── Closed onchange ───────────────────────────────────────────────────────

    def test_onchange_closed_false_clears_close_from_portal(self):
        """Setting closed=False should clear close_from_portal."""
        stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Portal Close",
            "closed": True,
            "close_from_portal": True,
        })
        stage.closed = False
        stage._onchange_closed()
        self.assertFalse(stage.close_from_portal)

    def test_onchange_closed_true_does_not_touch_portal(self):
        """Setting closed=True should leave close_from_portal unchanged."""
        stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Re-close",
            "closed": False,
            "close_from_portal": False,
        })
        stage.closed = True
        stage._onchange_closed()
        # close_from_portal was False and should remain False (no change)
        self.assertFalse(stage.close_from_portal)

    # ── validate_field_ids constraint ─────────────────────────────────────────

    def test_validate_field_ids_blocks_move_if_field_empty(self):
        """Ticket cannot move to a stage whose required fields are empty."""
        partner_field = self.env["ir.model.fields"].search([
            ("model", "=", "helpdesk.ticket"),
            ("name", "=", "partner_id"),
        ], limit=1)
        strict = self.env["helpdesk.ticket.stage"].create({
            "name": "Requires Partner",
            "sequence": 50,
            "validate_field_ids": [(4, partner_field.id)],
        })
        ticket = self._create_ticket()
        ticket.write({"partner_id": False})
        with self.assertRaises(ValidationError):
            ticket.stage_id = strict.id

    def test_validate_field_ids_allows_move_when_filled(self):
        """Ticket can move to validated stage when required fields are set."""
        partner_field = self.env["ir.model.fields"].search([
            ("model", "=", "helpdesk.ticket"),
            ("name", "=", "partner_id"),
        ], limit=1)
        strict = self.env["helpdesk.ticket.stage"].create({
            "name": "Requires Partner OK",
            "sequence": 55,
            "validate_field_ids": [(4, partner_field.id)],
        })
        ticket = self._create_ticket(partner_id=self.partner.id)
        ticket.stage_id = strict.id  # should not raise
        self.assertEqual(ticket.stage_id, strict)

    def test_stage_no_validate_fields_allows_any_ticket(self):
        """Stage without validate_field_ids should accept tickets without those fields."""
        open_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Open No Validation",
            "sequence": 5,
        })
        ticket = self._create_ticket()
        ticket.write({"partner_id": False})
        ticket.stage_id = open_stage.id  # should not raise
        self.assertEqual(ticket.stage_id, open_stage)

    # ── Team scoping ──────────────────────────────────────────────────────────

    def test_stage_linked_to_team_appears_in_applicable_stages(self):
        """Stage restricted to a team appears in that team's _get_applicable_stages."""
        stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Team Scoped",
            "sequence": 7,
            "team_ids": [(4, self.team.id)],
        })
        applicable = self.team._get_applicable_stages()
        self.assertIn(stage, applicable)

    def test_stage_with_mail_template(self):
        """Stage should store mail_template_id relationship."""
        template = self.env["mail.template"].search([
            ("model", "=", "helpdesk.ticket")
        ], limit=1)
        if not template:
            self.skipTest("No helpdesk mail template available")
        stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Email Stage",
            "mail_template_id": template.id,
        })
        self.assertEqual(stage.mail_template_id, template)

    # ── Archiving ─────────────────────────────────────────────────────────────

    def test_stage_can_be_archived(self):
        """Setting active=False should archive the stage."""
        stage = self.env["helpdesk.ticket.stage"].create({"name": "Archivable Stage"})
        stage.active = False
        found = self.env["helpdesk.ticket.stage"].search([("id", "=", stage.id)])
        self.assertFalse(found, "Archived stage should not appear in default search")
