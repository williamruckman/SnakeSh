from __future__ import annotations

import unittest

from snakesh.services.master_password_service import MasterPasswordService
from snakesh.services.settings_service import AppSettings


class MasterPasswordServiceTests(unittest.TestCase):
    def test_set_and_verify_master_password(self) -> None:
        settings = AppSettings.defaults()
        MasterPasswordService.set_master_password(settings, "top-secret")

        self.assertTrue(MasterPasswordService.has_master_password(settings))
        self.assertTrue(MasterPasswordService.verify_master_password(settings, "top-secret"))
        self.assertFalse(MasterPasswordService.verify_master_password(settings, "wrong-password"))

    def test_clear_master_password_disables_protection(self) -> None:
        settings = AppSettings.defaults()
        settings.master_password_enabled = True
        MasterPasswordService.set_master_password(settings, "top-secret")

        MasterPasswordService.clear_master_password(settings)

        self.assertFalse(settings.master_password_enabled)
        self.assertFalse(MasterPasswordService.has_master_password(settings))
        self.assertEqual(settings.master_password_salt_b64, "")
        self.assertEqual(settings.master_password_hash_b64, "")


if __name__ == "__main__":
    unittest.main()
