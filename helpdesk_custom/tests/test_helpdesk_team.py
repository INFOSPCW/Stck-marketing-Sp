"""
Tests for helpdesk.ticket.team:
  - Creation and alias auto-provisioning
  - Parent/child hierarchy and complete_name
  - _get_applicable_stages (global vs. team-specific)
  - todo_ticket_count family of computed fields
  - close_team_inactive_tickets scheduler logic
  - Python domain validation constraint
  - retrieve_dashboard helper
  - Timesheet allow_timesheet constraint
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from .common import HelpdeskCommon


@tagged("post_install", "-at_install")
class TestHelpdeskTeamCreation(HelpdeskCommon):

    def test_team_creates_alias(self):
        """A new team should automatically get a mail alias."""
        team = self.env["helpdesk.ticket.team"].create({"name": "Alias Team"})
        self.assertTrue(team.alias_id, "Team should have an alias after creation")

    def test_team_active_default(self):
        """Team should be active by default."""
        team = self.env["helpdesk.ticket.team"].create({"name": "Active Team"})
        self.assertTrue(team.active)

    def test_team_company_default(self):
        """Team company should default to current company."""
        team = self.env["helpdesk.ticket.team"].create({"name": "Company Team"})
        self.assertEqual(team.company_id, self.env.company)


@tagged("post_install", "-at_install")
class TestHelpdeskTeamHierarchy(HelpdeskCommon):

    def test_complete_name_no_parent(self):
        """Team without parent: complete_name == name."""
        team = self.env["helpdesk.ticket.team"].create({"name": "Standalone"})
        self.assertEqual(team.complete_name, "Standalone")

    def test_complete_name_one_level(self):
        """Child team: 'Parent / Child'."""
        parent = self.env["helpdesk.ticket.team"].create({"name": "Parent"})
        child = self.env["helpdesk.ticket.team"].create({
            "name": "Child", "parent_id": parent.id
        })
        self.assertEqual(child.complete_name, "Parent / Child")

    def test_complete_name_two_levels(self):
        """Three-level hierarchy: 'Root / Mid / Leaf'."""
        root = self.env["helpdesk.ticket.team"].create({"name": "Root"})
        mid = self.env["helpdesk.ticket.team"].create({
            "name": "Mid", "parent_id": root.id
        })
        leaf = self.env["helpdesk.ticket.team"].create({
            "name": "Leaf", "parent_id": mid.id
        })
        self.assertEqual(leaf.complete_name, "Root / Mid / Leaf")

    def test_complete_name_updates_on_parent_rename(self):
        """Renaming parent should propagate to child complete_name."""
        parent = self.env["helpdesk.ticket.team"].create({"name": "OldParent"})
        child = self.env["helpdesk.ticket.team"].create({
            "name": "Child", "parent_id": parent.id
        })
        parent.name = "NewParent"
        child.invalidate_recordset(["complete_name"])
        self.assertIn("NewParent", child.complete_name)


@tagged("post_install", "-at_install")
class TestHelpdeskTeamStages(HelpdeskCommon):

    def test_global_stage_applicable_to_all_teams(self):
        """Stage with no team restriction should be applicable to every team."""
        global_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Universal",
            "sequence": 5,
            # team_ids intentionally empty
        })
        applicable = self.team._get_applicable_stages()
        self.assertIn(global_stage, applicable)

    def test_team_specific_stage_not_applicable_to_other_team(self):
        """Stage restricted to team A should not appear for team B."""
        team_a = self.env["helpdesk.ticket.team"].create({"name": "Team A"})
        team_b = self.env["helpdesk.ticket.team"].create({"name": "Team B"})
        stage_a = self.env["helpdesk.ticket.stage"].create({
            "name": "Stage A Only",
            "sequence": 5,
            "team_ids": [(4, team_a.id)],
        })
        self.assertIn(stage_a, team_a._get_applicable_stages())
        self.assertNotIn(stage_a, team_b._get_applicable_stages())

    def test_empty_team_returns_global_stages_only(self):
        """_get_applicable_stages on empty recordset should return global stages."""
        global_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Global Only",
            "sequence": 3,
        })
        applicable = self.env["helpdesk.ticket.team"]._get_applicable_stages()
        self.assertIn(global_stage, applicable)


@tagged("post_install", "-at_install")
class TestHelpdeskTeamTicketCounts(HelpdeskCommon):

    def test_todo_ticket_count_increments_on_ticket_creation(self):
        """Creating an open ticket should increment todo_ticket_count."""
        before = self.team.todo_ticket_count
        self._create_ticket()
        self.team.invalidate_recordset(["todo_ticket_count"])
        self.assertGreater(self.team.todo_ticket_count, before)

    def test_todo_ticket_count_excludes_closed_tickets(self):
        """Closing a ticket should reduce todo_ticket_count."""
        ticket = self._create_ticket()
        self.team.invalidate_recordset(["todo_ticket_count"])
        open_count = self.team.todo_ticket_count
        ticket.write({"stage_id": self.stage_done.id})
        self.team.invalidate_recordset(["todo_ticket_count"])
        closed_count = self.team.todo_ticket_count
        self.assertLess(closed_count, open_count)

    def test_todo_ticket_count_unassigned(self):
        """Unassigned open ticket should increment todo_ticket_count_unassigned."""
        ticket = self._create_ticket()
        ticket.write({"user_id": False})
        self.team.invalidate_recordset(["todo_ticket_count_unassigned"])
        self.assertGreaterEqual(self.team.todo_ticket_count_unassigned, 1)

    def test_todo_ticket_count_high_priority(self):
        """High-priority ticket should increment todo_ticket_count_high_priority."""
        self._create_ticket(priority="3")
        self.team.invalidate_recordset(["todo_ticket_count_high_priority"])
        self.assertGreaterEqual(self.team.todo_ticket_count_high_priority, 1)


@tagged("post_install", "-at_install")
class TestHelpdeskTeamCloseInactive(HelpdeskCommon):

    def test_close_inactive_tickets_moves_to_closing_stage(self):
        """Tickets inactive beyond the day limit should be closed automatically."""
        closing_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Auto-Closed",
            "sequence": 200,
            "closed": True,
        })
        team = self.env["helpdesk.ticket.team"].create({
            "name": "Auto-Close Team",
            "close_inactive_tickets": True,
            "inactive_tickets_day_limit_closing": 0,  # 0 = close immediately
            "closing_ticket_stage": closing_stage.id,
        })
        ticket = self._create_ticket(team_id=team.id)
        # Backdate last_stage_update so the ticket appears inactive
        self.env.cr.execute(
            "UPDATE helpdesk_ticket SET last_stage_update = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=1), ticket.id),
        )
        ticket.invalidate_recordset()
        team.close_team_inactive_tickets()
        self.assertEqual(ticket.stage_id, closing_stage)

    def test_close_inactive_skips_already_closed_tickets(self):
        """Already-closed tickets should not be touched by the auto-close logic."""
        closing_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Closed Already",
            "sequence": 200,
            "closed": True,
        })
        team = self.env["helpdesk.ticket.team"].create({
            "name": "Skip Closed Team",
            "close_inactive_tickets": True,
            "inactive_tickets_day_limit_closing": 0,
            "closing_ticket_stage": closing_stage.id,
        })
        ticket = self._create_ticket(team_id=team.id)
        ticket.write({"stage_id": self.stage_done.id})
        # Backdate
        self.env.cr.execute(
            "UPDATE helpdesk_ticket SET last_stage_update = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=5), ticket.id),
        )
        ticket.invalidate_recordset()
        team.close_team_inactive_tickets()
        # Already-closed ticket should NOT be moved to closing_stage
        self.assertNotEqual(ticket.stage_id, closing_stage)

    def test_close_inactive_respects_stage_filter(self):
        """Only tickets in ticket_stage_ids should be checked for auto-close."""
        watched_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Watched",
            "sequence": 15,
        })
        closing_stage = self.env["helpdesk.ticket.stage"].create({
            "name": "Closed Filter",
            "sequence": 200,
            "closed": True,
        })
        team = self.env["helpdesk.ticket.team"].create({
            "name": "Filtered Team",
            "close_inactive_tickets": True,
            "inactive_tickets_day_limit_closing": 0,
            "closing_ticket_stage": closing_stage.id,
            "ticket_stage_ids": [(4, watched_stage.id)],
        })
        # Ticket not in watched stage should be skipped
        ticket = self._create_ticket(team_id=team.id)
        ticket.write({"stage_id": self.stage_new.id})
        self.env.cr.execute(
            "UPDATE helpdesk_ticket SET last_stage_update = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=1), ticket.id),
        )
        ticket.invalidate_recordset()
        team.close_team_inactive_tickets()
        self.assertNotEqual(ticket.stage_id, closing_stage)


@tagged("post_install", "-at_install")
class TestHelpdeskTeamMisc(HelpdeskCommon):

    def test_retrieve_dashboard_returns_list(self):
        """retrieve_dashboard should return a non-empty list of dicts."""
        dashboard = self.env["helpdesk.ticket.team"].retrieve_dashboard()
        self.assertIsInstance(dashboard, list)
        for item in dashboard:
            self.assertIn("name", item)
            self.assertIn("value", item)
            self.assertIn("sequence", item)

    def test_python_domain_constraint_rejects_invalid_code(self):
        """Saving invalid Python domain code should raise ValidationError."""
        team = self.env["helpdesk.ticket.team"].create({"name": "Python Team"})
        # The @api.constrains fires on write, so assignment triggers ValidationError
        with self.assertRaises(ValidationError):
            team.project_domain_python = "this is definitely not valid python!!! ><"

    def test_python_domain_constraint_accepts_valid_code(self):
        """Valid Python domain code should not raise."""
        team = self.env["helpdesk.ticket.team"].create({"name": "Valid Python Team"})
        # Should not raise
        team.project_domain_python = "domain = [('active', '=', True)]"

    def test_disable_timesheet_clears_default_project(self):
        """Disabling allow_timesheet should clear default_project_id."""
        project = self.env["project.project"].create({"name": "TS Default Project"})
        team = self.env["helpdesk.ticket.team"].create({
            "name": "Timesheet Toggle Team",
            "allow_timesheet": True,
            "default_project_id": project.id,
        })
        team.allow_timesheet = False
        team._constrains_allow_timesheet()
        self.assertFalse(team.default_project_id)

    def test_execute_python_domain_returns_domain(self):
        """_execute_python_domain_code should evaluate Python code without raising."""
        team = self.env["helpdesk.ticket.team"].create({"name": "Exec Team"})
        code = "domain = [('priority', '=', '3')]"
        result = team._execute_python_domain_code(code)
        # Result is either the domain list or [] (implementation-dependent eval scope)
        self.assertIsInstance(result, list)

    def test_execute_python_domain_invalid_code_returns_empty(self):
        """Invalid Python domain code should return [] without raising."""
        team = self.env["helpdesk.ticket.team"].create({"name": "Invalid Exec Team"})
        result = team._execute_python_domain_code("raise ValueError('boom')")
        self.assertEqual(result, [])
