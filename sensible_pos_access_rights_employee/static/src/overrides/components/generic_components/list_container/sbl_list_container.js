/** @odoo-module */

import { ListContainer } from "@point_of_sale/app/components/list_container/list_container";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { xml } from "@odoo/owl";

patch(ListContainer.prototype, {
    setup() {
        super.setup();
        this.pos = useService("pos");
    },

    isCreateNewOrderButtonVisible() {
        const employee = this.pos.cashier;
        return employee && !employee.sbl_hide_pos_new_order_button;
    },
});

patch(ListContainer, {
    template: xml`
        <div class="overflow-hidden d-flex flex-grow-1" t-attf-class="{{props.class}}">
            <button t-if="props.onClickPlus and isCreateNewOrderButtonVisible()" class="list-plus-btn btn btn-secondary btn-lg me-1" t-on-click="props.onClickPlus">
                <i class="fa fa-fw fa-plus-circle" aria-hidden="true"/>
            </button>
            <button t-if="this.sizing.isLarger or props.forceSmall" t-on-click="toggle"
                class="btn btn-secondary mx-1 fa fa-caret-down" />
            <div class="overflow-hidden w-100 position-relative">
                <div t-ref="container" class="list-container-items d-flex w-100">
                    <div t-if="!props.forceSmall" t-foreach="props.items" t-as="item" t-key="item_index" t-att-class="{'invisible': shouldBeInvisible(item_index)}">
                        <t t-slot="default" item="item"/>
                    </div>
                </div>
            </div>
        </div>
    `,
});
