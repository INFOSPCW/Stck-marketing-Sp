/** @odoo-module */

import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";
import { patch } from "@web/core/utils/patch";

patch(ReceiptScreen.prototype, {
    get hideNewOrderButton() {
        return this.pos?.user?.hide_pos_new_order_button ?? false;
    },
});
