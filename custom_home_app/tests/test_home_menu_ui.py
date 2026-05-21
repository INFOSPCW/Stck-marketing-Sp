from odoo.tests.common import HttpCase, tagged


@tagged("-at_install", "post_install")
class TestHomeMenuUi(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._create_home_app("test_home_menu_mail_app", "Mailbox", 1)
        cls._create_home_app("test_home_menu_drag_alpha", "Drag Alpha", 2)
        cls._create_home_app("test_home_menu_drag_beta", "Drag Beta", 3)

    @classmethod
    def _create_home_app(cls, xmlid_name, label, sequence):
        action = cls.env["ir.actions.act_window"].create(
            {
                "name": label,
                "res_model": "res.users",
                "view_mode": "list,form",
            }
        )
        menu = cls.env["ir.ui.menu"].create(
            {
                "name": label,
                "parent_id": False,
                "sequence": sequence,
                "action": f"{action._name},{action.id}",
            }
        )
        cls.env["ir.model.data"].create(
            {
                "module": "custom_home_app",
                "name": xmlid_name,
                "model": "ir.ui.menu",
                "res_id": menu.id,
                "noupdate": True,
            }
        )

    def test_home_menu_search_and_drag_drop(self):
        self.start_tour(
            "/web",
            "home_desk_app_home_menu_tour",
            login="admin",
        )
