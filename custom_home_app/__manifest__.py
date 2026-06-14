{
    'name': 'Dynamic Home Dashboard',
    'version': '19.0.1.1.0',
    'summary': 'Drag-and-drop Odoo home menu with app search, live clock, A-Z sorting, and mobile-ready dashboard layout.',
    'category': 'Productivity',
    'author': 'SnakeDrag Tech Solutions',
    'maintainer': 'SnakeDrag Tech Solutions',
    'support': 'snakedragtechsolutions@gmail.com',
    'license': 'OPL-1',    
    'description': """
        Odoo 19 Home Menu Dashboard Customizer
        ======================================
        Upgrade the standard Odoo Enterprise home menu into a customizable dashboard that helps users reach apps faster.

        Main Features
        -------------
        * Drag-and-drop app arrangement for a personalized Odoo home screen
        * Fast app and menu search directly from the dashboard
        * A-Z sorting, Z-A sorting, and one-click reset to default order
        * Live digital clock for kiosk-style or productivity-focused workspaces
        * Mobile-friendly and tablet-friendly layout with touch support
        * Per-user saved layout stored in user settings

        Best For
        --------
        * Teams that want a cleaner Odoo app launcher
        * Businesses using many modules and custom apps
        * Faster navigation for sales, inventory, CRM, manufacturing, HR, and service users
    """,
    'depends': ['base', 'web'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/client_action_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_home_app/static/src/xml/home_menu.xml',
            'custom_home_app/static/src/js/home_menu.js',
            'custom_home_app/static/src/scss/home_menu.scss',
        ],
        'web.assets_tests': [
            'custom_home_app/static/tests/tours/**/*.js',
        ],
    },
    'images': ['static/description/images/banner.png'],
    'installable': True,
    'application': True,
    'currency': 'EUR',
    'price': '9.50',
}
