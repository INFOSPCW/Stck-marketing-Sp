/** @odoo-module **/
import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { patch } from "@web/core/utils/patch";
import { onMounted, onPatched } from "@odoo/owl";

/**
 * Hides/shows the Cancel Order button based on employee permissions.
 *
 * We use DOM lifecycle hooks instead of a QWeb XPath because the Cancel
 * Order button's t-on-click handler differs across Odoo 19 builds
 * (community vs enterprise), making a reliable XPath selector impossible.
 *
 * The button is identified by its fa-trash icon, which is stable across
 * all known builds. We also intercept onCancelOrder() as a second layer
 * of defence in case the DOM manipulation misses the button.
 */
patch(ControlButtons.prototype, {
    setup() {
        super.setup(...arguments);

        const updateCancelOrderVisibility = () => {
            if (!this.el) {
                return;
            }
            const employee = this.pos.cashier;
            const hide = Boolean(employee?.sbl_hide_pos_action_cancel_order);
            // Locate the Cancel Order button by its trash icon (stable across builds)
            const trashIcon = this.el.querySelector(".fa-trash");
            if (trashIcon) {
                const cancelBtn = trashIcon.closest("button");
                if (cancelBtn) {
                    cancelBtn.style.display = hide ? "none" : "";
                }
            }
        };

        onMounted(updateCancelOrderVisibility);
        onPatched(updateCancelOrderVisibility);
    },

    onCancelOrder() {
        // Second layer of defence: even if the button is somehow visible,
        // block the action when the employee lacks permission.
        const employee = this.pos.cashier;
        if (employee?.sbl_hide_pos_action_cancel_order) {
            return;
        }
        return super.onCancelOrder(...arguments);
    },
});
