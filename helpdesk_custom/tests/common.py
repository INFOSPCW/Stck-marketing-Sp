"""
Common base class and fixtures shared across all helpdesk_custom tests.
"""
from odoo.tests.common import TransactionCase


class HelpdeskCommon(TransactionCase):
    """Base class providing shared fixtures for helpdesk_custom tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # ── Stages (no team restriction → applicable to all teams) ────────────
        cls.stage_new = cls.env["helpdesk.ticket.stage"].create({
            "name": "New",
            "sequence": 1,
        })
        cls.stage_progress = cls.env["helpdesk.ticket.stage"].create({
            "name": "In Progress",
            "sequence": 10,
        })
        cls.stage_done = cls.env["helpdesk.ticket.stage"].create({
            "name": "Done",
            "sequence": 100,
            "closed": True,
        })

        # ── Team ──────────────────────────────────────────────────────────────
        cls.team = cls.env["helpdesk.ticket.team"].create({
            "name": "Test Support Team",
        })

        # ── Users ─────────────────────────────────────────────────────────────
        cls.user_helpdesk = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Helpdesk User",
            "login": "hd_user_common_test",
            "email": "hd_user@test.com",
            "group_ids": [
                (4, cls.env.ref("helpdesk_custom.group_helpdesk_user").id),
            ],
        })

        # ── Partner ───────────────────────────────────────────────────────────
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Partner",
            "email": "partner@test.com",
            "phone": "+1234567890",
        })

        # ── Category ──────────────────────────────────────────────────────────
        cls.category = cls.env["helpdesk.ticket.category"].create({
            "name": "Billing",
        })

        # ── Tag ───────────────────────────────────────────────────────────────
        cls.tag = cls.env["helpdesk.ticket.tag"].create({
            "name": "Urgent",
        })

        # ── Channel ───────────────────────────────────────────────────────────
        cls.channel = cls.env["helpdesk.ticket.channel"].create({
            "name": "Email",
        })

    def _create_ticket(self, **kwargs):
        """Create a ticket with sensible defaults; override via kwargs."""
        vals = {
            "name": "Test Ticket",
            "description": "<p>Test description</p>",
            "team_id": self.team.id,
            "partner_id": self.partner.id,
        }
        vals.update(kwargs)
        return self.env["helpdesk.ticket"].create(vals)
