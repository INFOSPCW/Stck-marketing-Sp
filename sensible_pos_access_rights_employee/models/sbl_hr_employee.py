# Powered by Sensible Consulting Services
# -*- coding: utf-8 -*-
# © 2025 Sensible Consulting Services (<https://sensiblecs.com/>)
from odoo import api, models, fields


class HrEmployeeBase(models.Model):
    _inherit = 'hr.employee'

    sbl_hide_pos_new_order_button = fields.Boolean(
        string='Hide POS New Order Button',
        help='If checked, the New Order button will be hidden for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_delete_order_button = fields.Boolean(
        string='Hide POS Delete Order Button',
        help='If checked, the Delete Order button will be hidden for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_customer_selection_button = fields.Boolean(
        string='Hide POS Customer Selection Button',
        help='If checked, the Customer Selection button will be hidden for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_actions_button = fields.Boolean(
        string='Hide POS Actions Button',
        help='If checked, the Actions button will be hidden for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_numpad = fields.Boolean(
        string='Hide POS Numpad',
        help='If checked, the Numpad will be hidden for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_disable_pos_numpad_plus_minus = fields.Boolean(
        string='Disable POS Numpad Plus-Minus Buttons',
        help='If checked, the Plus-Minus buttons in the Numpad will be disabled for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_disable_pos_qty = fields.Boolean(
        string='Disable POS Quantity (QTY) Button',
        help='If checked, the Quantity (QTY) button will be disabled for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_disable_pos_discount_button = fields.Boolean(
        string='Disable POS Discount Button',
        help='If checked, the Discount button will be disabled for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_payment = fields.Boolean(
        string='Hide POS Payment',
        help='If checked, the Payment process will be hidden for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_disable_pos_change_price = fields.Boolean(
        string='Disable POS Change Price',
        help='If checked, the Change Price functionality will be disabled for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_finance_from_product_info = fields.Boolean(
        string='Hide Financials and Order from Product Info',
        help='If checked, the Financials and Order sections will be hidden in the Product Information popup for this employee.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_cash_in_out = fields.Boolean(
        string='Hide POS Cash In/Out',
        help='If checked, the Cash In/Out menu will be hidden for this employee in the POS interface.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_create_customer_button = fields.Boolean(
        string='Hide POS Create Customer Button',
        help='If checked, the Create/New Customer button will be hidden in the customer list popup.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_edit_customer_button = fields.Boolean(
        string='Hide Edit Customer Button',
        help='If checked, the Edit Details option will be hidden from the customer list for this employee.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_payment_customer_button = fields.Boolean(
        string='Hide Customer Button',
        help='If checked, the Customer button will be hidden on the payment screen.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_payment_invoice_button = fields.Boolean(
        string='Hide Invoice Button',
        help='If checked, the Invoice checkbox will be hidden on the payment screen.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_payment_validate_button = fields.Boolean(
        string='Hide Validate Button',
        help='If checked, the Validate button will be hidden on the payment screen.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_payment_ship_later_button = fields.Boolean(
        string='Hide Ship Later Button',
        help='If checked, the Ship Later button will be hidden on the payment screen.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_tip_button = fields.Boolean(
        string='Hide Tip Button',
        help='If checked, the Tip button will be hidden on the payment screen.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_open_cashbox_button = fields.Boolean(
        string='Hide Open Cashbox Button',
        help='If checked, the Open Cashbox button will be hidden on the payment screen.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_disabled_payment_method_ids = fields.Many2many(
        'pos.payment.method',
        'sbl_hr_employee_disabled_payment_method_rel',
        'employee_id',
        'payment_method_id',
        string='Disabled Payment Methods',
        help='Payment methods that will be hidden for this employee on the POS payment screen.',
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_install_app = fields.Boolean(
        string='Hide Install App Menu',
        help='If checked, the Install App option will be hidden from the POS hamburger menu.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_backend_menu = fields.Boolean(
        string='Hide Backend Menu',
        help='If checked, the Backend option will be hidden from the POS hamburger menu.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_close_register = fields.Boolean(
        string='Hide Close Register Menu',
        help='If checked, the Close Register option will be hidden from the POS hamburger menu.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_orders_menu = fields.Boolean(
        string='Hide Orders Menu',
        help='If checked, the Orders button in the navbar will be hidden for this employee.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_reload_data = fields.Boolean(
        string='Hide Reload Data Menu',
        help='If checked, the Reload Data option will be hidden from the navbar hamburger menu.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_create_product = fields.Boolean(
        string='Hide Create Product Menu',
        help='If checked, the Create Product option will be hidden from the navbar hamburger menu.',
        default=False,
        groups="hr.group_hr_user"
    )
    sbl_hide_pos_debug_button = fields.Boolean(
        string='Hide Debug Button',
        help='If checked, the Debug button (bottom right) will be hidden for this employee.',
        default=False,
        groups="hr.group_hr_user"
    )

    # POS Actions Popup - Base Actions Only
    sbl_hide_pos_action_general_note = fields.Boolean(
        string='Hide General Note Button',
        help='If checked, the General Note button will be hidden in the Actions popup.',
        default=False,
        groups="hr.group_hr_user",
    )
    sbl_hide_pos_action_customer_note = fields.Boolean(
        string='Hide Customer Note Button',
        help='If checked, the Customer Note button will be hidden in the Actions popup.',
        default=False,
        groups="hr.group_hr_user",
    )
    sbl_hide_pos_action_pricelist = fields.Boolean(
        string='Hide Pricelist Button',
        help='If checked, the Pricelist button will be hidden in the Actions popup.',
        default=False,
        groups="hr.group_hr_user",
    )
    sbl_hide_pos_action_refund = fields.Boolean(
        string='Hide Refund Button',
        help='If checked, the Refund button will be hidden in the Actions popup.',
        default=False,
        groups="hr.group_hr_user",
    )
    sbl_hide_pos_action_fiscal_position = fields.Boolean(
        string='Hide Fiscal Position Button',
        help='If checked, the Fiscal Position/Tax button will be hidden in the Actions popup.',
        default=False,
        groups="hr.group_hr_user",
    )
    sbl_hide_pos_action_cancel_order = fields.Boolean(
        string='Hide Cancel Order Button',
        help='If checked, the Cancel Order button will be hidden in the Actions popup.',
        default=False,
        groups="hr.group_hr_user",
    )
    sbl_hide_pos_action_product_info = fields.Boolean(
        string='Hide Product Info Button',
        help='If checked, the Product Info button will be hidden in the Actions popup.',
        default=False,
        groups="hr.group_hr_user",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        return fields + [
            'sbl_hide_pos_new_order_button', 'sbl_hide_pos_delete_order_button',
            'sbl_hide_pos_customer_selection_button', 'sbl_hide_pos_actions_button',
            'sbl_hide_pos_numpad', 'sbl_disable_pos_numpad_plus_minus', 'sbl_disable_pos_qty',
            'sbl_disable_pos_discount_button', 'sbl_hide_pos_payment', 'sbl_disable_pos_change_price',
            'sbl_hide_pos_finance_from_product_info', 'sbl_hide_pos_cash_in_out',
            'sbl_hide_pos_create_customer_button', 'sbl_hide_pos_edit_customer_button',
            'sbl_hide_pos_payment_customer_button', 'sbl_hide_pos_payment_invoice_button',
            'sbl_hide_pos_payment_validate_button', 'sbl_hide_pos_payment_ship_later_button',
            'sbl_hide_pos_tip_button', 'sbl_hide_pos_open_cashbox_button',
            'sbl_disabled_payment_method_ids',
            'sbl_hide_pos_install_app', 'sbl_hide_pos_backend_menu',
            'sbl_hide_pos_close_register',
            'sbl_hide_pos_reload_data', 'sbl_hide_pos_create_product',
            'sbl_hide_pos_debug_button',
            'sbl_hide_pos_orders_menu',
            'sbl_hide_pos_action_general_note', 'sbl_hide_pos_action_customer_note',
            'sbl_hide_pos_action_pricelist', 'sbl_hide_pos_action_refund',
            'sbl_hide_pos_action_fiscal_position', 'sbl_hide_pos_action_cancel_order',
            'sbl_hide_pos_action_product_info',
        ]