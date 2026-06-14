from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    ticket_ids = fields.Many2many("helpdesk.ticket", string="Tickets")
    ticket_count = fields.Integer(string="Ticket Count", compute="_compute_ticket_count")
    todo_ticket_count = fields.Integer(string="Open Ticket Count", compute="_compute_ticket_count")

    @api.depends("ticket_ids")
    def _compute_ticket_count(self):
        counts = {
            task.id: count
            for task, count in self.env["helpdesk.ticket"]._read_group(
                domain=[("sale_order_ids", "in", self.ids)],
                groupby=["sale_order_ids"],
                aggregates=["__count"],
            )
        }
        domain_todo = [("sale_order_ids", "in", self.ids), ("closed", "=", False)]
        counts_todo = {
            task.id: count
            for task, count in self.env["helpdesk.ticket"]._read_group(
                domain=domain_todo,
                groupby=["sale_order_ids"],
                aggregates=["__count"],
            )
        }
        for order in self:
            order.ticket_count = counts.get(order.id, 0)
            order.todo_ticket_count = counts_todo.get(order.id, 0)

    def action_view_tickets(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id("helpdesk_custom.helpdesk_ticket_action_main")
        action["domain"] = [("sale_order_ids", "in", [self.id])]
        action["context"] = {"default_sale_order_ids": [self.id]}
        return action
