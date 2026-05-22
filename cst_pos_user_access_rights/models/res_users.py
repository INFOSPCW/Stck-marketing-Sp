# -*- coding: utf-8 -*-

from odoo import api, models, fields


class ResUsers(models.Model):
    _inherit = 'res.users'

    hide_pos_new_order_button = fields.Boolean(
        string='Hide POS "New Order" Button',
        help='Hide the "New Order" button in the Point of Sale (POS) for this user.',
        default=False,
    )

    hide_pos_delete_order_button = fields.Boolean(
        string='Hide POS "Delete Order" Button',
        help='Hide the "Delete Order" button in the Point of Sale (POS) for this user.',
        default=False,
    )

    hide_pos_customer_selection_button = fields.Boolean(
        string='Hide POS Customer Selection',
        help='Prevent this user from selecting or changing customers in the POS.',
        default=False,
    )

    hide_pos_actions_button = fields.Boolean(
        string='Hide POS Actions Menu',
        help='Hide the Actions menu in the Point of Sale (POS) for this user.',
        default=False,
    )

    hide_pos_numpad = fields.Boolean(
        string='Hide POS Numpad',
        help='Completely hide the numeric keypad in the POS for this user.',
        default=False,
    )

    disable_pos_numpad_plus_minus = fields.Boolean(
        string='Disable POS + / - Buttons',
        help='Disable the plus (+) and minus (–) buttons in the POS numpad for this user.',
        default=False,
    )

    disable_pos_qty = fields.Boolean(
        string='Disable POS Quantity (Qty) Button',
        help='Disable the Quantity (Qty) button in the POS for this user.',
        default=False,
    )

    disable_pos_discount_button = fields.Boolean(
        string='Disable POS Discount(%) Button',
        help='Prevent this user from applying discounts in the POS.',
        default=False,
    )

    hide_pos_payment = fields.Boolean(
        string='Hide POS Payment Button',
        help='Hide the Payment button in the POS for this user.',
        default=False,
    )

    disable_pos_change_price = fields.Boolean(
        string='Disable POS Price Change',
        help='Prevent this user from changing product prices in the POS.',
        default=False,
    )
    disable_pos_remove_button = fields.Boolean(
        string='Disable POS Remove Button(⌫)',
        help='Prevent this user from removing lines from POS Cart',
        default=False,
    )

    hide_pos_finance_from_product_info = fields.Boolean(
        string='Hide Financial Details in Product Info',
        help='Hide financial and order-related details in the Product Information popup.',
        default=False,
    )

    hide_pos_action_product_info = fields.Boolean(
        string='Hide Product Info Action',
        help='Hide the Product Information option from the POS Actions menu.',
        default=False,
    )

    @api.model
    def _load_pos_data_fields(self, config_id):
        fields = super()._load_pos_data_fields(config_id)
        return fields + [
            'hide_pos_new_order_button',
            'hide_pos_delete_order_button',
            'hide_pos_customer_selection_button',
            'hide_pos_actions_button',
            'hide_pos_numpad',
            'disable_pos_numpad_plus_minus',
            'disable_pos_qty',
            'disable_pos_discount_button',
            'hide_pos_payment',
            'disable_pos_change_price',
            'hide_pos_finance_from_product_info',
            'hide_pos_action_product_info',
            'disable_pos_remove_button',
        ]
