"""
Tests for lookup / configuration models:
  - helpdesk.ticket.category  (hierarchy, template_description, archiving)
  - helpdesk.ticket.motive    (active field, team scoping, motive cleared on team change)
  - helpdesk.ticket.type      (creation, team association, onchange clears team)
  - helpdesk.ticket.tag       (creation, linkage to tickets)
  - helpdesk.ticket.channel   (creation, linkage to tickets)
"""
from odoo.tests.common import tagged

from .common import HelpdeskCommon


# =============================================================================
# Category
# =============================================================================

@tagged("post_install", "-at_install")
class TestHelpdeskCategory(HelpdeskCommon):

    # ── complete_name ─────────────────────────────────────────────────────────

    def test_complete_name_flat(self):
        """Category without parent: complete_name == name."""
        cat = self.env["helpdesk.ticket.category"].create({"name": "Support"})
        self.assertEqual(cat.complete_name, "Support")

    def test_complete_name_one_level(self):
        """Child category: 'Parent / Child'."""
        parent = self.env["helpdesk.ticket.category"].create({"name": "Hardware"})
        child = self.env["helpdesk.ticket.category"].create({
            "name": "Laptop", "parent_id": parent.id
        })
        self.assertEqual(child.complete_name, "Hardware / Laptop")

    def test_complete_name_three_levels(self):
        """Three-level hierarchy concatenates all names."""
        l1 = self.env["helpdesk.ticket.category"].create({"name": "L1"})
        l2 = self.env["helpdesk.ticket.category"].create({"name": "L2", "parent_id": l1.id})
        l3 = self.env["helpdesk.ticket.category"].create({"name": "L3", "parent_id": l2.id})
        self.assertEqual(l3.complete_name, "L1 / L2 / L3")

    def test_complete_name_updates_on_parent_rename(self):
        """Renaming parent propagates to child's complete_name."""
        parent = self.env["helpdesk.ticket.category"].create({"name": "OldCat"})
        child = self.env["helpdesk.ticket.category"].create({
            "name": "Child", "parent_id": parent.id
        })
        parent.name = "NewCat"
        child.invalidate_recordset(["complete_name"])
        self.assertIn("NewCat", child.complete_name)

    # ── child_ids ─────────────────────────────────────────────────────────────

    def test_child_ids_populated(self):
        """Parent should list its children in child_ids."""
        parent = self.env["helpdesk.ticket.category"].create({"name": "Parent Cat"})
        c1 = self.env["helpdesk.ticket.category"].create({"name": "C1", "parent_id": parent.id})
        c2 = self.env["helpdesk.ticket.category"].create({"name": "C2", "parent_id": parent.id})
        self.assertIn(c1, parent.child_ids)
        self.assertIn(c2, parent.child_ids)

    def test_cascade_delete_removes_children(self):
        """Deleting parent should cascade-delete its children."""
        parent = self.env["helpdesk.ticket.category"].create({"name": "Del Parent"})
        child = self.env["helpdesk.ticket.category"].create({
            "name": "Del Child", "parent_id": parent.id
        })
        parent.unlink()
        self.assertFalse(child.exists())

    # ── template_description ──────────────────────────────────────────────────

    def test_template_description_fills_empty_ticket_description(self):
        """Category template should auto-fill blank ticket description."""
        cat = self.env["helpdesk.ticket.category"].create({
            "name": "Billing Cat",
            "template_description": "<p>Please provide your invoice number.</p>",
        })
        ticket = self.env["helpdesk.ticket"].new({
            "name": "Billing Issue",
            "category_id": cat.id,
        })
        ticket._compute_description()
        self.assertEqual(ticket.description, "<p>Please provide your invoice number.</p>")

    def test_template_description_does_not_overwrite_existing(self):
        """Template should not overwrite a description the user already typed."""
        cat = self.env["helpdesk.ticket.category"].create({
            "name": "Overwrite Cat",
            "template_description": "<p>Template</p>",
        })
        ticket = self.env["helpdesk.ticket"].new({
            "name": "Already Has Desc",
            "description": "<p>User text</p>",
            "category_id": cat.id,
        })
        ticket._compute_description()
        self.assertEqual(ticket.description, "<p>User text</p>")

    def test_category_without_template_leaves_description_unchanged(self):
        """Category without template should not modify description."""
        cat = self.env["helpdesk.ticket.category"].create({"name": "No Tpl"})
        ticket = self.env["helpdesk.ticket"].new({
            "name": "No Tpl Ticket",
            "category_id": cat.id,
        })
        ticket._compute_description()
        self.assertFalse(ticket.description)

    # ── archiving ─────────────────────────────────────────────────────────────

    def test_category_active_default(self):
        """Category active defaults to True."""
        cat = self.env["helpdesk.ticket.category"].create({"name": "Active Cat"})
        self.assertTrue(cat.active)

    def test_category_archiving(self):
        """Setting active=False should hide category from default search."""
        cat = self.env["helpdesk.ticket.category"].create({"name": "Archived Cat"})
        cat.active = False
        found = self.env["helpdesk.ticket.category"].search([("name", "=", "Archived Cat")])
        self.assertFalse(found)

    # ── show_in_portal ────────────────────────────────────────────────────────

    def test_category_show_in_portal_default(self):
        """Category should show in portal by default."""
        cat = self.env["helpdesk.ticket.category"].create({"name": "Portal Cat"})
        self.assertTrue(cat.show_in_portal)


