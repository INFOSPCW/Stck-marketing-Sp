import { TicketScreen } from "@point_of_sale/app/screens/ticket_screen/ticket_screen";
import { patch } from "@web/core/utils/patch";

patch(TicketScreen.prototype, {
    shouldHideDeleteButton(order) {
        const employee = this.pos.cashier;
        if (employee.sbl_hide_pos_delete_order_button) {
            return true;
        }
        return super.shouldHideDeleteButton(order);
    }
});
