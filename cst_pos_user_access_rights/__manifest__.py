# -*- coding: utf-8 -*-

{
    "name": "POS User Access Rights",
    "summary": "Manage and restrict Point of Sale access rights at user level.",
    "description": """
        POS User Access Rights allows businesses to control and restrict Point of Sale (POS) operations
        based on individual user settings. This module provides a simple and flexible way to manage
        what POS actions and interface elements are accessible to each user.

        Administrators can configure POS access rules directly from the User form, helping to improve
        operational accuracy, reduce errors, and enforce internal policies without complex configuration.

        This module is designed to be lightweight, easy to configure, and fully compatible with the
        standard Odoo Point of Sale workflow.
        
    """,
    "author": "CodeSphere Tech",
    "website": "https://www.codespheretech.in/",
    "category": "Point Of Sale",
    "version": "19.0.1.0.0",
    "sequence": 0,
    "currency": "USD",
    "price": "0.0",
    "depends": ["base", "point_of_sale", ],
    "data": [
        "views/res_users_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "cst_pos_user_access_rights/static/src/**/*",
        ],
    },
    "images": [
        "static/description/Banner.png",
    ],
    "license": "LGPL-3",
    "installable": True,
    "application": False,
    "auto_install": False,
}
