import base64
import logging
from math import ceil

import werkzeug

import odoo.http as http
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.http import request
from odoo.tools import plaintext2html

_logger = logging.getLogger(__name__)


class HelpdeskPortalController(CustomerPortal):

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "ticket_count" in counters:
            partner = request.env.user.partner_id
            values["ticket_count"] = (
                request.env["helpdesk.ticket"]
                .sudo()
                .search_count([("partner_id", "child_of", [partner.commercial_partner_id.id])])
            )
        return values

    @http.route("/my/tickets", type="http", auth="user", website=True)
    def my_tickets(self, page=1, **kw):
        partner = request.env.user.partner_id
        domain = [("partner_id", "child_of", [partner.commercial_partner_id.id])]
        ticket_count = request.env["helpdesk.ticket"].sudo().search_count(domain)
        pager = portal_pager(
            url="/my/tickets",
            total=ticket_count,
            page=page,
            step=20,
        )
        tickets = (
            request.env["helpdesk.ticket"]
            .sudo()
            .search(domain, limit=20, offset=pager["offset"], order="id desc")
        )
        return request.render(
            "helpdesk_custom.portal_my_tickets",
            {"tickets": tickets, "page_name": "ticket", "pager": pager},
        )

    @http.route("/my/ticket/<int:ticket_id>", type="http", auth="user", website=True)
    def my_ticket_detail(self, ticket_id, **kw):
        ticket = request.env["helpdesk.ticket"].sudo().browse(ticket_id)
        if not ticket.exists():
            return request.not_found()
        close_stages = request.env["helpdesk.ticket.stage"].sudo().search(
            [("close_from_portal", "=", True), ("closed", "=", True)]
        )
        return request.render(
            "helpdesk_custom.portal_helpdesk_ticket_page",
            {"ticket": ticket, "close_stages": close_stages, "page_name": "ticket"},
        )


class HelpdeskTicketController(http.Controller):
    @http.route("/ticket/close", type="http", auth="user")
    def support_ticket_close(self, **kw):
        values = {}
        for field_name, field_value in kw.items():
            if field_name.endswith("_id"):
                values[field_name] = int(field_value)
            else:
                values[field_name] = field_value
        ticket = http.request.env["helpdesk.ticket"].sudo().search([("id", "=", values["ticket_id"])])
        stage = http.request.env["helpdesk.ticket.stage"].browse(values.get("stage_id"))
        if stage.close_from_portal:
            ticket.stage_id = values.get("stage_id")
        return werkzeug.utils.redirect("/my/ticket/" + str(ticket.id))

    def _get_teams(self):
        return (
            http.request.env["helpdesk.ticket.team"]
            .with_company(request.env.company.id)
            .search([("active", "=", True), ("show_in_portal", "=", True)])
            if http.request.env.user.company_id.helpdesk_mgmt_portal_select_team
            else False
        )

    def _get_categories(self, **kw):
        company = request.env.company
        category_model = http.request.env["helpdesk.ticket.category"]
        domain = [("active", "=", True), ("show_in_portal", "=", True)]
        return (
            category_model.with_company(company.id).search(domain)
            if http.request.env.user.company_id.helpdesk_mgmt_portal_select_category
            else category_model
        )

    def _get_types(self, team=None):
        company = request.env.company
        type_model = http.request.env["helpdesk.ticket.type"]
        if not company.helpdesk_mgmt_portal_type:
            return type_model
        domain = [("active", "=", True), ("show_in_portal", "=", True)]
        if team:
            domain += ["|", ("team_ids", "=", False), ("team_ids", "=", team.id)]
        return type_model.search(domain)

    @http.route("/new/ticket", type="http", auth="user", website=True)
    def create_new_ticket(self, **kw):
        values = self._get_create_new_ticket_values(**kw)
        return http.request.render("helpdesk_custom.portal_create_ticket", values)

    def _get_create_new_ticket_values(self, **kw):
        session_info = http.request.env["ir.http"].session_info()
        company = request.env.company
        email = http.request.env.user.email
        name = http.request.env.user.name
        teams = self._get_teams()
        return {
            "categories": self._get_categories(**kw),
            "teams": teams,
            "types": self._get_types(),
            "priorities": [("0", "Low"), ("1", "Medium"), ("2", "High"), ("3", "Very High")],
            "email": email,
            "name": name,
            "ticket_team_id_required": company.helpdesk_mgmt_portal_team_id_required,
            "ticket_category_id_required": company.helpdesk_mgmt_portal_category_id_required,
            "ticket_type_id_required": company.helpdesk_mgmt_portal_type_id_required,
            "max_upload_size": session_info["max_file_upload_size"],
        }

    def _prepare_submit_ticket_vals(self, **kw):
        category = http.request.env["helpdesk.ticket.category"].browse(int(kw.get("category") or 0))
        company = category.company_id or http.request.env.company
        vals = {
            "company_id": company.id,
            "category_id": category.id,
            "description": plaintext2html(kw.get("description")),
            "name": kw.get("subject"),
            "attachment_ids": False,
            "channel_id": request.env.ref("helpdesk_custom.helpdesk_ticket_channel_web", False).id,
            "partner_id": request.env.user.partner_id.id,
            "partner_name": request.env.user.partner_id.name,
            "partner_email": request.env.user.partner_id.email,
            "user_id": False,
            "priority": kw.get("priority", "1"),
        }
        team = http.request.env["helpdesk.ticket.team"]
        if company.helpdesk_mgmt_portal_select_team and kw.get("team"):
            team = (
                http.request.env["helpdesk.ticket.team"].sudo()
                .search([("id", "=", int(kw.get("team"))), ("show_in_portal", "=", True)])
            )
            vals["team_id"] = team.id
        if company.helpdesk_mgmt_portal_type and kw.get("type"):
            vals["type_id"] = int(kw.get("type"))
        vals["stage_id"] = team._get_applicable_stages()[:1].id
        return vals

    @http.route("/submitted/ticket", type="http", auth="user", website=True, csrf=True)
    def submit_ticket(self, **kw):
        vals = self._prepare_submit_ticket_vals(**kw)
        new_ticket = request.env["helpdesk.ticket"].sudo().create(vals)
        new_ticket.message_subscribe(partner_ids=request.env.user.partner_id.ids)
        if kw.get("attachment"):
            for c_file in request.httprequest.files.getlist("attachment"):
                data = c_file.read()
                if c_file.filename:
                    request.env["ir.attachment"].sudo().create({
                        "name": c_file.filename,
                        "datas": base64.b64encode(data),
                        "res_model": "helpdesk.ticket",
                        "res_id": new_ticket.id,
                    })
        return werkzeug.utils.redirect(f"/my/ticket/{new_ticket.id}")
