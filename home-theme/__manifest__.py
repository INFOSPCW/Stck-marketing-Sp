# -*- coding: utf-8 -*-
{
    'name': 'Enterprise Home Screen - Modern App Dashboard',
    'version': '18.0.1.0.0',
    'category': 'Themes/Backend',
    'summary': 'Transform Odoo Community with Enterprise-style home screen, custom backgrounds, and drag-drop app ordering',
    'description': """
Enterprise Home Screen - Modern App Dashboard
==============================================

Transform your Odoo Community Edition with a beautiful, professional home screen

Key Features
------------

🎨 **Enterprise-Style Interface**
   • Clean, modern grid layout displaying all installed apps
   • Professional icon display with smooth hover effects
   • Responsive design - works perfectly on desktop, tablet, and mobile
   • Glass-effect transparent navbar for a premium look

🖼️ **Customizable Backgrounds**
   • Choose from 3 background types:
     - Elegant gradient backgrounds (default)
     - Solid color backgrounds with custom hex colors
     - Upload custom images for personalized branding
   • Easy configuration through Settings → Home Screen Theme

📱 **Drag & Drop App Ordering**
   • Rearrange apps by simply dragging and dropping
   • Personal app order saved per user
   • Order persists across sessions and devices
   • Intuitive visual feedback during drag operations

⚡ **Performance & Compatibility**
   • Fast loading with optimized asset delivery
   • Works with all Odoo Community Edition modules
   • No conflicts with existing customizations
   • Fully compatible with Odoo 18.0

🎯 **User Experience**
   • 6 apps per row on desktop for optimal viewing
   • Smooth animations and transitions
   • Click to open apps instantly
   • Professional typography and spacing

Perfect For
-----------
• Companies wanting a professional Odoo interface
• Users transitioning from Odoo Enterprise
• Businesses that value aesthetics and usability
• Teams who want personalized app organization

Installation
------------
1. Install the module from Apps menu
2. Refresh your browser
3. Navigate to the "Home" menu item
4. Customize via Settings → Home Screen Theme

Configuration
-------------
Access Settings → Home Screen Theme to customize:
• Background type (gradient/solid/image)
• Background color (for solid type)
• Upload custom background image
• All settings are system-wide

Technical
---------
• Built with modern OWL (Odoo Web Library) components
• Uses Odoo's native menu system for reliability
• Secure user-specific preferences storage
• Clean, maintainable code following Odoo standards

Support
-------
For issues, suggestions, or feature requests, please contact the module author.

Upgrade Your Odoo Experience Today! ✨
    """,
    'author': 'Roshan',
    'license': 'OPL-1',
    'depends': ['web', 'base'],
    'data': [
        'security/ir.model.access.csv',
        'views/webclient_templates.xml',
        'views/home_screen_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'home-theme/static/src/css/home_screen.css',
            'home-theme/static/src/js/home_screen.js',
            'home-theme/static/src/xml/home_screen.xml',
        ],
    },
    'images': [
        'static/description/screenshot_1_screenshot.png',
        'static/description/screenshot_2.png',
        'static/description/screenshot_3.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
