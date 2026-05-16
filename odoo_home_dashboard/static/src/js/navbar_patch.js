/** @odoo-module **/
/**
 * Navbar patch — injects openHomeDashboard() into the NavBar component
 * so the patched template can call it when the ⊞ apps button is clicked.
 *
 * Also patches session_info on startup so that `user.homeActionId` points
 * to our dashboard, meaning the breadcrumb "home" button and the fallback
 * navigation both land on the grid rather than the default action.
 */

import { NavBar } from "@web/webclient/navbar/navbar";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

/**
 * We need the database ID of our ir.actions.client record.
 * We fetch it once by its XML ID using the standard name_search / read
 * mechanism, cache it in a module-level promise so we only call once.
 */
let _actionIdPromise = null;

function getHomeDashboardActionId() {
    if (!_actionIdPromise) {
        _actionIdPromise = rpc("/web/dataset/call_kw/ir.actions.client/search_read", {
            model: "ir.actions.client",
            method: "search_read",
            args: [[["tag", "=", "odoo_home_dashboard.HomeDashboard"]]],
            kwargs: {
                fields: ["id"],
                limit: 1,
            },
        }).then((records) => {
            if (records && records.length) {
                return records[0].id;
            }
            return null;
        });
    }
    return _actionIdPromise;
}

patch(NavBar.prototype, {
    setup() {
        super.setup(...arguments);
        // Make the action service available to our new method
        this._actionService = useService("action");
    },

    /**
     * Called when the user clicks the ⊞ apps button (or presses Alt+H).
     * Opens our HomeDashboard client action, clearing the breadcrumb stack.
     */
    async openHomeDashboard() {
        const actionId = await getHomeDashboardActionId();
        if (actionId) {
            await this._actionService.doAction(actionId, { clearBreadcrumbs: true });
        } else {
            // Fallback: navigate to /odoo (default Odoo home)
            window.location = "/odoo";
        }
    },
});
