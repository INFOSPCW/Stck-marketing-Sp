# -*- coding: utf-8 -*-

from odoo import models, fields, api
import base64


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    home_background_image = fields.Binary(
        string='Home Screen Background Image',
        attachment=True,
        help='Upload a custom background image for the home screen dashboard'
    )
    home_background_image_attachment_id = fields.Many2one(
        'ir.attachment',
        string='Background Image Attachment'
    )
    home_background_color = fields.Char(
        string='Background Color',
        default='#f5f7fa',
        help='Background color if no image is set (hex color code)'
    )
    home_background_type = fields.Selection([
        ('gradient', 'Gradient'),
        ('solid', 'Solid Color'),
        ('image', 'Custom Image'),
    ], string='Background Type', default='gradient', help='Select background type')

    # Keep for backward compatibility
    home_use_gradient = fields.Boolean(
        string='Use Gradient Background',
        compute='_compute_use_gradient',
        inverse='_inverse_use_gradient',
        store=False
    )

    @api.depends('home_background_type')
    def _compute_use_gradient(self):
        for record in self:
            record.home_use_gradient = record.home_background_type == 'gradient'

    def _inverse_use_gradient(self):
        for record in self:
            if record.home_use_gradient:
                record.home_background_type = 'gradient'

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        IrConfigParameter = self.env['ir.config_parameter'].sudo()

        # Get attachment ID for background image
        attachment_id = IrConfigParameter.get_param('home_theme.background_image_attachment_id', False)
        background_image = False

        if attachment_id:
            attachment = self.env['ir.attachment'].sudo().browse(int(attachment_id))
            if attachment.exists():
                background_image = attachment.datas

        res.update(
            home_background_image=background_image,
            home_background_color=IrConfigParameter.get_param('home_theme.background_color', '#f5f7fa'),
            home_background_type=IrConfigParameter.get_param('home_theme.background_type', 'gradient'),
        )
        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        IrConfigParameter = self.env['ir.config_parameter'].sudo()

        # Handle background image through attachment
        if self.home_background_image and self.home_background_type == 'image':
            # Create or update attachment
            attachment_id = IrConfigParameter.get_param('home_theme.background_image_attachment_id', False)

            attachment_vals = {
                'name': 'home_screen_background.png',
                'type': 'binary',
                'datas': self.home_background_image,
                'res_model': 'res.config.settings',
                'res_id': 0,
            }

            if attachment_id:
                attachment = self.env['ir.attachment'].sudo().browse(int(attachment_id))
                if attachment.exists():
                    attachment.write(attachment_vals)
                else:
                    attachment = self.env['ir.attachment'].sudo().create(attachment_vals)
                    IrConfigParameter.set_param('home_theme.background_image_attachment_id', attachment.id)
            else:
                attachment = self.env['ir.attachment'].sudo().create(attachment_vals)
                IrConfigParameter.set_param('home_theme.background_image_attachment_id', attachment.id)
        elif self.home_background_type != 'image':
            # Clear attachment if not using image
            attachment_id = IrConfigParameter.get_param('home_theme.background_image_attachment_id', False)
            if attachment_id:
                attachment = self.env['ir.attachment'].sudo().browse(int(attachment_id))
                if attachment.exists():
                    attachment.unlink()
                IrConfigParameter.set_param('home_theme.background_image_attachment_id', False)

        IrConfigParameter.set_param('home_theme.background_color', self.home_background_color or '#f5f7fa')
        IrConfigParameter.set_param('home_theme.background_type', self.home_background_type or 'gradient')
