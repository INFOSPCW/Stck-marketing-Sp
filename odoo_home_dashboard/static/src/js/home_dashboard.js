/** @odoo-module **/
/**
 * Enterprise-Style Home Dashboard — Odoo 19 Community
 *
 * Reads apps from the live menu service so it only shows apps that are
 * installed *and* visible to the current user. No static list needed.
 */

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { computeAppsAndMenuItems } from "@web/webclient/menus/menu_helpers";

// ─── Category inference ──────────────────────────────────────────────────────
// Order in which category sections appear on the dashboard.
const CAT_ORDER = [
    "Sales",
    "Accounting",
    "Human Resources",
    "Supply Chain",
    "Marketing",
    "Website",
    "Productivity",
    "Other",
];

/**
 * Maps an app (from computeAppsAndMenuItems) to a human-readable category
 * using its xmlid and label. Falls back to "Other".
 */
function getCategory(app) {
    const x = (app.xmlid || "").toLowerCase();
    const n = (app.label || "").toLowerCase();

    if (x.match(/\b(sale|crm|point_of_sale|contacts|loyalty)\b/) ||
        n.match(/\b(sales|crm|point of sale|contacts)\b/))
        return "Sales";

    if (x.match(/\baccount/) || n.match(/\b(invoice|accounting|payment)\b/))
        return "Accounting";

    if (x.match(/\b(hr|fleet|lunch)\b/) ||
        n.match(/\b(employee|recruitment|attendance|time off|expense|payroll|fleet|leave|holiday)\b/))
        return "Human Resources";

    if (x.match(/\b(stock|mrp|purchase|maintenance|repair|delivery|inventory)\b/) ||
        n.match(/\b(inventory|manufacturing|purchase|repair|maintenance|stock)\b/))
        return "Supply Chain";

    if (x.match(/\b(mass_mailing|event|survey|marketing|social)\b/) ||
        n.match(/\b(marketing|email|sms|survey|event)\b/))
        return "Marketing";

    if (x.match(/\b(website|im_livechat|slides|ecommerce|blog)\b/) ||
        n.match(/\b(website|ecommerce|live chat|elearning|blog)\b/))
        return "Website";

    if (x.match(/\b(project|mail|calendar|board|spreadsheet|discuss|todo|note)\b/) ||
        n.match(/\b(project|discuss|calendar|dashboard|to-do|todo|timesheet)\b/))
        return "Productivity";

    return "Other";
}

// ─── Component ───────────────────────────────────────────────────────────────
export class HomeDashboard extends Component {
    static template = "odoo_home_dashboard.HomeDashboard";
    static props = {};

    setup() {
        this.menuService = useService("menu");
        this.actionService = useService("action");

        this.state = useState({ query: "", category: "All" });

        // Build app list once — menu service already filters to apps the
        // current user can see (installed + access rights enforced server-side).
        const tree = this.menuService.getMenuAsTree("root");
        const { apps } = computeAppsAndMenuItems(tree);
        this._apps = apps;
    }

    // ── Derived data ─────────────────────────────────────────────────────────

    /** Unique category labels present in the app list, in canonical order. */
    get categories() {
        const present = new Set(this._apps.map(getCategory));
        const ordered = CAT_ORDER.filter((c) => present.has(c));
        for (const c of present) {
            if (!ordered.includes(c)) ordered.push(c);
        }
        return ["All", ...ordered];
    }

    /**
     * Apps filtered by search query + active category tab,
     * returned as [{category, apps}] groups in canonical order.
     */
    get groups() {
        const q = this.state.query.trim().toLowerCase();
        const activeCat = this.state.category;

        const filtered = this._apps.filter((app) => {
            const cat = getCategory(app);
            const matchCat = activeCat === "All" || cat === activeCat;
            const matchQ =
                !q ||
                (app.label || "").toLowerCase().includes(q) ||
                (app.xmlid || "").toLowerCase().includes(q);
            return matchCat && matchQ;
        });

        // Group by category
        const bycat = {};
        for (const app of filtered) {
            const cat = getCategory(app);
            (bycat[cat] = bycat[cat] || []).push(app);
        }

        // Sort groups by canonical order
        const result = [];
        for (const cat of CAT_ORDER) {
            if (bycat[cat]) result.push({ category: cat, apps: bycat[cat] });
        }
        for (const cat of Object.keys(bycat)) {
            if (!CAT_ORDER.includes(cat)) result.push({ category: cat, apps: bycat[cat] });
        }
        return result;
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    /**
     * Returns a CSS background-color style for the icon wrapper when the app
     * uses a font-icon (web_icon) rather than a binary image.
     */
    iconBg(app) {
        if (!app.webIconData && app.webIcon && app.webIcon.backgroundColor) {
            return `background-color: ${app.webIcon.backgroundColor};`;
        }
        return "";
    }

    // ── Event handlers ───────────────────────────────────────────────────────

    setCategory(cat) {
        this.state.category = cat;
    }

    async openApp(app) {
        await this.menuService.selectMenu(app);
    }
}

// Register as a client action — the tag must match `ir.actions.client.tag`
// in views/home_dashboard_action.xml.
registry.category("actions").add("odoo_home_dashboard.HomeDashboard", HomeDashboard);
