from odoo import models, fields


class DmsAccess(models.Model):
    _name = 'dms.access'
    _description = 'Document Access Right'
    _order = 'partner_id'

    document_id = fields.Many2one(
        'dms.document', string='Document', required=True, ondelete='cascade', index=True,
    )
    partner_id = fields.Many2one(
        'res.partner', string='Contact', required=True, ondelete='cascade',
    )
    role = fields.Selection(
        [('view', 'View'), ('edit', 'Edit')],
        string='Access', required=True, default='view',
    )
    expiration_date = fields.Datetime(string='Expiration Date')
    last_access_date = fields.Datetime(string='Last Access', readonly=True, copy=False)

    _unique_document_partner = models.Constraint(
        'UNIQUE(document_id, partner_id)',
        'A partner can only have one access rule per document.',
    )
