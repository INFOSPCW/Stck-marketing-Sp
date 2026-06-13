from odoo import models, fields


class DmsTag(models.Model):
    _name = 'dms.tag'
    _description = 'Document Tag'
    _order = 'sequence, name'

    name = fields.Char(string='Name', required=True, translate=True)
    sequence = fields.Integer(string='Sequence', default=10)
    color = fields.Integer(string='Color')
    tooltip = fields.Char(string='Tooltip', translate=True)
    document_ids = fields.Many2many(
        'dms.document', 'dms_document_tag_rel', 'tag_id', 'document_id',
        string='Documents',
    )
    document_count = fields.Integer(
        string='Document Count', compute='_compute_document_count',
    )

    def _compute_document_count(self):
        for tag in self:
            tag.document_count = len(tag.document_ids)