# =============================================================================
# Motive
# =============================================================================

@tagged("post_install", "-at_install")
class TestHelpdeskMotive(HelpdeskCommon):

    def test_motive_active_default_true(self):
        """Motive should be active by default."""
        motive = self.env["helpdesk.ticket.motive"].create({
            "name": "Price Issue",
            "team_id": self.team.id,
        })
        self.assertTrue(motive.active)

    def test_motive_archiving(self):
        """Archived motive should not appear in default search."""
        motive = self.env["helpdesk.ticket.motive"].create({
            "name": "Old Motive",
            "team_id": self.team.id,
        })
        motive.active = False
        found = self.env["helpdesk.ticket.motive"].search([("name", "=", "Old Motive")])
        self.assertFalse(found)

    def test_motive_linked_to_team(self):
        """Motive should appear in team.motive_ids."""
        motive = self.env["helpdesk.ticket.motive"].create({
            "name": "Delivery Issue",
            "team_id": self.team.id,
        })
        self.assertIn(motive, self.team.motive_ids)

    def test_motive_cleared_when_team_changes(self):
        """Motive should be cleared when ticket's team changes to a different team."""
        team2 = self.env["helpdesk.ticket.team"].create({"name": "Motive Team 2"})
        motive = self.env["helpdesk.ticket.motive"].create({
            "name": "Team1 Motive",
            "team_id": self.team.id,
        })
        ticket = self._create_ticket(motive_id=motive.id)
        ticket.team_id = team2.id
        ticket._compute_motive_id()
        self.assertFalse(ticket.motive_id, "Motive should be cleared when team changes")

    def test_motive_kept_when_team_stays_same(self):
        """Motive should NOT be cleared when team stays the same."""
        motive = self.env["helpdesk.ticket.motive"].create({
            "name": "Same Team Motive",
            "team_id": self.team.id,
        })
        ticket = self._create_ticket(motive_id=motive.id)
        ticket.team_id = self.team.id  # no change
        ticket._compute_motive_id()
        self.assertEqual(ticket.motive_id, motive)


# =============================================================================
# Type
# =============================================================================

