# -*- coding: utf-8 -*-
{
    'name': 'Enterprise-Style Home Dashboard',
    'version': '19.0.1.0.0',
    'category': 'Extra Tools',
    'summary': 'Shows all installed apps as an Enterprise-style home grid dashboard',
    'description': """
Enterprise-Style Home Dashboard for Odoo 19 Community
======================================================
Replaces the hamburger app-list dropdown with a full-screen grid dashboard,
grouped by business category. Only shows apps that are installed and visible
to the current user — reads live from Odoo's menu service so it stays in
sync automatically. No config needed: just install and reload.
    """,
    'author': 'Custom',
    'depends': ['web'],
    'data': [
        'views/home_dashboard_action.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'odoo_home_dashboard/static/src/scss/home_dashboard.scss',
            'odoo_home_dashboard/static/src/js/home_dashboard.xml',
            'odoo_home_dashboard/static/src/js/home_dashboard.js',
            # navbar_patch disabled: XPath template inheritance can blank all of Odoo
            # if web.NavBar.AppsMenu template structure doesn't match.
            # Dashboard is accessible via the Home menu item instead.
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
