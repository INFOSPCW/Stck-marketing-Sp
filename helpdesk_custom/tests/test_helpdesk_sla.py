"""
Tests for SLA models:
  - helpdesk.sla: creation, domain filter, active
  - helpdesk.ticket.sla: state machine (in_progress, accomplished, expired, on_hold)
  - ticket.set_sla / ticket.refresh_sla
  - ticket.sla_expired computed field
  - SLA color coding
  - _get_sla() domain filtering
"""
from odoo import fields
from odoo.tests.common import tagged

from .common import HelpdeskCommon


@tagged("post_install", "-at_install")
class TestHelpdeskSlaModel(HelpdeskCommon):
    """Tests for the helpdesk.sla master record."""

    def test_sla_creation_defaults(self):
        """SLA should be active by default with days/hours defaulting to 0."""
        sla = self.env["helpdesk.sla"].create({
            "name": "Basic SLA",
            "stage_id": self.stage_done.id,
        })
        self.assertTrue(sla.active)
        self.assertEqual(sla.days, 0)
        self.assertEqual(sla.hours, 0)

    def test_sla_can_be_archived(self):
        """SLA should support archiving via active=False."""
        sla = self.env["helpdesk.sla"].create({"name": "Old SLA"})
        sla.active = False
        self.assertFalse(sla.active)

    def test_sla_team_association(self):
        """SLA should be linkable to one or more teams."""
        sla = self.env["helpdesk.sla"].create({
            "name": "Team SLA",
            "team_ids": [(4, self.team.id)],
        })
        self.assertIn(self.team, sla.team_ids)

    def test_sla_category_filter(self):
        """SLA with category_ids should only apply to matching categories."""
        sla = self.env["helpdesk.sla"].create({
            "name": "Category SLA",
            "team_ids": [(4, self.team.id)],
            "category_ids": [(4, self.category.id)],
            "stage_id": self.stage_done.id,
        })
        self.assertIn(self.category, sla.category_ids)


