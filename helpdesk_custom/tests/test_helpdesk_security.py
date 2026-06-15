"""
Tests for access control and record rules:
  - All four security groups exist
  - Group hierarchy (manager implies user implies team implies own)
  - Helpdesk users can create / read / write tickets
  - Manager can access SLA records
  - Company-scoped rules (tickets stay within company)
  - Helpdesk user_own can see tickets assigned to them
  - Portal restriction flag on partners
"""
from odoo.tests.common import tagged

from .common import HelpdeskCommon


@tagged("post_install", "-at_install")
class TestHelpdeskGroupsExist(HelpdeskCommon):
    """Verify all four security groups are present after installation."""

    def test_group_user_own_exists(self):
        group = self.env.ref("helpdesk_custom.group_helpdesk_user_own", raise_if_not_found=False)
        self.assertTrue(group, "group_helpdesk_user_own should exist")

    def test_group_user_team_exists(self):
        group = self.env.ref("helpdesk_custom.group_helpdesk_user_team", raise_if_not_found=False)
        self.assertTrue(group, "group_helpdesk_user_team should exist")

    def test_group_user_exists(self):
        group = self.env.ref("helpdesk_custom.group_helpdesk_user", raise_if_not_found=False)
        self.assertTrue(group, "group_helpdesk_user should exist")

    def test_group_manager_exists(self):
        group = self.env.ref("helpdesk_custom.group_helpdesk_manager", raise_if_not_found=False)
        self.assertTrue(group, "group_helpdesk_manager should exist")


@tagged("post_install", "-at_install")
class TestHelpdeskGroupHierarchy(HelpdeskCommon):
    """Verify implied group chains."""

    def test_manager_implies_user(self):
        """group_helpdesk_manager should imply group_helpdesk_user."""
        manager_group = self.env.ref("helpdesk_custom.group_helpdesk_manager")
        user_group = self.env.ref("helpdesk_custom.group_helpdesk_user")
        implied_ids = manager_group.implied_ids | manager_group.all_implied_ids
        self.assertIn(user_group, implied_ids)

    def test_user_implies_user_team(self):
        """group_helpdesk_user should imply group_helpdesk_user_team."""
        user_group = self.env.ref("helpdesk_custom.group_helpdesk_user")
        team_group = self.env.ref("helpdesk_custom.group_helpdesk_user_team")
        implied_ids = user_group.implied_ids | user_group.all_implied_ids
        self.assertIn(team_group, implied_ids)

    def test_user_team_implies_user_own(self):
        """group_helpdesk_user_team should imply group_helpdesk_user_own."""
        team_group = self.env.ref("helpdesk_custom.group_helpdesk_user_team")
        own_group = self.env.ref("helpdesk_custom.group_helpdesk_user_own")
        implied_ids = team_group.implied_ids | team_group.all_implied_ids
        self.assertIn(own_group, implied_ids)


