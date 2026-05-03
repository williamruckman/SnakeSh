from __future__ import annotations

import unittest

from snakesh.services.settings_service import AppSettings, SettingsService


class WebServerProfileSettingsTests(unittest.TestCase):
    def test_roundtrip_preserves_web_server_profiles_and_selection(self) -> None:
        settings = AppSettings.defaults()
        settings.web_server_profiles = [
            {
                "id": "profile-a",
                "name": "Docs Preview",
                "config": {
                    "bind_host": "127.0.0.1",
                    "port": 8001,
                    "mode": "static",
                    "document_root": "/tmp/site",
                    "index_page": "index.html",
                    "tls_mode": "none",
                    "cert_file": "",
                    "key_file": "",
                    "chain_file": "",
                    "allow_directory_listing": True,
                    "upstream_url": "",
                    "proxy_path_prefix": "/",
                    "proxy_strip_prefix": False,
                    "proxy_preserve_host": True,
                    "proxy_send_x_forwarded": True,
                    "proxy_verify_upstream_tls": True,
                    "proxy_enable_websocket": True,
                    "proxy_connect_timeout": 30,
                    "proxy_read_timeout": 60,
                    "proxy_extra_headers": "",
                    "certbot_executable": "certbot",
                    "certbot_primary_domain": "",
                    "certbot_additional_domains": "",
                    "certbot_email": "",
                    "certbot_challenge_port": 80,
                    "certbot_staging": False,
                },
            }
        ]
        settings.last_web_server_profile_id = "profile-a"

        restored = AppSettings.from_dict(settings.to_dict())

        self.assertEqual(len(restored.web_server_profiles), 1)
        self.assertEqual(restored.web_server_profiles[0]["id"], "profile-a")
        self.assertEqual(restored.web_server_profiles[0]["name"], "Docs Preview")
        self.assertEqual(restored.web_server_profiles[0]["config"]["port"], 8001)
        self.assertEqual(restored.last_web_server_profile_id, "profile-a")

    def test_sanitize_normalizes_web_server_profiles_and_clears_invalid_selection(self) -> None:
        settings = AppSettings.defaults()
        settings.web_server_profiles = [
            {
                "id": "profile-a",
                "name": "Saved Profile",
                "config": {
                    "bind_host": "   ",
                    "port": "70000",
                    "mode": "unsupported",
                    "document_root": "~/site",
                    "index_page": "  index.html  ",
                    "protocol": "ftp",
                    "cert_file": "~/server.crt",
                    "key_file": "~/server.key",
                    "chain_file": "~/chain.pem",
                    "generate_self_signed": 1,
                    "allow_directory_listing": 0,
                    "upstream_url": "  https://127.0.0.1:9000  ",
                    "proxy_path_prefix": "api/",
                    "proxy_strip_prefix": 1,
                    "proxy_preserve_host": 0,
                    "proxy_send_x_forwarded": 1,
                    "proxy_verify_upstream_tls": 0,
                    "proxy_enable_websocket": 1,
                    "proxy_connect_timeout": "0",
                    "proxy_read_timeout": "999999",
                    "proxy_extra_headers": "X-Test: value",
                    "certbot_executable": "  ",
                    "certbot_primary_domain": " example.com ",
                    "certbot_additional_domains": [" www.example.com ", ""],
                    "certbot_email": " admin@example.com ",
                    "certbot_challenge_port": "70000",
                    "certbot_staging": 1,
                },
            },
            {
                "id": "profile-a",
                "name": "Duplicate",
                "config": {"bind_host": "127.0.0.1"},
            },
            {
                "id": "bad-profile",
                "name": "Bad",
                "config": "not-a-dict",
            },
        ]
        settings.last_web_server_profile_id = "missing-profile"

        sanitized = SettingsService._sanitize(settings)

        self.assertEqual(len(sanitized.web_server_profiles), 1)
        profile = sanitized.web_server_profiles[0]
        config = profile["config"]
        self.assertEqual(profile["id"], "profile-a")
        self.assertEqual(profile["name"], "Saved Profile")
        self.assertEqual(config["bind_host"], "127.0.0.1")
        self.assertEqual(config["port"], 65535)
        self.assertEqual(config["mode"], "static")
        self.assertEqual(config["index_page"], "index.html")
        self.assertEqual(config["tls_mode"], "self_signed")
        self.assertEqual(config["protocol"], "https")
        self.assertNotIn("~", config["document_root"])
        self.assertNotIn("~", config["cert_file"])
        self.assertNotIn("~", config["key_file"])
        self.assertNotIn("~", config["chain_file"])
        self.assertTrue(config["generate_self_signed"])
        self.assertFalse(config["allow_directory_listing"])
        self.assertEqual(config["proxy_path_prefix"], "/api")
        self.assertEqual(config["proxy_connect_timeout"], 1)
        self.assertEqual(config["proxy_read_timeout"], 3600)
        self.assertEqual(config["certbot_executable"], "certbot")
        self.assertEqual(config["certbot_additional_domains"], "www.example.com")
        self.assertEqual(config["certbot_challenge_port"], 65535)
        self.assertEqual(sanitized.last_web_server_profile_id, "")


if __name__ == "__main__":
    unittest.main()
