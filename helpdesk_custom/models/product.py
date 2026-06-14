from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    ticket_active = fields.Boolean(string="Available for Helpdesk", default=True)
    helpdesk_ticket_ids = fields.One2many(
        related="product_variant_ids.helpdesk_ticket_ids",
        string="Helpdesk Tickets",
    )
    helpdesk_ticket_count = fields.Integer(
        compute="_compute_helpdesk_ticket_count",
        string="Ticket Count",
    )

    @api.depends("product_variant_ids.helpdesk_ticket_ids")
    def _compute_helpdesk_ticket_count(self):
        for template in self:
            template.helpdesk_ticket_count = len(template.helpdesk_ticket_ids)

    def action_view_helpdesk_tickets(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("helpdesk_custom.helpdesk_ticket_action_main")
        action["domain"] = [("product_id.product_tmpl_id", "=", self.id)]
        action["context"] = {}
        return action


class ProductProduct(models.Model):
    _inherit = "product.product"

    helpdesk_ticket_ids = fields.One2many(
        comodel_name="helpdesk.ticket",
        inverse_name="product_id",
        string="Helpdesk Tickets",
    )
    helpdesk_ticket_count = fields.Integer(compute="_compute_helpdesk_ticket_count")

    @api.depends("helpdesk_ticket_ids")
    def _compute_helpdesk_ticket_count(self):
        for product in self:
            product.helpdesk_ticket_count = len(product.helpdesk_ticket_ids)

    def action_view_helpdesk_tickets(self):
        return self.product_tmpl_id.action_view_helpdesk_tickets()