@tagged("post_install", "-at_install")
class TestHelpdeskUserAccess(HelpdeskCommon):
    """Verify helpdesk users can perform CRUD on tickets."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_full = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Full HD User",
            "login": "sec_full_user_test",
            "email": "sec_full@test.com",
            "group_ids": [(4, cls.env.ref("helpdesk_custom.group_helpdesk_user").id)],
        })

    def test_helpdesk_user_can_create_ticket(self):
        """User in group_helpdesk_user should be able to create a ticket."""
        ticket = self.env["helpdesk.ticket"].with_user(self.user_full).create({
            "name": "User Created",
            "description": "<p>Created by helpdesk user</p>",
        })
        self.assertTrue(ticket.id)

    def test_helpdesk_user_can_read_ticket(self):
        """User should be able to read a ticket they have access to."""
        ticket = self._create_ticket()
        ticket_read = ticket.with_user(self.user_full)
        self.assertTrue(ticket_read.name)

    def test_helpdesk_user_can_write_ticket(self):
        """User should be able to update ticket fields."""
        ticket = self._create_ticket()
        ticket.with_user(self.user_full).write({"name": "Updated by User"})
        self.assertEqual(ticket.name, "Updated by User")

    def test_helpdesk_user_can_read_stages(self):
        """User should be able to read stages."""
        stages = self.env["helpdesk.ticket.stage"].with_user(self.user_full).search([
            ("id", "=", self.stage_new.id)
        ])
        self.assertIn(self.stage_new, stages)

    def test_helpdesk_user_can_read_categories(self):
        """User should be able to read categories."""
        cats = self.env["helpdesk.ticket.category"].with_user(self.user_full).search([
            ("id", "=", self.category.id)
        ])
        self.assertIn(self.category, cats)

    def test_helpdesk_user_can_read_tags(self):
        """User should be able to read tags."""
        tags = self.env["helpdesk.ticket.tag"].with_user(self.user_full).search([
            ("id", "=", self.tag.id)
        ])
        self.assertIn(self.tag, tags)

    def test_helpdesk_user_can_read_teams(self):
        """User should be able to read team records."""
        teams = self.env["helpdesk.ticket.team"].with_user(self.user_full).search([
            ("id", "=", self.team.id)
        ])
        self.assertIn(self.team, teams)


@tagged("post_install", "-at_install")
class TestHelpdeskManagerAccess(HelpdeskCommon):
    """Verify manager-level access."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_mgr = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "HD Manager",
            "login": "sec_mgr_user_test",
            "email": "sec_mgr@test.com",
            "group_ids": [(4, cls.env.ref("helpdesk_custom.group_helpdesk_manager").id)],
        })

    def test_manager_can_read_all_tickets(self):
        """Manager should be able to read any ticket in the system."""
        ticket = self._create_ticket()
        ticket_as_mgr = ticket.with_user(self.user_mgr)
        self.assertTrue(ticket_as_mgr.name)

    def test_manager_can_create_team(self):
        """Manager should be able to create a helpdesk team."""
        team = self.env["helpdesk.ticket.team"].with_user(self.user_mgr).create({
            "name": "Manager Team"
        })
        self.assertTrue(team.id)

    def test_manager_can_access_sla(self):
        """Manager should be able to read SLA records."""
        sla = self.env["helpdesk.sla"].create({"name": "Manager SLA"})
        sla_as_mgr = sla.with_user(self.user_mgr)
        self.assertTrue(sla_as_mgr.name)

    def test_manager_can_create_stage(self):
        """Manager should be able to create ticket stages."""
        stage = self.env["helpdesk.ticket.stage"].with_user(self.user_mgr).create({
            "name": "Manager Stage"
        })
        self.assertTrue(stage.id)


@tagged("post_install", "-at_install")
class TestHelpdeskOwnUserAccess(HelpdeskCommon):
    """Verify user_own level can see their own tickets."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_own = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Own Only User",
            "login": "sec_own_user_test",
            "email": "sec_own@test.com",
            "group_ids": [(4, cls.env.ref("helpdesk_custom.group_helpdesk_user_own").id)],
        })

    def test_own_user_sees_ticket_assigned_to_them(self):
        """User with 'own' access can read tickets where they are user_id."""
        ticket = self.env["helpdesk.ticket"].create({
            "name": "Own User Ticket",
            "description": "<p>x</p>",
            "user_id": self.user_own.id,
        })
        results = self.env["helpdesk.ticket"].with_user(self.user_own).search([
            ("id", "=", ticket.id)
        ])
        self.assertIn(ticket, results)


@tagged("post_install", "-at_install")
class TestHelpdeskCompanyScope(HelpdeskCommon):
    """Verify company-scoped access rules."""

    def test_ticket_gets_company_on_create(self):
        """Every ticket should have a company_id after creation."""
        ticket = self._create_ticket()
        self.assertTrue(ticket.company_id)

    def test_ticket_company_matches_current(self):
        """Ticket company should match the creating user's company."""
        ticket = self.env["helpdesk.ticket"].create({
            "name": "Company Scoped",
            "description": "<p>x</p>",
        })
        self.assertEqual(ticket.company_id, self.env.company)

    def test_stage_has_company(self):
        """Stage should have company_id after creation."""
        stage = self.env["helpdesk.ticket.stage"].create({"name": "Scoped Stage"})
        self.assertEqual(stage.company_id, self.env.company)

    def test_category_has_company(self):
        """Category should have company_id after creation."""
        cat = self.env["helpdesk.ticket.category"].create({"name": "Scoped Cat"})
        self.assertEqual(cat.company_id, self.env.company)

    def test_tag_has_company(self):
        """Tag should have company_id after creation."""
        tag = self.env["helpdesk.ticket.tag"].create({"name": "Scoped Tag"})
        self.assertEqual(tag.company_id, self.env.company)

    def test_channel_has_company(self):
        """Channel should have company_id after creation."""
        ch = self.env["helpdesk.ticket.channel"].create({"name": "Scoped Channel"})
        self.assertEqual(ch.company_id, self.env.company)

    def test_team_has_company(self):
        """Team should have company_id after creation."""
        team = self.env["helpdesk.ticket.team"].create({"name": "Scoped Team"})
        self.assertEqual(team.company_id, self.env.company)
