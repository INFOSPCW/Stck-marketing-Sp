/** @odoo-module **/
/**
 * Enterprise-Style Home Dashboard — Odoo 19 Community
 *
 * Two-column layout:
 *  LEFT  — categorised app grid with live per-app stats
 *  RIGHT — "At a Glance" panel: KPIs, recent activity, daily plan
 */

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { computeAppsAndMenuItems } from "@web/webclient/menus/menu_helpers";

// ─── Pure helpers ─────────────────────────────────────────────────────────────

function parseWebIcon(str) {
    if (!str || typeof str !== "string") return null;
    const [iconClass = "", color = "#ffffff", backgroundColor = "#714B67"] = str.split(",");
    return { iconClass: iconClass.trim(), color: color.trim(), backgroundColor: backgroundColor.trim() };
}

function formatNum(n) {
    if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return Math.round(n / 1e3) + "K";
    return String(Math.round(n));
}

function timeAgo(dateStr) {
    const date = new Date(dateStr.includes("T") ? dateStr : dateStr + "Z");
    const mins = Math.floor((Date.now() - date) / 60000);
    if (mins < 2) return "just now";
    if (mins < 60) return `${mins}m ago`;
    if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
    return `${Math.floor(mins / 1440)}d ago`;
}

function stripHtml(html) {
    return (html || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

function todayStart() { return new Date().toISOString().slice(0, 10) + " 00:00:00"; }
function todayEnd()   { return new Date().toISOString().slice(0, 10) + " 23:59:59"; }

// ─── Category system ──────────────────────────────────────────────────────────

const CAT_ORDER = [
    "Sales", "Accounting", "Human Resources",
    "Supply Chain", "Marketing", "Website", "Productivity", "Other",
];

function getCategory(app) {
    const x = (app.xmlid || "").toLowerCase();
    const n = (app.label || app.name || "").toLowerCase();
    if (x.match(/\b(sale|crm|point_of_sale|contacts|loyalty)\b/)      || n.match(/\b(sales|crm|point of sale|contacts)\b/)) return "Sales";
    if (x.match(/\baccount/)                                           || n.match(/\b(invoic|accounting|payment)\b/))        return "Accounting";
    if (x.match(/\b(hr|fleet|lunch)\b/)                               || n.match(/\b(employee|recruitment|attendance|time off|expense|payroll|fleet|leave)\b/)) return "Human Resources";
    if (x.match(/\b(stock|mrp|purchase|maintenance|repair|inventory)\b/) || n.match(/\b(inventory|manufacturing|purchase|repair|maintenance|stock)\b/)) return "Supply Chain";
    if (x.match(/\b(mass_mailing|event|survey|marketing|social)\b/)   || n.match(/\b(marketing|email|sms|survey|event)\b/)) return "Marketing";
    if (x.match(/\b(website|im_livechat|slides|ecommerce|blog)\b/)    || n.match(/\b(website|ecommerce|live chat|elearning|blog)\b/)) return "Website";
    if (x.match(/\b(project|calendar|discuss|todo|note|timesheet)\b/) || n.match(/\b(project|discuss|calendar|to-do|todo|timesheet)\b/)) return "Productivity";
    return "Other";
}

// ─── Component ───────────────────────────────────────────────────────────────

export class HomeDashboard extends Component {
    static template = "odoo_home_dashboard.HomeDashboard";
    static props = {};

    setup() {
        this.menuService = useService("menu");
        this.orm = useService("orm");

        this.state = useState({
            query: "",
            category: "All",
            stats: {},       // menuId → stat string
            kpis: [],        // [{ label, value }]
            recent: [],      // [{ id, record, author, body, ago }]
            plan: [],        // [{ icon, label, detail }]
            iconErrors: {},  // menuId → true when PNG fails
        });

        const { apps } = computeAppsAndMenuItems(this.menuService.getMenuAsTree("root"));
        this._apps = apps.map((app) => ({
            ...app,
            name: app.label || app.name || "(unnamed)",
            _cat: getCategory(app),
            _icon: parseWebIcon(typeof app.webIcon === "string" ? app.webIcon : null),
            _iconUrl: `/web/image/ir.ui.menu/${app.id}/web_icon_data`,
        }));
        this._appsById = Object.fromEntries(this._apps.map((a) => [a.id, a]));

        // Load all data before first render (runs in parallel, errors swallowed per-section).
        onWillStart(() =>
            Promise.all([
                this._loadStats(),
                this._loadKpis(),
                this._loadRecent(),
                this._loadPlan(),
            ])
        );
    }

    // ── App stats ─────────────────────────────────────────────────────────────

    async _loadStats() {
        const settled = await Promise.allSettled(this._apps.map((a) => this._statFor(a)));
        settled.forEach((r, i) => {
            if (r.status === "fulfilled" && r.value) {
                this.state.stats[this._apps[i].id] = r.value;
            }
        });
    }

    async _statFor(app) {
        const x = (app.xmlid || "").toLowerCase();
        const n = app.name.toLowerCase();
        try {
            if (x.includes("contact") || n.includes("contact")) {
                const cnt = await this.orm.searchCount("res.partner", [["active", "=", true], ["type", "=", "contact"]]);
                return `${cnt} contacts`;
            }
            if (x.includes("crm") || n === "crm") {
                const cnt = await this.orm.searchCount("crm.lead", [["type", "=", "opportunity"], ["active", "=", true]]);
                return `${cnt} active deals`;
            }
            if (x.includes("discuss") || n.includes("discuss")) {
                const cnt = await this.orm.searchCount("mail.message", [["needaction", "=", true]]);
                return cnt ? `${cnt} unread` : "No new messages";
            }
            if ((x.includes("todo") || n.includes("to-do")) && !x.includes("project")) {
                const [total, overdue] = await Promise.all([
                    this.orm.searchCount("project.task", [["project_id", "=", false], ["state", "not in", ["done", "cancelled", "1_done"]]]),
                    this.orm.searchCount("project.task", [["project_id", "=", false], ["state", "not in", ["done", "cancelled", "1_done"]], ["date_deadline", "<", new Date().toISOString().slice(0, 10)]]),
                ]);
                return overdue ? `${total} tasks, ${overdue} overdue` : `${total} tasks`;
            }
            if (x.includes("project") && !x.includes("todo")) {
                const cnt = await this.orm.searchCount("project.project", [["active", "=", true]]);
                return `${cnt} active projects`;
            }
            if (x.includes("calendar") || n.includes("calendar")) {
                const cnt = await this.orm.searchCount("calendar.event", [["start", ">=", todayStart()], ["start", "<=", todayEnd()]]);
                return cnt ? `${cnt} events today` : "No events today";
            }
            if ((x.includes("sale") || n.includes("sales")) && !x.includes("account")) {
                const cnt = await this.orm.searchCount("sale.order", [["state", "in", ["sale", "done"]]]);
                return `${cnt} confirmed orders`;
            }
            if (x.includes("account") || n.includes("account")) {
                const cnt = await this.orm.searchCount("account.move", [["state", "=", "posted"], ["payment_state", "!=", "paid"], ["move_type", "=", "out_invoice"]]);
                return cnt ? `${cnt} unpaid invoices` : "All invoices paid";
            }
            if (x.includes("purchase") || n.includes("purchase")) {
                const cnt = await this.orm.searchCount("purchase.order", [["state", "in", ["purchase", "done"]]]);
                return `${cnt} orders`;
            }
            if (x.includes("stock") || x.includes("inventory")) {
                const cnt = await this.orm.searchCount("stock.picking", [["state", "=", "assigned"]]);
                return `${cnt} ready to process`;
            }
        } catch {}
        return null;
    }

    // ── KPIs ──────────────────────────────────────────────────────────────────

    async _loadKpis() {
        const kpis = [];

        // Revenue from confirmed sales
        try {
            const [row] = await this.orm.readGroup("sale.order", [["state", "in", ["sale", "done"]]], ["amount_untaxed"], []);
            kpis.push({ label: "Total Revenue", value: "$" + formatNum(row.amount_untaxed || 0) });
        } catch { kpis.push({ label: "Total Revenue", value: "–" }); }

        // Internal users
        try {
            const n = await this.orm.searchCount("res.users", [["active", "=", true], ["share", "=", false]]);
            kpis.push({ label: "Active Users", value: String(n) });
        } catch { kpis.push({ label: "Active Users", value: "–" }); }

        // Open opportunities
        try {
            const n = await this.orm.searchCount("crm.lead", [["type", "=", "opportunity"], ["active", "=", true]]);
            kpis.push({ label: "Open Deals", value: String(n) });
        } catch { kpis.push({ label: "Open Deals", value: "–" }); }

        // Task completion %
        try {
            const [total, done] = await Promise.all([
                this.orm.searchCount("project.task", [["active", "=", true]]),
                this.orm.searchCount("project.task", [["active", "=", true], ["state", "in", ["done", "1_done"]]]),
            ]);
            const pct = total > 0 ? Math.round((done / total) * 100) : 0;
            kpis.push({ label: "Task Completion", value: pct + "%" });
        } catch { kpis.push({ label: "Task Completion", value: "–" }); }

        this.state.kpis = kpis;
    }

    // ── Recent activity ───────────────────────────────────────────────────────

    async _loadRecent() {
        try {
            const rows = await this.orm.searchRead(
                "mail.message",
                [["message_type", "in", ["email", "comment"]], ["model", "!=", false]],
                ["body", "date", "author_id", "record_name"],
                { limit: 6, order: "date desc" }
            );
            this.state.recent = rows.map((r) => ({
                id: r.id,
                record: r.record_name || (r.author_id ? r.author_id[1] : ""),
                author: r.author_id ? r.author_id[1] : "System",
                body: stripHtml(r.body).slice(0, 55) || "(no content)",
                ago: timeAgo(r.date),
            }));
        } catch {}
    }

    // ── Daily plan ────────────────────────────────────────────────────────────

    async _loadPlan() {
        const plan = [];

        try {
            const evs = await this.orm.searchRead(
                "calendar.event",
                [["start", ">=", todayStart()], ["start", "<=", todayEnd()]],
                ["name", "start"],
                { limit: 4, order: "start asc" }
            );
            for (const e of evs) {
                const t = new Date(e.start.includes("T") ? e.start : e.start + "Z");
                plan.push({
                    icon: "fa-calendar-o",
                    label: e.name,
                    detail: t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
                });
            }
        } catch {}

        try {
            const tasks = await this.orm.searchRead(
                "project.task",
                [["date_deadline", "=", new Date().toISOString().slice(0, 10)], ["state", "not in", ["done", "1_done", "cancelled"]]],
                ["name"],
                { limit: 3 }
            );
            for (const t of tasks) {
                plan.push({ icon: "fa-check-square-o", label: t.name, detail: "Due today" });
            }
        } catch {}

        this.state.plan = plan;
    }

    // ── Computed ──────────────────────────────────────────────────────────────

    get categories() {
        const present = new Set(this._apps.map((a) => a._cat));
        const ordered = CAT_ORDER.filter((c) => present.has(c));
        for (const c of present) if (!ordered.includes(c)) ordered.push(c);
        return ["All", ...ordered];
    }

    get groups() {
        const q = this.state.query.trim().toLowerCase();
        const activeCat = this.state.category;
        const filtered = this._apps.filter((app) => {
            if (activeCat !== "All" && app._cat !== activeCat) return false;
            if (q && !app.name.toLowerCase().includes(q) && !(app.xmlid || "").toLowerCase().includes(q)) return false;
            return true;
        });
        const bycat = {};
        for (const app of filtered) (bycat[app._cat] = bycat[app._cat] || []).push(app);
        const result = [];
        for (const cat of CAT_ORDER) if (bycat[cat]) result.push({ category: cat, apps: bycat[cat] });
        for (const cat of Object.keys(bycat)) if (!CAT_ORDER.includes(cat)) result.push({ category: cat, apps: bycat[cat] });
        return result;
    }

    // Apps shown in the "General Applications" section of the right panel.
    get glanceApps() {
        const shown = new Set(this.groups.flatMap((g) => g.apps.map((a) => a.id)));
        // Prefer "Other" apps; fall back to last group's tail if Other is empty.
        const others = this._apps.filter((a) => a._cat === "Other" && shown.has(a.id));
        return (others.length ? others : this._apps.filter((a) => shown.has(a.id)).slice(-4)).slice(0, 4);
    }

    // ── Icon ──────────────────────────────────────────────────────────────────

    iconBg(app) {
        if (this.state.iconErrors[app.id] && app._icon && app._icon.backgroundColor) {
            return `background-color: ${app._icon.backgroundColor};`;
        }
        return "";
    }

    onIconError(ev) {
        const id = parseInt(ev.target.dataset.menuId, 10);
        if (id) this.state.iconErrors[id] = true;
    }

    // ── Event handlers ────────────────────────────────────────────────────────

    onSearchInput(ev) { this.state.query = ev.target.value; }
    clearSearch()      { this.state.query = ""; }

    onFilterClick(ev) {
        const cat = ev.currentTarget.dataset.cat;
        if (cat) this.state.category = cat;
    }

    async onAppClick(ev) {
        const id = parseInt(ev.currentTarget.dataset.appId, 10);
        const app = this._appsById[id];
        if (app) await this.menuService.selectMenu(app);
    }
}

registry.category("actions").add("odoo_home_dashboard.HomeDashboard", HomeDashboard);
