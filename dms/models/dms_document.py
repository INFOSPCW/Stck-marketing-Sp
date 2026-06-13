import base64
import os

from odoo import models, fields, api, _
from odoo.exceptions import AccessError, UserError


class DmsDocument(models.Model):
    _name = 'dms.document'
    _description = 'DMS Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'write_date desc, id desc'

    # ── Core identity ────────────────────────────────────────────────────────
    name = fields.Char(string='Name', required=True, tracking=True)
    active = fields.Boolean(string='Active', default=True, tracking=True)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, tracking=True,
    )
    folder_id = fields.Many2one(
        'dms.folder', string='Folder', required=True,
        ondelete='restrict', tracking=True, index=True,
    )
    description = fields.Text(string='Description')
    sequence = fields.Integer(string='Sequence', default=10)

    # ── Document type ─────────────────────────────────────────────────────────
    type = fields.Selection(
        [('binary', 'File'), ('url', 'URL')],
        string='Type', required=True, default='binary', tracking=True,
    )

    # ── File / attachment ─────────────────────────────────────────────────────
    attachment_id = fields.Many2one(
        'ir.attachment', string='Attachment', ondelete='cascade', copy=False,
    )
    datas = fields.Binary(
        string='File Content', related='attachment_id.datas',
        readonly=False, store=False,
    )
    file_name = fields.Char(
        string='File Name', related='attachment_id.name',
        readonly=False, store=False,
    )
    file_size = fields.Integer(
        string='File Size (bytes)', related='attachment_id.file_size', store=True,
    )
    mimetype = fields.Char(
        string='MIME Type', related='attachment_id.mimetype', store=True,
    )
    index_content = fields.Text(
        string='Indexed Content', related='attachment_id.index_content', store=True,
    )
    checksum = fields.Char(
        related='attachment_id.checksum', store=True,
    )
    file_extension = fields.Char(
        string='Extension', compute='_compute_file_extension', store=True,
    )

    # ── URL type ──────────────────────────────────────────────────────────────
    url = fields.Char(string='URL', tracking=True)

    # ── Classification ────────────────────────────────────────────────────────
    tag_ids = fields.Many2many(
        'dms.tag', 'dms_document_tag_rel', 'document_id', 'tag_id',
        string='Tags',
    )

    # ── People ────────────────────────────────────────────────────────────────
    owner_id = fields.Many2one(
        'res.users', string='Owner',
        default=lambda self: self.env.user, tracking=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Contact', tracking=True,
    )

    # ── Favorites ─────────────────────────────────────────────────────────────
    favorited_ids = fields.Many2many(
        'res.users', 'dms_document_favorite_rel', 'document_id', 'user_id',
        string='Favorited By',
    )
    is_favorited = fields.Boolean(
        string='Is Favorited', compute='_compute_is_favorited',
        inverse='_inverse_is_favorited', search='_search_is_favorited',
    )

    # ── Locking ───────────────────────────────────────────────────────────────
    lock_uid = fields.Many2one(
        'res.users', string='Locked By', copy=False,
    )
    is_locked = fields.Boolean(
        string='Is Locked', compute='_compute_is_locked',
    )

    # ── Access control ────────────────────────────────────────────────────────
    access_ids = fields.One2many(
        'dms.access', 'document_id', string='Access Rights',
    )
    access_internal = fields.Selection(
        [('view', 'View'), ('edit', 'Edit'), ('none', 'No Access')],
        string='Internal Access', default='view', tracking=True,
        help='Default access for all internal users.',
    )
    access_via_link = fields.Selection(
        [('view', 'View'), ('edit', 'Edit'), ('none', 'No Access')],
        string='Link Access', default='none', tracking=True,
    )
    document_token = fields.Char(
        string='Access Token', copy=False,
        default=lambda self: self.env['ir.attachment']._generate_access_token(),
    )

    # ── Link to any Odoo record ───────────────────────────────────────────────
    res_model = fields.Char(string='Related Model', index=True)
    res_id = fields.Integer(string='Related Record ID', index=True)
    res_name = fields.Char(string='Related Record Name', compute='_compute_res_name')

    # ── Computed helpers ──────────────────────────────────────────────────────
    thumbnail = fields.Binary(string='Thumbnail', compute='_compute_thumbnail')
    human_size = fields.Char(string='Size', compute='_compute_human_size')

    # ── Constraints & computes ────────────────────────────────────────────────

    @api.depends('mimetype', 'file_name', 'attachment_id.name')
    def _compute_file_extension(self):
        for doc in self:
            fname = doc.file_name or doc.attachment_id.name or ''
            _, ext = os.path.splitext(fname)
            doc.file_extension = ext.lower().lstrip('.') if ext else ''

    def _compute_is_favorited(self):
        for doc in self:
            doc.is_favorited = self.env.user in doc.favorited_ids

    def _inverse_is_favorited(self):
        for doc in self:
            if doc.is_favorited:
                doc.favorited_ids = [(4, self.env.user.id)]
            else:
                doc.favorited_ids = [(3, self.env.user.id)]

    def _search_is_favorited(self, operator, value):
        if operator not in ('=', '!='):
            return []
        domain_op = 'in' if (operator == '=' and value) or (operator == '!=' and not value) else 'not in'
        favored = self.env['dms.document'].search(
            [('favorited_ids', 'in', [self.env.user.id])]
        ).ids
        return [('id', domain_op, favored)]

    def _compute_is_locked(self):
        for doc in self:
            doc.is_locked = bool(doc.lock_uid)

    def _compute_res_name(self):
        for doc in self:
            if doc.res_model and doc.res_id:
                try:
                    record = self.env[doc.res_model].browse(doc.res_id)
                    doc.res_name = record.display_name
                except Exception:
                    doc.res_name = False
            else:
                doc.res_name = False

    def _compute_thumbnail(self):
        image_mimetypes = {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml'}
        for doc in self:
            if doc.mimetype in image_mimetypes and doc.attachment_id.datas:
                doc.thumbnail = doc.attachment_id.datas
            else:
                doc.thumbnail = False

    def _compute_human_size(self):
        for doc in self:
            size = doc.file_size or 0
            if size < 1024:
                doc.human_size = f'{size} B'
            elif size < 1024 ** 2:
                doc.human_size = f'{size / 1024:.1f} KB'
            elif size < 1024 ** 3:
                doc.human_size = f'{size / 1024 ** 2:.1f} MB'
            else:
                doc.human_size = f'{size / 1024 ** 3:.1f} GB'

    # ── ORM overrides ─────────────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('type', 'binary') == 'binary' and vals.get('datas'):
                attachment = self.env['ir.attachment'].create({
                    'name': vals.get('name', 'document'),
                    'datas': vals.pop('datas', False),
                    'res_model': self._name,
                    'res_id': 0,
                })
                vals['attachment_id'] = attachment.id
        docs = super().create(vals_list)
        for doc in docs:
            if doc.attachment_id:
                doc.attachment_id.write({'res_id': doc.id})
        return docs

    def write(self, vals):
        if vals.get('datas') and not self.attachment_id:
            attachment = self.env['ir.attachment'].create({
                'name': vals.get('name', self.name),
                'datas': vals.pop('datas'),
                'res_model': self._name,
                'res_id': self.id,
            })
            vals['attachment_id'] = attachment.id
        return super().write(vals)

    def unlink(self):
        attachments = self.mapped('attachment_id')
        result = super().unlink()
        attachments.unlink()
        return result

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_lock(self):
        for doc in self:
            if doc.lock_uid:
                raise UserError(_(
                    'Document "%s" is already locked by %s.',
                    doc.name, doc.lock_uid.name,
                ))
            doc.lock_uid = self.env.user

    def action_unlock(self):
        for doc in self:
            if doc.lock_uid and doc.lock_uid != self.env.user:
                if not self.env.user.has_group('dms.group_dms_manager'):
                    raise AccessError(_(
                        'Only a DMS manager can unlock a document locked by another user.'
                    ))
            doc.lock_uid = False

    def action_toggle_favorite(self):
        for doc in self:
            doc.is_favorited = not doc.is_favorited

    def action_download(self):
        self.ensure_one()
        if self.type == 'url':
            return {'type': 'ir.actions.act_url', 'url': self.url, 'target': 'new'}
        if not self.attachment_id:
            raise UserError(_('No file attached to this document.'))
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{self.attachment_id.id}?download=true',
            'target': 'new',
        }

    def action_open_preview(self):
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(_('No file attached to this document.'))
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{self.attachment_id.id}',
            'target': 'new',
        }
