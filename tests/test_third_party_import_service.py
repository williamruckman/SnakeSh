from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from snakesh.services.third_party_import_service import ThirdPartyImportService


class ThirdPartyImportServiceTests(unittest.TestCase):
    def test_import_openssh_config_imports_named_hosts_and_skips_wildcards(self) -> None:
        service = ThirdPartyImportService()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config"
            config_path.write_text(
                "\n".join(
                    [
                        "Host web",
                        "  HostName 198.51.100.5",
                        "  User admin",
                        "  Port 2222",
                        "  IdentityFile ~/.ssh/id_ed25519",
                        "",
                        "Host *.example.com",
                        "  HostName wildcard-ignored",
                        "",
                        "Host db app",
                        "  HostName 192.0.2.10",
                    ]
                ),
                encoding="utf-8",
            )

            report = service.import_openssh_config(config_path)

            names = sorted(session.name for session in report.imported_sessions)
            self.assertEqual(names, ["app", "db", "web"])
            web = [session for session in report.imported_sessions if session.name == "web"][0]
            self.assertEqual(web.host, "198.51.100.5")
            self.assertEqual(web.username, "admin")
            self.assertEqual(web.port, 2222)
            self.assertIn("Imported/OpenSSH", report.folders)

    def test_import_openssh_config_resolves_include(self) -> None:
        service = ThirdPartyImportService()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            include_path = base / "extra.conf"
            include_path.write_text(
                "\n".join(
                    [
                        "Host included",
                        "  HostName 203.0.113.5",
                        "  User root",
                    ]
                ),
                encoding="utf-8",
            )
            config_path = base / "config"
            config_path.write_text(
                "\n".join(
                    [
                        f"Include {include_path.name}",
                        "Host local",
                        "  HostName 127.0.0.1",
                    ]
                ),
                encoding="utf-8",
            )

            report = service.import_openssh_config(config_path)
            names = sorted(session.name for session in report.imported_sessions)
            self.assertEqual(names, ["included", "local"])

    def test_putty_folder_and_name_parses_hierarchy(self) -> None:
        folder, name = ThirdPartyImportService._putty_folder_and_name("Team\\Prod\\RouterA")
        self.assertEqual(folder, "Imported/PuTTY/Team/Prod")
        self.assertEqual(name, "RouterA")


if __name__ == "__main__":
    unittest.main()