@tagged("post_install", "-at_install")
class TestHelpdeskType(HelpdeskCommon):

    def test_type_creation_defaults(self):
        """Type should be active=True by default."""
        t = self.env["helpdesk.ticket.type"].create({"name": "Bug"})
        self.assertTrue(t.active)

    def test_type_team_association(self):
        """Type can be added to a team's type_ids."""
        t = self.env["helpdesk.ticket.type"].create({"name": "Feature Request"})
        self.team.type_ids = [(4, t.id)]
        self.assertIn(t, self.team.type_ids)

    def test_type_not_in_team_clears_team_on_ticket(self):
        """Selecting a type not in team's type_ids should clear team on ticket."""
        team_with_type = self.env["helpdesk.ticket.team"].create({"name": "Type Team"})
        t_allowed = self.env["helpdesk.ticket.type"].create({"name": "Allowed"})
        t_disallowed = self.env["helpdesk.ticket.type"].create({"name": "Disallowed"})
        team_with_type.type_ids = [(4, t_allowed.id)]

        ticket = self.env["helpdesk.ticket"].new({
            "name": "Type Change Test",
            "description": "<p>x</p>",
            "team_id": team_with_type.id,
            "type_id": t_allowed.id,
        })
        ticket.type_id = t_disallowed
        ticket._onchange_type_id()
        self.assertFalse(ticket.team_id)

    def test_type_show_in_portal(self):
        """Type can be marked for portal visibility."""
        t = self.env["helpdesk.ticket.type"].create({
            "name": "Portal Type",
            "show_in_portal": True,
        })
        self.assertTrue(t.show_in_portal)

    def test_type_archiving(self):
        """Type should be archivable."""
        t = self.env["helpdesk.ticket.type"].create({"name": "Archivable Type"})
        t.active = False
        found = self.env["helpdesk.ticket.type"].search([("name", "=", "Archivable Type")])
        self.assertFalse(found)


# =============================================================================
# Tag
# =============================================================================

@tagged("post_install", "-at_install")
class TestHelpdeskTag(HelpdeskCommon):

    def test_tag_creation_defaults(self):
        """Tag should be created with active=True."""
        tag = self.env["helpdesk.ticket.tag"].create({"name": "VIP"})
        self.assertTrue(tag.active)

    def test_tag_linked_to_ticket(self):
        """Tag should be linkable to a ticket via tag_ids."""
        ticket = self._create_ticket(tag_ids=[(4, self.tag.id)])
        self.assertIn(self.tag, ticket.tag_ids)

    def test_multiple_tags_on_ticket(self):
        """Multiple tags should all appear in ticket.tag_ids."""
        tag2 = self.env["helpdesk.ticket.tag"].create({"name": "Low"})
        ticket = self._create_ticket(tag_ids=[(4, self.tag.id), (4, tag2.id)])
        self.assertIn(self.tag, ticket.tag_ids)
        self.assertIn(tag2, ticket.tag_ids)

    def test_tag_archiving(self):
        """Archived tag should not appear in default search."""
        tag = self.env["helpdesk.ticket.tag"].create({"name": "Old Tag"})
        tag.active = False
        found = self.env["helpdesk.ticket.tag"].search([("name", "=", "Old Tag")])
        self.assertFalse(found)

    def test_tag_color(self):
        """Tag color should be storable as integer."""
        tag = self.env["helpdesk.ticket.tag"].create({"name": "Colored", "color": 5})
        self.assertEqual(tag.color, 5)


# =============================================================================
# Channel
# =============================================================================

@tagged("post_install", "-at_install")
class TestHelpdeskChannel(HelpdeskCommon):

    def test_channel_creation_defaults(self):
        """Channel should be created with active=True."""
        ch = self.env["helpdesk.ticket.channel"].create({"name": "Phone"})
        self.assertTrue(ch.active)

    def test_channel_on_ticket(self):
        """Channel should be linkable to a ticket."""
        ticket = self._create_ticket(channel_id=self.channel.id)
        self.assertEqual(ticket.channel_id, self.channel)

    def test_channel_sequence(self):
        """Channel sequence should be storable."""
        ch = self.env["helpdesk.ticket.channel"].create({"name": "Chat", "sequence": 5})
        self.assertEqual(ch.sequence, 5)

    def test_channel_archiving(self):
        """Archived channel should not appear in default search."""
        ch = self.env["helpdesk.ticket.channel"].create({"name": "Old Channel"})
        ch.active = False
        found = self.env["helpdesk.ticket.channel"].search([("name", "=", "Old Channel")])
        self.assertFalse(found)