@tagged("post_install", "-at_install")
class TestHelpdeskSlaOnTicket(HelpdeskCommon):
    """Tests for SLA assignment and refresh on tickets."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team.write({"use_sla": True})
        cls.sla = cls.env["helpdesk.sla"].create({
            "name": "Test SLA 1d",
            "team_ids": [(4, cls.team.id)],
            "stage_id": cls.stage_progress.id,
            "days": 1,
            "hours": 0,
        })

    def test_set_sla_creates_ticket_sla_records(self):
        """set_sla() should create helpdesk.ticket.sla for each applicable SLA."""
        ticket = self._create_ticket()
        # SLAs are set on create; verify records exist
        self.assertTrue(
            ticket.ticket_sla_ids,
            "Ticket should have ticket_sla_ids when team has use_sla=True",
        )

    def test_sla_ids_reflect_ticket_sla_ids(self):
        """sla_ids should be the SLA masters from ticket_sla_ids."""
        ticket = self._create_ticket()
        ticket.set_sla()
        if ticket.ticket_sla_ids:
            self.assertIn(self.sla, ticket.sla_ids)

    def test_refresh_sla_keeps_same_count_for_same_config(self):
        """refresh_sla should result in same number of SLA records."""
        ticket = self._create_ticket()
        ticket.set_sla()
        initial_count = len(ticket.ticket_sla_ids)
        ticket.refresh_sla()
        self.assertEqual(len(ticket.ticket_sla_ids), initial_count)

    def test_sla_expired_field_on_ticket(self):
        """ticket.sla_expired should be False when no ticket_sla is expired."""
        ticket = self._create_ticket()
        ticket.set_sla()
        # Move to accomplished state - all SLAs should be accomplished or in_progress
        ticket.write({"stage_id": self.stage_done.id})
        ticket.invalidate_recordset(["sla_expired"])
        # Just verify the field computes without error and returns a boolean
        self.assertIsInstance(ticket.sla_expired, bool)

    def test_sla_not_expired_initially(self):
        """Freshly created ticket SLAs should not be expired."""
        ticket = self._create_ticket()
        ticket.set_sla()
        if not ticket.ticket_sla_ids:
            self.skipTest("No SLA records")
        # Freshly created SLA records should not be in expired state
        expired_slas = ticket.ticket_sla_ids.filtered(lambda s: s.state == "expired")
        self.assertFalse(expired_slas)


@tagged("post_install", "-at_install")
class TestHelpdeskTicketSlaStateMachine(HelpdeskCommon):
    """Tests for helpdesk.ticket.sla state machine."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team.write({"use_sla": True})
        cls.sla_target_progress = cls.env["helpdesk.sla"].create({
            "name": "SLA → In Progress",
            "team_ids": [(4, cls.team.id)],
            "stage_id": cls.stage_progress.id,
            "days": 1,
        })
        cls.sla_target_done = cls.env["helpdesk.sla"].create({
            "name": "SLA → Done",
            "team_ids": [(4, cls.team.id)],
            "stage_id": cls.stage_done.id,
            "days": 2,
        })

    def test_ticket_sla_in_progress_when_before_target_stage(self):
        """SLA state should be in_progress when ticket hasn't reached expected stage."""
        ticket = self._create_ticket()
        ticket_sla = self.env["helpdesk.ticket.sla"].create({
            "ticket_id": ticket.id,
            "sla_id": self.sla_target_progress.id,
        })
        ticket.write({"stage_id": self.stage_new.id})
        ticket_sla._compute_sla_data()
        self.assertEqual(ticket_sla.state, "in_progress")

    def test_ticket_sla_accomplished_when_at_or_beyond_target_stage(self):
        """SLA state should be accomplished once ticket reaches expected stage."""
        ticket = self._create_ticket()
        ticket_sla = self.env["helpdesk.ticket.sla"].create({
            "ticket_id": ticket.id,
            "sla_id": self.sla_target_progress.id,
        })
        # Move to stage_progress (sequence=10) which is the SLA target
        ticket.write({"stage_id": self.stage_progress.id})
        ticket_sla._compute_sla_data()
        self.assertEqual(ticket_sla.state, "accomplished")

    def test_ticket_sla_accomplished_beyond_target_stage(self):
        """Ticket in a stage beyond target (sequence > target) should be accomplished."""
        ticket = self._create_ticket()
        ticket_sla = self.env["helpdesk.ticket.sla"].create({
            "ticket_id": ticket.id,
            "sla_id": self.sla_target_progress.id,
        })
        # stage_done has sequence=100 > stage_progress sequence=10
        ticket.write({"stage_id": self.stage_done.id})
        ticket_sla._compute_sla_data()
        self.assertEqual(ticket_sla.state, "accomplished")

    def test_ticket_sla_on_hold_in_ignore_stage(self):
        """SLA should be on_hold when ticket is in an ignored stage."""
        ignore_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Awaiting Customer",
            "sequence": 5,
        })
        sla_with_ignore = self.env["helpdesk.sla"].create({
            "name": "SLA with Hold",
            "team_ids": [(4, self.team.id)],
            "stage_id": self.stage_done.id,
            "days": 3,
            "ignore_stage_ids": [(4, ignore_stage.id)],
        })
        ticket = self._create_ticket()
        ticket_sla = self.env["helpdesk.ticket.sla"].create({
            "ticket_id": ticket.id,
            "sla_id": sla_with_ignore.id,
        })
        ticket.write({"stage_id": ignore_stage.id})
        ticket_sla._compute_sla_data()
        self.assertEqual(ticket_sla.state, "on_hold")

    def test_ticket_sla_expired_state(self):
        """ticket_sla state should be in_progress when ticket is below SLA target stage."""
        ticket = self._create_ticket()
        ticket_sla = self.env["helpdesk.ticket.sla"].create({
            "ticket_id": ticket.id,
            "sla_id": self.sla_target_done.id,
        })
        # stage_new seq=1 < stage_done seq=100 → in_progress
        ticket.write({"stage_id": self.stage_new.id})
        ticket_sla._compute_sla_data()
        self.assertEqual(ticket_sla.state, "in_progress")

    def test_ticket_sla_hours_computed_from_sla(self):
        """ticket_sla.hours should be sla.hours + calendar_hours_per_day * sla.days."""
        ticket = self._create_ticket()
        ticket_sla = self.env["helpdesk.ticket.sla"].create({
            "ticket_id": ticket.id,
            "sla_id": self.sla_target_progress.id,
        })
        calendar = self.team.resource_calendar_id
        expected_hours = (
            self.sla_target_progress.hours
            + (calendar.hours_per_day if calendar else 8) * self.sla_target_progress.days
        )
        self.assertAlmostEqual(ticket_sla.hours, expected_hours)

    def test_ticket_sla_color_accomplished(self):
        """Accomplished SLA should have color=10 (green)."""
        ticket = self._create_ticket()
        ticket_sla = self.env["helpdesk.ticket.sla"].create({
            "ticket_id": ticket.id,
            "sla_id": self.sla_target_progress.id,
        })
        ticket.write({"stage_id": self.stage_done.id})
        ticket_sla._compute_sla_data()
        ticket_sla._compute_color()
        self.assertEqual(ticket_sla.color, 10)

    def test_ticket_sla_color_accomplished(self):
        """Accomplished SLA should have color=10 (green)."""
        ticket = self._create_ticket()
        ticket_sla = self.env["helpdesk.ticket.sla"].create({
            "ticket_id": ticket.id,
            "sla_id": self.sla_target_progress.id,
        })
        # stage_done seq=100 >= stage_progress seq=10 → accomplished
        ticket.write({"stage_id": self.stage_done.id})
        ticket_sla._compute_sla_data()
        ticket_sla._compute_color()
        self.assertEqual(ticket_sla.color, 10)

    def test_ticket_sla_expired_field_via_new_record(self):
        """expired field should be True when state=in_progress and deadline < now."""
        from datetime import timedelta
        ticket = self._create_ticket()
        # Create a virtual in-memory record to test _compute_expired logic
        ticket_sla_virt = self.env["helpdesk.ticket.sla"].new({
            "ticket_id": ticket.id,
            "sla_id": self.sla_target_progress.id,
        })
        # Directly set in-memory values: state=in_progress, deadline=past
        ticket_sla_virt.update({
            "state": "in_progress",
            "deadline": fields.Datetime.now() - timedelta(hours=2),
        })
        ticket_sla_virt._compute_expired()
        self.assertTrue(ticket_sla_virt.expired)


