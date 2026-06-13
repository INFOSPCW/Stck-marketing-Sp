{
    'name': 'Document Management System',
    'version': '19.0.1.0.0',
    'summary': 'Organize, upload and manage your documents in folders with tags and access control',
    'description': """
Document Management System (DMS)
=================================
Upload and manage documents in a hierarchical folder structure.
Features:
- Hierarchical folders
- File upload (PDF, images, Office files, etc.)
- URL document links
- Tagging system with colors
- Per-document access control (view/edit per partner)
- Favorites
- Full-text search via attachment indexation
- Activity & chatter integration
- Link documents to any Odoo record
    """,
    'category': 'Productivity/Documents',
    'author': 'Custom',
    'website': '',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'portal',
        'attachment_indexation',
    ],
    'data': [
        'security/dms_security.xml',
        'security/ir.model.access.csv',
        'data/dms_data.xml',
        'views/dms_tag_views.xml',
        'views/dms_folder_views.xml',
        'views/dms_document_views.xml',
        'wizard/dms_upload_wizard_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dms/static/src/scss/dms.scss',
        ],
    },
    'images': ['static/description/icon.png'],
    'application': True,
    'installable': True,
    'auto_install': False,
}
