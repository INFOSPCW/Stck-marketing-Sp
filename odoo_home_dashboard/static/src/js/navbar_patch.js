/** @odoo-module **/
/**
 * Patches NavBar to add openHomeDashboard() — called by the patched
 * AppsMenu template when the user clicks the ⊞ apps button.
 *
 * We look up the client action by its tag once (cached), then use
 * doAction to navigate to the dashboard.
 */

import { NavBar } from "@web/webclient/navbar/navbar";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

let _cachedActionId = null;

async function resolveActionId() {
    if (_cachedActionId) return _cachedActionId;
    const records = await rpc("/web/dataset/call_kw/ir.actions.client/search_read", {
        model: "ir.actions.client",
        method: "search_read",
        args: [[["tag", "=", "odoo_home_dashboard.HomeDashboard"]]],
        kwargs: { fields: ["id"], limit: 1 },
    });
    if (records && records.length) {
        _cachedActionId = records[0].id;
    }
    return _cachedActionId;
}

patch(NavBar.prototype, {
    setup() {
        super.setup(...arguments);
        // Store a reference to action service for use in openHomeDashboard
        this._hdActionService = useService("action");
    },

    async openHomeDashboard() {
        const actionId = await resolveActionId();
        if (actionId) {
            await this._hdActionService.doAction(actionId, { clearBreadcrumbs: true });
        } else {
            // Fallback if action wasn't found (e.g. module not installed yet)
            window.location.href = "/odoo";
        }
    },
});
