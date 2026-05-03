from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from snakesh.ui import help_dialog as help_dialog_module
from snakesh.ui.help_dialog import HelpDialog

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SOURCE_HELP_DIR = _REPO_ROOT / "src" / "snakesh" / "assets" / "help"
_BUNDLED_HELP_DIRS = (
    _REPO_ROOT / "build" / "lib" / "snakesh" / "assets" / "help",
    _REPO_ROOT / "dist" / "SnakeSh" / "_internal" / "snakesh" / "assets" / "help",
    _REPO_ROOT / "build" / "AppDir" / "usr" / "lib" / "snakesh" / "_internal" / "snakesh" / "assets" / "help",
)


class HelpDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        help_dialog_module._HELP_MANIFEST_CACHE.clear()

    def tearDown(self) -> None:
        help_dialog_module._HELP_MANIFEST_CACHE.clear()

    def test_dialog_loads_bundled_manifest(self) -> None:
        dialog = HelpDialog()
        try:
            QApplication.processEvents()
            self.assertGreaterEqual(dialog.index_list.count(), 10)
            titles = [dialog.index_list.item(index).text() for index in range(dialog.index_list.count())]
            self.assertIn("Tool: ASN Lookup", titles)
            self.assertIn("Tool: Diff Tool", titles)
            self.assertIn("Tool: IP Scan", titles)
            self.assertIn("Tool: MTU / MSS Calculator", titles)
            self.assertIn("Tool: Resource Monitor", titles)
            self.assertIn("Tool: Syslog / SNMP Monitor", titles)
            diff_index = titles.index("Tool: Diff Tool")
            dialog.index_list.setCurrentRow(diff_index)
            QApplication.processEvents()
            self.assertTrue(dialog.browser.openExternalLinks())
            self.assertIn("background: #f7f9fc", dialog.browser.styleSheet())
            self.assertIn("body", dialog.browser.document().defaultStyleSheet())
            self.assertIn("Tool: Diff Tool", dialog.browser.toPlainText())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_syslog_snmp_monitor_help_mentions_profiles_and_retention(self) -> None:
        dialog = HelpDialog()
        try:
            QApplication.processEvents()
            titles = [dialog.index_list.item(index).text() for index in range(dialog.index_list.count())]
            page_index = titles.index("Tool: Syslog / SNMP Monitor")
            dialog.index_list.setCurrentRow(page_index)
            QApplication.processEvents()

            page_text = dialog.browser.toPlainText()
            self.assertIn("Profiles", page_text)
            self.assertIn("Retention", page_text)
            self.assertIn("Timezone", page_text)
            self.assertIn("Clear Database", page_text)
            self.assertIn("SNMP", page_text)
            self.assertIn("syslog", page_text.lower())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_resource_monitor_help_mentions_gpu_and_authorization(self) -> None:
        dialog = HelpDialog()
        try:
            QApplication.processEvents()
            titles = [dialog.index_list.item(index).text() for index in range(dialog.index_list.count())]
            page_index = titles.index("Tool: Resource Monitor")
            dialog.index_list.setCurrentRow(page_index)
            QApplication.processEvents()

            page_text = dialog.browser.toPlainText()
            self.assertIn("GPU", page_text)
            self.assertIn("Processes", page_text)
            self.assertIn("authorization", page_text.lower())
            self.assertIn("End Task", page_text)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_traceroute_help_mentions_graphs_and_avg(self) -> None:
        dialog = HelpDialog()
        try:
            QApplication.processEvents()
            titles = [dialog.index_list.item(index).text() for index in range(dialog.index_list.count())]
            traceroute_index = titles.index("Tool: Traceroute")
            dialog.index_list.setCurrentRow(traceroute_index)
            QApplication.processEvents()

            traceroute_text = dialog.browser.toPlainText()
            self.assertIn("Graph", traceroute_text)
            self.assertIn("Avg", traceroute_text)
            self.assertIn("Export", traceroute_text)
            self.assertIn("Auto", traceroute_text)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_terminal_workflows_help_mentions_searchable_scrollback(self) -> None:
        dialog = HelpDialog()
        try:
            QApplication.processEvents()
            titles = [dialog.index_list.item(index).text() for index in range(dialog.index_list.count())]
            page_index = titles.index("Terminal Workflows")
            dialog.index_list.setCurrentRow(page_index)
            QApplication.processEvents()

            page_text = dialog.browser.toPlainText()
            self.assertIn("Scrollback", page_text)
            self.assertIn("Ctrl+F", page_text)
            self.assertIn("Regex", page_text)
            self.assertIn("live", page_text.lower())
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_command_line_help_page_mentions_debug_flags_and_tool_keys(self) -> None:
        dialog = HelpDialog()
        try:
            QApplication.processEvents()
            titles = [dialog.index_list.item(index).text() for index in range(dialog.index_list.count())]
            page_index = titles.index("Command Line and Launchers")
            dialog.index_list.setCurrentRow(page_index)
            QApplication.processEvents()

            page_text = dialog.browser.toPlainText()
            self.assertIn("--install-desktop", page_text)
            self.assertIn("--debug-level", page_text)
            self.assertIn("--web-server-helper", page_text)
            self.assertIn("resource_monitor", page_text)
            self.assertIn("syslog_snmp_monitor", page_text)
            self.assertIn("securepython", page_text)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_workspace_help_mentions_scrollback_button(self) -> None:
        dialog = HelpDialog()
        try:
            QApplication.processEvents()
            titles = [dialog.index_list.item(index).text() for index in range(dialog.index_list.count())]
            page_index = titles.index("Workspace and Tabs")
            dialog.index_list.setCurrentRow(page_index)
            QApplication.processEvents()

            page_text = dialog.browser.toPlainText()
            self.assertIn("Scrollback", page_text)
            self.assertIn("bottom command bar", page_text)
        finally:
            dialog.deleteLater()
            QApplication.processEvents()

    def test_missing_manifest_is_handled_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp)
            with patch("snakesh.ui.help_dialog.runtime.asset_path", return_value=fake_root / "missing.json"):
                dialog = HelpDialog()
                try:
                    self.assertFalse(dialog.index_list.isEnabled())
                    self.assertIn("could not be loaded", dialog.browser.toPlainText().lower())
                finally:
                    dialog.deleteLater()
                    QApplication.processEvents()

    def test_manifest_is_cached_across_dialog_instances(self) -> None:
        original_read_text = Path.read_text
        manifest_reads = 0

        def _tracked_read_text(path_self: Path, *args, **kwargs):
            nonlocal manifest_reads
            if path_self.name == "manifest.json":
                manifest_reads += 1
            return original_read_text(path_self, *args, **kwargs)

        with patch("pathlib.Path.read_text", autospec=True, side_effect=_tracked_read_text):
            first = HelpDialog()
            second = HelpDialog()
            try:
                QApplication.processEvents()
            finally:
                first.deleteLater()
                second.deleteLater()
                QApplication.processEvents()

        self.assertEqual(manifest_reads, 1)

    def test_bundled_help_trees_match_source_help(self) -> None:
        source_files = {
            path.relative_to(_SOURCE_HELP_DIR)
            for path in _SOURCE_HELP_DIR.rglob("*")
            if path.is_file()
        }
        self.assertGreater(len(source_files), 0)

        checked_roots = 0
        for bundled_dir in _BUNDLED_HELP_DIRS:
            if not bundled_dir.exists():
                continue
            checked_roots += 1
            bundled_files = {
                path.relative_to(bundled_dir)
                for path in bundled_dir.rglob("*")
                if path.is_file()
            }
            with self.subTest(bundled_dir=str(bundled_dir)):
                self.assertEqual(bundled_files, source_files)
            for relative_path in sorted(source_files):
                with self.subTest(bundled_dir=str(bundled_dir), file=str(relative_path)):
                    self.assertEqual(
                        (bundled_dir / relative_path).read_bytes(),
                        (_SOURCE_HELP_DIR / relative_path).read_bytes(),
                    )

        if checked_roots == 0:
            self.skipTest("No built help bundle exists in this test run.")


if __name__ == "__main__":
    unittest.main()
