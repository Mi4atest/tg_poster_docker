"""OAuth binding: owner / group / scopes до записи токена."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class VkOauthBindingTests(unittest.TestCase):
    def test_public_redirect_uses_public_base_url(self):
        with patch("app.utils.vk_public_url.env_settings") as env:
            env.PUBLIC_BASE_URL = "https://mobi.ap43.ru"
            env.AVITO_FEED_PUBLIC_BASE_URL = "https://mobi.ap43.ru"
            from app.utils.vk_public_url import vk_oauth_redirect_uri, vk_oauth_start_url

            self.assertEqual(
                vk_oauth_redirect_uri(), "https://mobi.ap43.ru/vk/oauth/callback"
            )
            self.assertEqual(
                vk_oauth_start_url(), "https://mobi.ap43.ru/vk/oauth/vkid/start"
            )

    def test_reject_wrong_owner(self):
        from app.api.endpoints import vk_oauth as mod

        data = {
            "access_token": "vk2.a.test",
            "user_id": 111,
            "scope": "market wall photos stories groups offline",
        }
        with patch.object(mod, "vk_oauth_owner_id", return_value=393964690), patch.object(
            mod, "resolved_vk_group_id_int", return_value=129808251
        ), patch.object(mod, "check_group_admin_level", return_value=(True, 3, "ok")):
            ok, detail, meta = mod.validate_oauth_binding(data)
        self.assertFalse(ok)
        self.assertIn("не совпадает", detail)
        self.assertEqual(meta["user_id"], 111)

    def test_reject_missing_scopes(self):
        from app.api.endpoints import vk_oauth as mod

        data = {
            "access_token": "vk2.a.test",
            "user_id": 393964690,
            "scope": "wall groups",
        }
        with patch.object(mod, "vk_oauth_owner_id", return_value=393964690), patch.object(
            mod, "resolved_vk_group_id_int", return_value=129808251
        ):
            ok, detail, meta = mod.validate_oauth_binding(data)
        self.assertFalse(ok)
        self.assertIn("market", detail.lower() + " ".join(meta.get("missing_scopes") or []))

    def test_reject_wrong_group_role(self):
        from app.api.endpoints import vk_oauth as mod

        data = {
            "access_token": "vk2.a.test",
            "user_id": 393964690,
            "scope": "market wall photos stories groups offline",
        }
        with patch.object(mod, "vk_oauth_owner_id", return_value=393964690), patch.object(
            mod, "resolved_vk_group_id_int", return_value=129808251
        ), patch.object(
            mod,
            "check_group_admin_level",
            return_value=(False, 0, "нужен editor/admin в группе 129808251, admin_level=0"),
        ):
            ok, detail, meta = mod.validate_oauth_binding(data)
        self.assertFalse(ok)
        self.assertIn("editor/admin", detail)
        self.assertEqual(meta["admin_level"], 0)

    def test_accept_valid_binding_and_store_meta(self):
        from app.api.endpoints import vk_oauth as mod

        data = {
            "access_token": "vk2.a.test",
            "refresh_token": "vk2.a.refresh",
            "user_id": 393964690,
            "expires_in": 3600,
            "scope": "market wall photos stories groups offline",
        }
        service = MagicMock()
        with patch.object(mod, "vk_oauth_owner_id", return_value=393964690), patch.object(
            mod, "resolved_vk_group_id_int", return_value=129808251
        ), patch.object(
            mod, "check_group_admin_level", return_value=(True, 3, "ok")
        ), patch(
            "app.services.settings_service.get_settings_service", return_value=service
        ), patch(
            "app.services.price_sync_service.reset_vk_publisher"
        ), patch(
            "app.utils.vk_flood_gate.clear_flood"
        ):
            ok, detail, meta = mod.validate_oauth_binding(data)
            self.assertTrue(ok, detail)
            saved = mod._store_tokens(data, "device-xyz", binding_meta=meta)
        self.assertIn("vk_access_token", saved)
        self.assertIn("vk_refresh_token", saved)
        service.set_secret.assert_any_call("vk_access_token", "vk2.a.test")
        service.set_secret.assert_any_call("vk_refresh_token", "vk2.a.refresh")
        update_arg = service.update.call_args[0][0]
        self.assertEqual(update_arg["integrations"]["vk_oauth_user_id"], 393964690)
        self.assertEqual(update_arg["integrations"]["vk_oauth_group_id"], 129808251)
        self.assertEqual(update_arg["integrations"]["vk_token_device_id"], "device-xyz")


if __name__ == "__main__":
    unittest.main()
