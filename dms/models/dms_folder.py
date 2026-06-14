from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class DmsFolder(models.Model):
    _name = 'dms.folder'
    _description = 'DMS Folder'
    _parent_name = 'parent_folder_id'
    _parent_store = True
    _rec_name = 'complete_name'
    _order = 'complete_name'

    name = fields.Char(string='Name', required=True)
    complete_name = fields.Char(
        string='Complete Name', compute='_compute_complete_name', store=True, recursive=True,
    )
    parent_folder_id = fields.Many2one(
        'dms.folder', string='Parent Folder', index=True, ondelete='cascade',
    )
    child_folder_ids = fields.One2many(
        'dms.folder', 'parent_folder_id', string='Subfolders',
    )
    parent_path = fields.Char(index=True)
    document_ids = fields.One2many(
        'dms.document', 'folder_id', string='Documents',
    )
    document_count = fields.Integer(
        string='Document Count', compute='_compute_document_count',
    )
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(string='Active', default=True)
    # Groups allowed to access this folder (empty = all internal users)
    group_ids = fields.Many2many(
        'res.groups', string='Allowed Groups',
        help='Leave empty to allow all internal users.',
    )
    description = fields.Text(string='Description')
    color = fields.Integer(string='Color')

    @api.depends('name', 'parent_folder_id.complete_name')
    def _compute_complete_name(self):
        for folder in self:
            if folder.parent_folder_id:
                folder.complete_name = f'{folder.parent_folder_id.complete_name} / {folder.name}'
            else:
                folder.complete_name = folder.name

    def _compute_document_count(self):
        doc_data = self.env['dms.document']._read_group(
            [('folder_id', 'in', self.ids)], ['folder_id'], ['__count'],
        )
        counts = {folder.id: count for folder, count in doc_data}
        for folder in self:
            folder.document_count = counts.get(folder.id, 0)

    @api.constrains('parent_folder_id')
    def _check_parent_loop(self):
        if not self._check_recursion():
            raise ValidationError(_('You cannot create recursive folders.'))

    def action_open_documents(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Documents in %s') % self.name,
            'res_model': 'dms.document',
            'view_mode': 'kanban,list,form',
            'domain': [('folder_id', '=', self.id)],
            'context': {'default_folder_id': self.id},
        }
