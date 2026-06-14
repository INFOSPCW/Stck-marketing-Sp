import base64

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


def b64(s):
    """Return base64-encoded bytes for a plain string (test helper)."""
    return base64.b64encode(s.encode()).decode()


@tagged('post_install', '-at_install', 'dms')
class TestDms(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Users
        cls.admin = cls.env.ref('base.user_admin')
        grp_user = cls.env.ref('dms.group_dms_user')
        grp_mgr = cls.env.ref('dms.group_dms_manager')

        cls.dms_user = cls.env['res.users'].create({
            'name': 'DMS User',
            'login': 'dms_test_user',
            'email': 'dms_user@test.com',
            'group_ids': [grp_user.id],
        })
        cls.dms_manager = cls.env['res.users'].create({
            'name': 'DMS Manager',
            'login': 'dms_test_manager',
            'email': 'dms_manager@test.com',
            'group_ids': [grp_mgr.id],
        })

        # Sample file content (str, as Odoo Binary fields accept str or bytes)
        cls.file_content = b64('Hello, DMS World!')
        cls.pdf_content = b64('%PDF-1.4 fake pdf content')

    # ── Folder tests ──────────────────────────────────────────────────────────

    def test_01_create_root_folder(self):
        """Creating a root folder works and complete_name equals name."""
        folder = self.env['dms.folder'].create({'name': 'Root Folder'})
        self.assertEqual(folder.complete_name, 'Root Folder')
        self.assertTrue(folder.active)

    def test_02_create_subfolder(self):
        """Subfolder complete_name includes parent path."""
        parent = self.env['dms.folder'].create({'name': 'Parent'})
        child = self.env['dms.folder'].create({
            'name': 'Child',
            'parent_folder_id': parent.id,
        })
        self.assertEqual(child.complete_name, 'Parent / Child')

    def test_03_folder_recursive_loop_blocked(self):
        """Setting a folder as its own ancestor raises an error (ValidationError or ORM error)."""
        f1 = self.env['dms.folder'].create({'name': 'F1'})
        f2 = self.env['dms.folder'].create({'name': 'F2', 'parent_folder_id': f1.id})
        with self.assertRaises(Exception):
            f1.write({'parent_folder_id': f2.id})

    def test_04_folder_document_count(self):
        """document_count reflects the number of documents in the folder."""
        folder = self.env['dms.folder'].create({'name': 'Count Folder'})
        self.assertEqual(folder.document_count, 0)
        self.env['dms.document'].create({
            'name': 'Doc 1', 'folder_id': folder.id, 'type': 'url', 'url': 'https://odoo.com',
        })
        self.env['dms.document'].create({
            'name': 'Doc 2', 'folder_id': folder.id, 'type': 'url', 'url': 'https://odoo.com',
        })
        folder.invalidate_recordset()
        self.assertEqual(folder.document_count, 2)

    def test_05_folder_archive(self):
        """Archiving a folder sets active=False."""
        folder = self.env['dms.folder'].create({'name': 'To Archive'})
        folder.write({'active': False})
        self.assertFalse(folder.active)
        # Searching active_test=True should exclude it
        found = self.env['dms.folder'].search([('id', '=', folder.id)])
        self.assertFalse(found)

    # ── Tag tests ─────────────────────────────────────────────────────────────

    def test_06_create_tag(self):
        """Tags can be created with a name and colour."""
        tag = self.env['dms.tag'].create({'name': 'Confidential', 'color': 1})
        self.assertEqual(tag.name, 'Confidential')
        self.assertEqual(tag.color, 1)

    # ── Document creation ─────────────────────────────────────────────────────

    def test_07_create_url_document(self):
        """URL-type documents are saved with a url and no attachment."""
        folder = self.env['dms.folder'].create({'name': 'URL Folder'})
        doc = self.env['dms.document'].create({
            'name': 'Odoo Website',
            'folder_id': folder.id,
            'type': 'url',
            'url': 'https://www.odoo.com',
        })
        self.assertEqual(doc.type, 'url')
        self.assertEqual(doc.url, 'https://www.odoo.com')
        self.assertFalse(doc.attachment_id)

    def test_08_create_binary_document_with_datas(self):
        """Binary document upload creates an ir.attachment and links it."""
        folder = self.env['dms.folder'].create({'name': 'Binary Folder'})
        doc = self.env['dms.document'].create({
            'name': 'hello.txt',
            'folder_id': folder.id,
            'type': 'binary',
            'datas': self.file_content,
            'file_name': 'hello.txt',
        })
        self.assertTrue(doc.attachment_id, "Attachment must be created automatically")
        self.assertEqual(doc.attachment_id.res_model, 'dms.document')
        self.assertEqual(doc.attachment_id.res_id, doc.id)
        # Binary fields return bytes in Odoo 19; decode for comparison
        datas_val = doc.datas
        if isinstance(datas_val, bytes):
            datas_val = datas_val.decode()
        self.assertEqual(datas_val, self.file_content)
        self.assertEqual(doc.file_name, 'hello.txt')

    def test_09_replace_file_on_existing_document(self):
        """Writing datas to an existing document updates the attachment."""
        folder = self.env['dms.folder'].create({'name': 'Replace Folder'})
        doc = self.env['dms.document'].create({
            'name': 'original.txt',
            'folder_id': folder.id,
            'type': 'binary',
            'datas': self.file_content,
            'file_name': 'original.txt',
        })
        new_content = b64('Updated content!')
        doc.write({'datas': new_content})
        datas_val = doc.datas
        if isinstance(datas_val, bytes):
            datas_val = datas_val.decode()
        self.assertEqual(datas_val, new_content)

    def test_10_create_binary_document_without_file(self):
        """Binary document can be created without a file (datas set later)."""
        folder = self.env['dms.folder'].create({'name': 'Empty Folder'})
        doc = self.env['dms.document'].create({
            'name': 'pending.pdf',
            'folder_id': folder.id,
            'type': 'binary',
        })
        self.assertFalse(doc.attachment_id)
        self.assertFalse(doc.datas)

    def test_11_file_extension_computed(self):
        """file_extension is extracted from the uploaded filename."""
        folder = self.env['dms.folder'].create({'name': 'Ext Folder'})
        doc = self.env['dms.document'].create({
            'name': 'report',
            'folder_id': folder.id,
            'type': 'binary',
            'datas': self.pdf_content,
            'file_name': 'report.pdf',
        })
        self.assertEqual(doc.file_extension, 'pdf')

    def test_12_human_size_computed(self):
        """human_size is computed from file_size."""
        folder = self.env['dms.folder'].create({'name': 'Size Folder'})
        doc = self.env['dms.document'].create({
            'name': 'sized.txt',
            'folder_id': folder.id,
            'type': 'binary',
            'datas': self.file_content,
            'file_name': 'sized.txt',
        })
        self.assertTrue(doc.human_size, "human_size must not be empty")
        # Small file should be in bytes or KB
        self.assertIn(doc.human_size[-1], ['B', 'B'],
                      "Small file should be in B or KB range")

    # ── Delete / unlink ───────────────────────────────────────────────────────

    def test_13_unlink_document_removes_attachment(self):
        """Deleting a document also deletes its linked ir.attachment."""
        folder = self.env['dms.folder'].create({'name': 'Unlink Folder'})
        doc = self.env['dms.document'].create({
            'name': 'to_delete.txt',
            'folder_id': folder.id,
            'type': 'binary',
            'datas': self.file_content,
            'file_name': 'to_delete.txt',
        })
        attachment_id = doc.attachment_id.id
        self.assertTrue(attachment_id)
        doc.unlink()
        remaining = self.env['ir.attachment'].search([('id', '=', attachment_id)])
        self.assertFalse(remaining, "Attachment must be deleted with the document")

    # ── Favorite tests ────────────────────────────────────────────────────────

    def test_14_toggle_favorite(self):
        """action_toggle_favorite flips is_favorited for the current user."""
        folder = self.env['dms.folder'].create({'name': 'Fav Folder'})
        doc = self.env['dms.document'].create({
            'name': 'fav_doc', 'folder_id': folder.id, 'type': 'url',
            'url': 'https://example.com',
        })
        self.assertFalse(doc.is_favorited)
        doc.action_toggle_favorite()
        self.assertTrue(doc.is_favorited)
        doc.action_toggle_favorite()
        self.assertFalse(doc.is_favorited)

    def test_15_favorite_is_per_user(self):
        """Favoriting by one user does not affect another user's view."""
        folder = self.env['dms.folder'].create({'name': 'Per User Fav'})
        doc = self.env['dms.document'].create({
            'name': 'shared_doc', 'folder_id': folder.id, 'type': 'url',
            'url': 'https://example.com',
        })
        # Admin favorites the document
        doc.action_toggle_favorite()
        self.assertTrue(doc.is_favorited)
        # DMS user should not see it as favorited (invalidate to flush user-scoped cache)
        doc_as_user = doc.with_user(self.dms_user)
        doc_as_user.invalidate_recordset(['is_favorited'])
        self.assertFalse(doc_as_user.is_favorited)

    # ── Lock / unlock tests ───────────────────────────────────────────────────

    def test_16_lock_document(self):
        """action_lock sets lock_uid to the current user."""
        folder = self.env['dms.folder'].create({'name': 'Lock Folder'})
        doc = self.env['dms.document'].create({
            'name': 'locked_doc', 'folder_id': folder.id, 'type': 'url',
            'url': 'https://example.com',
        })
        self.assertFalse(doc.is_locked)
        doc.action_lock()
        self.assertTrue(doc.is_locked)
        self.assertEqual(doc.lock_uid, self.env.user)

    def test_17_cannot_lock_already_locked(self):
        """Locking an already locked document raises UserError."""
        folder = self.env['dms.folder'].create({'name': 'Double Lock'})
        doc = self.env['dms.document'].create({
            'name': 'dl_doc', 'folder_id': folder.id, 'type': 'url',
            'url': 'https://example.com',
        })
        doc.action_lock()
        with self.assertRaises(UserError):
            doc.action_lock()

    def test_18_unlock_own_document(self):
        """action_unlock by the locking user clears lock_uid."""
        folder = self.env['dms.folder'].create({'name': 'Unlock Folder'})
        doc = self.env['dms.document'].create({
            'name': 'unlock_doc', 'folder_id': folder.id, 'type': 'url',
            'url': 'https://example.com',
        })
        doc.action_lock()
        doc.action_unlock()
        self.assertFalse(doc.is_locked)

    def test_19_regular_user_cannot_unlock_others_lock(self):
        """A non-manager cannot unlock a document locked by another user."""
        folder = self.env['dms.folder'].create({'name': 'Other Lock'})
        doc = self.env['dms.document'].create({
            'name': 'other_lock', 'folder_id': folder.id, 'type': 'url',
            'url': 'https://example.com',
        })
        # Admin locks it
        doc.action_lock()
        # DMS user (non-manager) tries to unlock — should raise AccessError
        with self.assertRaises(AccessError):
            doc.with_user(self.dms_user).action_unlock()

    def test_20_manager_can_unlock_others_lock(self):
        """A DMS manager can unlock a document locked by another user."""
        folder = self.env['dms.folder'].create({'name': 'Mgr Unlock'})
        doc = self.env['dms.document'].create({
            'name': 'mgr_unlock', 'folder_id': folder.id, 'type': 'url',
            'url': 'https://example.com',
        })
        doc.action_lock()
        doc.with_user(self.dms_manager).action_unlock()
        self.assertFalse(doc.is_locked)

    # ── Download / preview actions ────────────────────────────────────────────

    def test_21_download_binary_document(self):
        """action_download returns an act_url action for binary files."""
        folder = self.env['dms.folder'].create({'name': 'DL Folder'})
        doc = self.env['dms.document'].create({
            'name': 'dl.txt',
            'folder_id': folder.id,
            'type': 'binary',
            'datas': self.file_content,
            'file_name': 'dl.txt',
        })
        result = doc.action_download()
        self.assertEqual(result['type'], 'ir.actions.act_url')
        self.assertIn(str(doc.attachment_id.id), result['url'])

    def test_22_download_url_document(self):
        """action_download on a URL document redirects to the URL."""
        folder = self.env['dms.folder'].create({'name': 'URL DL Folder'})
        doc = self.env['dms.document'].create({
            'name': 'url_doc', 'folder_id': folder.id, 'type': 'url',
            'url': 'https://example.com',
        })
        result = doc.action_download()
        self.assertEqual(result['url'], 'https://example.com')

    def test_23_download_no_attachment_raises(self):
        """action_download on binary doc with no file raises UserError."""
        folder = self.env['dms.folder'].create({'name': 'No File Folder'})
        doc = self.env['dms.document'].create({
            'name': 'empty.txt', 'folder_id': folder.id, 'type': 'binary',
        })
        with self.assertRaises(UserError):
            doc.action_download()

    # ── Upload wizard tests ───────────────────────────────────────────────────

    def test_24_upload_wizard_creates_documents(self):
        """Upload wizard creates one document per line with a file."""
        folder = self.env['dms.folder'].create({'name': 'Wizard Folder'})
        wizard = self.env['dms.upload.wizard'].create({
            'folder_id': folder.id,
        })
        self.env['dms.upload.wizard.line'].create({
            'wizard_id': wizard.id,
            'name': 'wizard_file',
            'file_name': 'wizard_file.txt',
            'datas': self.file_content,
        })
        result = wizard.action_upload()
        self.assertEqual(result['type'], 'ir.actions.act_window')
        docs = self.env['dms.document'].search(result['domain'])
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs.folder_id, folder)
        self.assertTrue(docs.attachment_id)

    def test_25_upload_wizard_no_lines_raises(self):
        """Upload wizard with no lines raises UserError."""
        folder = self.env['dms.folder'].create({'name': 'Empty Wizard'})
        wizard = self.env['dms.upload.wizard'].create({'folder_id': folder.id})
        with self.assertRaises(UserError):
            wizard.action_upload()

    def test_26_upload_wizard_line_missing_file_raises(self):
        """Upload wizard line without datas raises UserError."""
        folder = self.env['dms.folder'].create({'name': 'Missing File Wizard'})
        wizard = self.env['dms.upload.wizard'].create({'folder_id': folder.id})
        self.env['dms.upload.wizard.line'].create({
            'wizard_id': wizard.id,
            'name': 'no_file',
        })
        with self.assertRaises(UserError):
            wizard.action_upload()

    def test_27_upload_wizard_inherits_tags_and_owner(self):
        """Wizard copies tags and owner to all created documents."""
        folder = self.env['dms.folder'].create({'name': 'Tagged Upload'})
        tag = self.env['dms.tag'].create({'name': 'WizardTag'})
        wizard = self.env['dms.upload.wizard'].create({
            'folder_id': folder.id,
            'tag_ids': [tag.id],
            'owner_id': self.dms_user.id,
        })
        self.env['dms.upload.wizard.line'].create({
            'wizard_id': wizard.id,
            'name': 'tagged_doc',
            'file_name': 'tagged.txt',
            'datas': self.file_content,
        })
        wizard.action_upload()
        doc = self.env['dms.document'].search([('folder_id', '=', folder.id)], limit=1)
        self.assertIn(tag, doc.tag_ids)
        self.assertEqual(doc.owner_id, self.dms_user)

    # ── Access control tests ──────────────────────────────────────────────────

    def test_28_access_record_unique_per_document_partner(self):
        """Two access records for same document + partner raises constraint."""
        folder = self.env['dms.folder'].create({'name': 'Access Folder'})
        doc = self.env['dms.document'].create({
            'name': 'access_doc', 'folder_id': folder.id,
            'type': 'url', 'url': 'https://example.com',
        })
        partner = self.env['res.partner'].create({'name': 'Test Partner'})
        self.env['dms.access'].create({
            'document_id': doc.id,
            'partner_id': partner.id,
            'role': 'view',
        })
        with self.assertRaises(Exception):
            self.env['dms.access'].create({
                'document_id': doc.id,
                'partner_id': partner.id,
                'role': 'edit',
            })

    def test_29_open_folder_action(self):
        """action_open_documents returns kanban action filtered to folder."""
        folder = self.env['dms.folder'].create({'name': 'Open Folder'})
        result = folder.action_open_documents()
        self.assertEqual(result['type'], 'ir.actions.act_window')
        self.assertEqual(result['res_model'], 'dms.document')
        self.assertIn(('folder_id', '=', folder.id), result['domain'])

    def test_30_res_name_computed(self):
        """res_name is computed correctly when a related record is set."""
        folder = self.env['dms.folder'].create({'name': 'Linked Folder'})
        partner = self.env['res.partner'].create({'name': 'Linked Partner'})
        doc = self.env['dms.document'].create({
            'name': 'linked_doc',
            'folder_id': folder.id,
            'type': 'url',
            'url': 'https://example.com',
            'res_model': 'res.partner',
            'res_id': partner.id,
        })
        self.assertEqual(doc.res_name, partner.display_name)