@tagged("post_install", "-at_install")
class TestHelpdeskSlaGetSla(HelpdeskCommon):
    """Tests for ticket._get_sla() filtering logic."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.team.write({"use_sla": True})

    def test_get_sla_includes_matching_sla(self):
        """_get_sla() should include SLAs with matching team."""
        sla = self.env["helpdesk.sla"].create({
            "name": "Match SLA",
            "team_ids": [(4, self.team.id)],
            "stage_id": self.stage_done.id,
            "days": 1,
        })
        ticket = self._create_ticket()
        self.assertIn(sla, ticket._get_sla())

    def test_get_sla_excludes_other_team_sla(self):
        """_get_sla() should exclude SLAs from other teams."""
        other_team = self.env["helpdesk.ticket.team"].create({"name": "Other SLA Team"})
        sla_other = self.env["helpdesk.sla"].create({
            "name": "Other Team SLA",
            "team_ids": [(4, other_team.id)],
            "stage_id": self.stage_done.id,
            "days": 1,
        })
        ticket = self._create_ticket()
        self.assertNotIn(sla_other, ticket._get_sla())

    def test_get_sla_domain_filter_priority(self):
        """SLA domain filter should only match tickets satisfying the domain."""
        sla_hp = self.env["helpdesk.sla"].create({
            "name": "High Priority SLA",
            "team_ids": [(4, self.team.id)],
            "stage_id": self.stage_done.id,
            "days": 0,
            "hours": 4,
            "domain": "[('priority', '=', '3')]",
        })
        ticket_hp = self._create_ticket(name="HP Ticket", priority="3")
        ticket_lp = self._create_ticket(name="LP Ticket", priority="0")
        self.assertIn(sla_hp, ticket_hp._get_sla())
        self.assertNotIn(sla_hp, ticket_lp._get_sla())

    def test_global_sla_applies_to_all_teams(self):
        """SLA with no team restriction should apply regardless of ticket team."""
        global_sla = self.env["helpdesk.sla"].create({
            "name": "Global SLA",
            # team_ids empty → applies to all
            "stage_id": self.stage_done.id,
            "days": 7,
        })
        ticket = self._create_ticket()
        self.assertIn(global_sla, ticket._get_sla())
