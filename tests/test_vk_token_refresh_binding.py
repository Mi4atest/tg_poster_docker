"""Refresh сохраняет owner binding и алертит на fatal ошибки."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class VkTokenRefreshBindingTests(unittest.TestCase):
    def test_refresh_preserves_bound_user(self):
        from app.utils import vk_token_refresh as mod

        service = MagicMock()
        service.get_secret.side_effect = lambda name: {
            "vk_refresh_token": "old-refresh",
            "vk_app_secret": "",
            "vk_access_token": "vk2.a.old",
        }.get(name, "")
        service.get_all.return_value = {
            "integrations": {
                "vk_token_device_id": "dev-1",
                "vk_oauth_user_id": 393964690,
                "vk_oauth_group_id": 129808251,
                "vk_oauth_admin_level": 3,
            }
        }
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "access_token": "vk2.a.new",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "user_id": 393964690,
            "scope": "market wall photos stories groups offline",
        }

        with patch.object(mod, "_get_secret", side_effect=lambda n: {
            "vk_refresh_token": "old-refresh",
            "vk_access_token": "vk2.a.old",
        }.get(n, "")), patch.object(
            mod, "_device_id", return_value="dev-1"
        ), patch.object(
            mod, "vk_app_id", return_value="54604726"
        ), patch.object(
            mod, "vk_oauth_owner_id", return_value=393964690
        ), patch.object(
            mod, "_bound_user_id", return_value=393964690
        ), patch(
            "requests.post", return_value=fake_resp
        ), patch(
            "app.api.endpoints.vk_oauth._store_tokens", return_value="vk_access_token"
        ) as store, patch(
            "app.services.settings_service.get_settings_service", return_value=service
        ):
            result = mod.refresh_vk_access_token()

        self.assertTrue(result["ok"])
        self.assertEqual(result["user_id"], 393964690)
        binding = store.call_args.kwargs.get("binding_meta") or store.call_args[1].get(
            "binding_meta"
        )
        if binding is None and store.call_args.kwargs:
            binding = store.call_args.kwargs["binding_meta"]
        # positional: (data, device_id)
        self.assertEqual(store.call_args[0][1], "dev-1")
        meta = store.call_args.kwargs.get("binding_meta")
        if meta is None:
            # Python 3.12 may put kwargs only
            meta = store.call_args[1].get("binding_meta") if len(store.call_args) > 1 else None
        # Prefer kwargs
        meta = store.call_args.kwargs["binding_meta"]
        self.assertEqual(meta["user_id"], 393964690)
        self.assertEqual(meta["group_id"], 129808251)

    def test_refresh_rejects_user_mismatch(self):
        from app.utils import vk_token_refresh as mod

        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "access_token": "vk2.a.new",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
            "user_id": 1,
        }
        with patch.object(mod, "_get_secret", side_effect=lambda n: {
            "vk_refresh_token": "old-refresh",
            "vk_access_token": "vk2.a.old",
        }.get(n, "")), patch.object(
            mod, "_device_id", return_value="dev-1"
        ), patch.object(
            mod, "vk_app_id", return_value="54604726"
        ), patch.object(
            mod, "vk_oauth_owner_id", return_value=393964690
        ), patch.object(
            mod, "_bound_user_id", return_value=393964690
        ), patch(
            "requests.post", return_value=fake_resp
        ), patch.object(
            mod, "_alert_refresh_failure"
        ) as alert, patch(
            "app.services.settings_service.get_settings_service"
        ) as gs:
            gs.return_value.get_secret.return_value = ""
            result = mod.refresh_vk_access_token()

        self.assertFalse(result["ok"])
        self.assertTrue(result.get("fatal"))
        alert.assert_called()

    def test_fatal_invalid_grant_alerts(self):
        from app.utils import vk_token_refresh as mod

        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "token revoked",
        }
        with patch.object(mod, "_get_secret", side_effect=lambda n: {
            "vk_refresh_token": "old-refresh",
            "vk_access_token": "vk2.a.old",
        }.get(n, "")), patch.object(
            mod, "_device_id", return_value="dev-1"
        ), patch.object(
            mod, "vk_app_id", return_value="54604726"
        ), patch(
            "requests.post", return_value=fake_resp
        ), patch.object(
            mod, "_alert_refresh_failure"
        ) as alert, patch(
            "app.services.settings_service.get_settings_service"
        ) as gs:
            gs.return_value.get_secret.return_value = ""
            # reset cooldown
            mod._last_fatal_alert_at = 0
            result = mod.refresh_vk_access_token()

        self.assertFalse(result["ok"])
        self.assertTrue(result.get("fatal"))
        alert.assert_called()


if __name__ == "__main__":
    unittest.main()
