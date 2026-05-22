/** @odoo-module */
// Powered by Sensible Consulting Services
// © 2025 Sensible Consulting Services (<https://sensiblecs.com/>)

import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";

patch(PaymentScreen.prototype, {
    setup() {
        super.setup();
        const cashier = this.pos.cashier;
        const disabledMethods = cashier?.sbl_disabled_payment_method_ids || [];
        if (disabledMethods.length > 0) {
            const disabledMethodIds = disabledMethods.map(dpm => dpm.id);
            this.payment_methods_from_config = this.payment_methods_from_config.filter(
                (pm) => !disabledMethodIds.includes(pm.id)
            );
        }
    },
});
