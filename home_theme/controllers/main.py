# -*- coding: utf-8 -*-

import logging
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class HomeScreenController(http.Controller):

    @http.route('/web/home_screen/save_order', type='json', auth='user')
    def save_app_order(self, app_ids):
        """
        Save the custom order of apps for the current user
        app_ids: list of menu IDs in the desired order
        """
        try:
            user = request.env.user
            _logger.info(f"Saving app order for user {user.name} (id={user.id}): {app_ids}")

            HomeAppSequence = request.env['home.app.sequence'].sudo()

            # Update or create sequence records for each app
            for index, menu_id in enumerate(app_ids):
                sequence_record = HomeAppSequence.search([
                    ('user_id', '=', user.id),
                    ('menu_id', '=', menu_id)
                ], limit=1)

                if sequence_record:
                    _logger.info(f"Updating sequence for menu {menu_id}: {index}")
                    sequence_record.write({'sequence': index})
                else:
                    _logger.info(f"Creating new sequence for menu {menu_id}: {index}")
                    HomeAppSequence.create({
                        'user_id': user.id,
                        'menu_id': menu_id,
                        'sequence': index
                    })

            _logger.info("App order saved successfully")
            return {'success': True}
        except Exception as e:
            _logger.error(f"Error saving app order: {e}", exc_info=True)
            return {'success': False, 'error': str(e)}

    @http.route('/web/home_screen', type='json', auth='user')
    def get_home_screen_data(self):
        """
        Return data for the home screen dashboard
        """
        try:
            IrUiMenu = request.env['ir.ui.menu'].sudo()
            IrConfigParameter = request.env['ir.config_parameter'].sudo()

            apps = IrUiMenu.get_home_screen_apps()

            # Apply custom ordering if exists
            user = request.env.user
            HomeAppSequence = request.env['home.app.sequence'].sudo()
            user_sequences = HomeAppSequence.search([('user_id', '=', user.id)])

            _logger.info(f"Found {len(user_sequences)} custom sequences for user {user.name} (id={user.id})")

            if user_sequences:
                # Create a map of menu_id to sequence
                sequence_map = {seq.menu_id.id: seq.sequence for seq in user_sequences}
                _logger.info(f"Sequence map: {sequence_map}")

                # Sort apps based on custom sequence
                def get_sequence(app):
                    return sequence_map.get(app['id'], 9999)  # Apps without custom order go to end

                apps = sorted(apps, key=get_sequence)
                _logger.info(f"Apps after sorting: {[app['id'] for app in apps]}")

            # Get background settings
            background_type = IrConfigParameter.get_param('home_theme.background_type', 'gradient')
            background_image = False

            if background_type == 'image':
                attachment_id = IrConfigParameter.get_param('home_theme.background_image_attachment_id', False)
                if attachment_id:
                    attachment = request.env['ir.attachment'].sudo().browse(int(attachment_id))
                    if attachment.exists() and attachment.datas:
                        # Return base64 encoded image data
                        background_image = attachment.datas.decode('utf-8') if isinstance(attachment.datas, bytes) else attachment.datas

            background_color = IrConfigParameter.get_param('home_theme.background_color', '#f5f7fa')

            return {
                'apps': apps,
                'user_name': request.env.user.sudo().name,
                'company_name': request.env.company.sudo().name,
                'background_type': background_type,
                'background_image': background_image,
                'background_color': background_color,
            }
        except Exception as e:
            _logger.error(f"Error fetching home screen data: {e}", exc_info=True)
            return {
                'apps': [],
                'user_name': request.env.user.sudo().name,
                'company_name': request.env.company.sudo().name,
                'background_image': False,
                'background_color': '#f5f7fa',
                'background_type': 'gradient',
            }
