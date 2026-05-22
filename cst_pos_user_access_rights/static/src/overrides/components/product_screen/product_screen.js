import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";

patch(ProductScreen.prototype, {
    getNumpadButtons() {
        const buttons = super.getNumpadButtons();
        console.log(buttons);
        const currentUser = this.pos?.user;

        if (!currentUser) {
            return buttons;
        }

        for (const button of buttons) {
            switch (button.value) {
                case "quantity":
                    button.disabled = currentUser.disable_pos_qty;
                    break;

                case "price":
                    button.disabled =
                        !this.pos.cashierHasPriceControlRights() ||
                        currentUser.disable_pos_change_price;
                    break;

                case "discount":
                    button.disabled =
                        !this.pos.config.manual_discount ||
                        currentUser.disable_pos_discount_button;
                    break;
                case "Backspace":
                    button.disabled = currentUser.disable_pos_remove_button;
                    break;
                case "-":
                    button.disabled = currentUser.disable_pos_numpad_plus_minus;
                    break;
            }
        }

        const activeButton = buttons.find(
            (button) => button.value === this.pos.numpadMode
        );

        if (activeButton?.disabled) {
            for (const button of buttons) {
                if (!["quantity", "discount", "price", "-"].includes(button.value)) {
                    button.disabled = true;
                }
            }
        }

        return buttons;
    },
});
