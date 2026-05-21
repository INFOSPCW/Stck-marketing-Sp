# -*- coding: utf-8 -*-

import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def get_home_screen_apps(self):
        """
        Return all root menu items (apps) visible to the current user
        for display on the home screen dashboard
        """
        try:
            # Use Odoo's standard load_menus method to get all menu data
            menus = self.sudo().load_menus(debug=False)
            _logger.info(f"Total menus loaded: {len(menus)}")

            # Get root menu to find app IDs
            root_menu = menus.get('root', {})
            app_ids = root_menu.get('children', [])
            _logger.info(f"Root apps found: {len(app_ids)}")
        except Exception as e:
            _logger.error(f"Error loading menus: {e}", exc_info=True)
            return []

        apps_data = []
        for app_id in app_ids:
            try:
                menu = menus.get(app_id)
                if not menu:
                    continue

                _logger.info(f"Processing app: {menu.get('name')} (id={app_id})")
                _logger.info(f"  web_icon: {menu.get('web_icon')}")
                _logger.info(f"  web_icon_data: {bool(menu.get('web_icon_data'))}")
                _logger.info(f"  web_icon_data_mimetype: {menu.get('web_icon_data_mimetype')}")

                # Get the icon information
                icon_data = self._parse_menu_icon(menu)

                app_data = {
                    'id': menu['id'],
                    'name': menu['name'],
                    'xmlid': menu.get('xmlid', ''),
                    'action_id': False,
                    'icon_class': icon_data.get('icon_class', 'fa fa-th'),
                    'icon_color': icon_data.get('icon_color', '#FFFFFF'),
                    'background_color': icon_data.get('background_color', '#875A7B'),
                }

                # Get action from menu (action is stored as "model,id" string)
                action = menu.get('action')
                if action:
                    action_parts = action.split(',')
                    if len(action_parts) == 2:
                        app_data['action_id'] = int(action_parts[1])

                # Include web_icon_data if available (already base64 encoded from load_menus)
                if icon_data.get('web_icon_data'):
                    app_data['web_icon_data'] = icon_data['web_icon_data']
                    app_data['web_icon_data_mimetype'] = icon_data.get('web_icon_data_mimetype', 'image/png')

                # Include module icon URL if available
                if icon_data.get('module_icon_url'):
                    app_data['module_icon_url'] = icon_data['module_icon_url']

                apps_data.append(app_data)
                _logger.info(f"Added app: {menu['name']} (icon_data keys: {list(icon_data.keys())})")
            except Exception as e:
                _logger.error(f"Error processing menu {app_id}: {e}", exc_info=True)
                continue

        _logger.info(f"Returning {len(apps_data)} apps")
        return apps_data

    def _parse_menu_icon(self, menu):
        """
        Extract icon information from menu dictionary (from load_menus)
        Returns dict with icon_class, icon_color, background_color, web_icon_data, and module_icon_url
        """
        result = {
            'icon_class': 'fa fa-th',
            'icon_color': '#FFFFFF',
            'background_color': '#875A7B',
        }

        # Check if binary icon data exists (already base64 encoded from load_menus)
        if menu.get('web_icon_data'):
            result['web_icon_data'] = menu['web_icon_data']
            result['web_icon_data_mimetype'] = menu.get('web_icon_data_mimetype', 'image/png')
            return result

        # Parse web_icon field
        web_icon = menu.get('web_icon', '')
        if web_icon:
            # Parse web_icon format: "module,path" or "fa fa-icon,#bgcolor" or "fa fa-icon,#bgcolor,#color"
            parts = [p.strip() for p in web_icon.split(',')]

            if len(parts) >= 1:
                first_part = parts[0].strip()

                if first_part.startswith('fa '):
                    # FontAwesome icon: "fa fa-icon" or "fa fa-icon,#bgcolor" or "fa fa-icon,#bgcolor,#color"
                    result['icon_class'] = first_part

                    # Parse colors if provided
                    if len(parts) >= 2 and parts[1].strip().startswith('#'):
                        result['background_color'] = parts[1].strip()
                    if len(parts) >= 3 and parts[2].strip().startswith('#'):
                        result['icon_color'] = parts[2].strip()
                else:
                    # Module icon path: "module_name,path/to/icon.png"
                    # Construct the URL to the module's static file
                    module_name = first_part
                    icon_path = parts[1].strip() if len(parts) >= 2 else 'static/description/icon.png'
                    # Format: /module_name/path/to/icon.png (Odoo standard static file serving)
                    result['module_icon_url'] = f"/{module_name}/{icon_path}"

        return result

    def _get_fontawesome_icon_for_module(self, module_name):
        """
        Return a FontAwesome icon class based on the module name
        """
        # Map common module names to appropriate FontAwesome icons
        icon_map = {
            'mail': 'fa fa-envelope',
            'discuss': 'fa fa-comments',
            'contacts': 'fa fa-users',
            'crm': 'fa fa-list',
            'sale': 'fa fa-shopping-cart',
            'stock': 'fa fa-cubes',
            'purchase': 'fa fa-file-alt',
            'account': 'fa fa-dollar',
            'invoicing': 'fa fa-file-text',
            'accounting': 'fa fa-calculator',
            'inventory': 'fa fa-warehouse',
            'fleet': 'fa fa-car',
            'hr': 'fa fa-users',
            'hrm': 'fa fa-users',
            'payroll': 'fa fa-money',
            'website': 'fa fa-globe',
            'ecommerce': 'fa fa-shopping-cart',
            'marketing': 'fa fa-bullhorn',
            'email_marketing': 'fa fa-envelope',
            'sms_marketing': 'fa fa-mobile',
            'events': 'fa fa-calendar',
            'helpdesk': 'fa fa-headphones',
            'project': 'fa fa-tasks',
            'planning': 'fa fa-calendar',
            'timesheet': 'fa fa-clock-o',
            'spreadsheet': 'fa fa-table',
            'documents': 'fa fa-file',
            'knowledge': 'fa fa-book',
            'sign': 'fa fa-edit',
            'iot': 'fa fa-internet-explorer',
            'iot_server': 'fa fa-rss',
            'barcode': 'fa fa-barcode',
            'pos': 'fa fa-cash-register',
            'restaurant': 'fa fa-utensils',
            'quality': 'fa fa-check',
            'maintenance': 'fa fa-wrench',
            'survey': 'fa fa-poll',
            'social_media': 'fa fa-share-alt',
            'landbot': 'fa fa-comments',
            'whatsapp': 'fa fa-whatsapp',
            'google': 'fa fa-google',
            'microsoft': 'fa fa-windows',
            'dropbox': 'fa fa-dropbox',
            'slack': 'fa fa-slack',
            'base': 'fa fa-cogs',
            'settings': 'fa fa-cog',
            'apps': 'fa fa-th',
        }

        # Check if module name is in the map
        if module_name in icon_map:
            return icon_map[module_name]

        # Check for partial matches (e.g., "sale_management" -> "sale")
        for key, icon in icon_map.items():
            if key in module_name:
                return icon

        # Default icon
        return 'fa fa-th'
