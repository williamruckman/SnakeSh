from __future__ import annotations

import os
import unittest

from PySide6.QtWidgets import QApplication

from snakesh.services.settings_service import AppSettings
from snakesh.ui.password_generator_dialog import PasswordGeneratorDialog, PasswordOptions, generate_passwords


class PasswordGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def test_generate_passwords_respects_count_and_length(self) -> None:
        options = PasswordOptions(
            length=20,
            count=4,
            complexity="Strong",
            include_lower=True,
            include_upper=True,
            include_digits=True,
            include_symbols=True,
        )

        generated = generate_passwords(options)

        self.assertEqual(len(generated), 4)
        for password in generated:
            self.assertEqual(len(password), 20)

    def test_generate_passwords_contains_selected_character_groups(self) -> None:
        options = PasswordOptions(
            length=24,
            count=3,
            complexity="Maximum",
            include_lower=True,
            include_upper=True,
            include_digits=True,
            include_symbols=True,
        )

        generated = generate_passwords(options)

        for password in generated:
            self.assertTrue(any(ch.islower() for ch in password))
            self.assertTrue(any(ch.isupper() for ch in password))
            self.assertTrue(any(ch.isdigit() for ch in password))
            self.assertTrue(any(not ch.isalnum() for ch in password))

    def test_generate_passwords_rejects_too_few_groups_for_complexity(self) -> None:
        options = PasswordOptions(
            length=20,
            count=1,
            complexity="Maximum",
            include_lower=True,
            include_upper=False,
            include_digits=False,
            include_symbols=False,
        )

        with self.assertRaises(ValueError):
            generate_passwords(options)

    def test_generate_passwords_includes_required_characters(self) -> None:
        options = PasswordOptions(
            length=18,
            count=5,
            complexity="Strong",
            include_lower=True,
            include_upper=True,
            include_digits=True,
            include_symbols=False,
            include_characters="@_",
        )

        generated = generate_passwords(options)

        for password in generated:
            self.assertIn("@", password)
            self.assertIn("_", password)

    def test_generate_passwords_rejects_length_shorter_than_required_characters(self) -> None:
        options = PasswordOptions(
            length=5,
            count=1,
            complexity="Balanced",
            include_lower=True,
            include_upper=False,
            include_digits=False,
            include_symbols=False,
            include_characters="ABCDE",
        )

        with self.assertRaises(ValueError):
            generate_passwords(options)

    def test_generate_passwords_excludes_characters(self) -> None:
        options = PasswordOptions(
            length=24,
            count=8,
            complexity="Strong",
            include_lower=True,
            include_upper=True,
            include_digits=True,
            include_symbols=True,
            exclude_characters="aA0!?",
        )

        generated = generate_passwords(options)

        for password in generated:
            for excluded in "aA0!?":
                self.assertNotIn(excluded, password)

    def test_generate_passwords_rejects_conflicting_include_and_exclude_characters(self) -> None:
        options = PasswordOptions(
            length=18,
            count=2,
            complexity="Strong",
            include_lower=True,
            include_upper=True,
            include_digits=True,
            include_symbols=False,
            include_characters="Z9",
            exclude_characters="9x",
        )

        with self.assertRaises(ValueError):
            generate_passwords(options)

    def test_dialog_runtime_settings_update_output_font_only(self) -> None:
        dialog = PasswordGeneratorDialog()
        try:
            dialog.output.setPlainText("alpha\nbeta")
            before_length_font = dialog.length_input.font().pointSize()
            before_output_font = dialog.output.font().pointSize()

            settings = AppSettings.defaults()
            settings.terminal_font_pt = max(before_output_font + 4, 12)

            dialog.apply_runtime_settings(settings)
            QApplication.processEvents()

            self.assertEqual(dialog.output.toPlainText(), "alpha\nbeta")
            self.assertEqual(dialog.output.font().pointSize(), settings.terminal_font_pt)
            self.assertEqual(dialog.length_input.font().pointSize(), before_length_font)
            self.assertGreater(dialog.output.font().pointSize(), before_output_font)
        finally:
            dialog.close()
            dialog.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
