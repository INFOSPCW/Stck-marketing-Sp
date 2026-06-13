from odoo import models, fields, api, _
from odoo.exceptions import UserError


class DmsUploadWizard(models.TransientModel):
    _name = 'dms.upload.wizard'
    _description = 'Upload Documents Wizard'

    folder_id = fields.Many2one(
        'dms.folder', string='Destination Folder', required=True,
        default=lambda self: self.env.context.get('default_folder_id'),
    )
    tag_ids = fields.Many2many('dms.tag', string='Tags')
    owner_id = fields.Many2one(
        'res.users', string='Owner', default=lambda self: self.env.user,
    )
    access_internal = fields.Selection(
        [('view', 'View'), ('edit', 'Edit'), ('none', 'No Access')],
        string='Internal Access', default='view',
    )
    line_ids = fields.One2many(
        'dms.upload.wizard.line', 'wizard_id', string='Files',
    )

    def action_upload(self):
        if not self.line_ids:
            raise UserError(_('Please add at least one file to upload.'))
        documents = self.env['dms.document']
        for line in self.line_ids:
            if not line.datas:
                continue
            attachment = self.env['ir.attachment'].create({
                'name': line.file_name or line.name,
                'datas': line.datas,
                'res_model': 'dms.document',
                'res_id': 0,
            })
            doc = self.env['dms.document'].create({
                'name': line.name or line.file_name,
                'type': 'binary',
                'folder_id': self.folder_id.id,
                'attachment_id': attachment.id,
                'tag_ids': [(6, 0, self.tag_ids.ids)],
                'owner_id': self.owner_id.id,
                'access_internal': self.access_internal,
            })
            attachment.write({'res_id': doc.id})
            documents |= doc
        return {
            'type': 'ir.actions.act_window',
            'name': _('Uploaded Documents'),
            'res_model': 'dms.document',
            'view_mode': 'kanban,list,form',
            'domain': [('id', 'in', documents.ids)],
        }


class DmsUploadWizardLine(models.TransientModel):
    _name = 'dms.upload.wizard.line'
    _description = 'Upload Wizard File Line'

    wizard_id = fields.Many2one('dms.upload.wizard', ondelete='cascade')
    name = fields.Char(string='Document Name')
    file_name = fields.Char(string='File Name')
    datas = fields.Binary(string='File', filename='file_name')
