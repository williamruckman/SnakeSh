from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from snakesh.services.network_inspector import InterfaceAddress, InterfaceInfo
from snakesh.services.settings_service import AppSettings
from snakesh.services.web_server_service import WebServerConfig, WebServerStatus, write_web_server_config
from snakesh.ui.web_server_dialog import WebServerDialog


class WebServerDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls._app = QApplication.instance() or QApplication([])

    @staticmethod
    def _interface_inventory(
        *,
        primary_name: str = "eth0",
        secondary_name: str = "wifi0",
    ) -> list[InterfaceInfo]:
        return [
            InterfaceInfo(
                name=primary_name,
                is_up=True,
                mtu=1500,
                speed_mbps=1000,
                duplex="Full",
                mac_address="",
                addresses=[
                    InterfaceAddress(family="IPv4", address="192.0.2.22"),
                    InterfaceAddress(family="IPv6", address="fe80::1234"),
                ],
            ),
            InterfaceInfo(
                name="lo",
                is_up=True,
                mtu=65536,
                speed_mbps=0,
                duplex="Unknown",
                mac_address="",
                addresses=[
                    InterfaceAddress(family="IPv4", address="127.0.0.1"),
                    InterfaceAddress(family="IPv6", address="::1"),
                ],
            ),
            InterfaceInfo(
                name=secondary_name,
                is_up=False,
                mtu=1500,
                speed_mbps=0,
                duplex="Unknown",
                mac_address="",
                addresses=[
                    InterfaceAddress(family="IPv4", address="198.51.100.5"),
                    InterfaceAddress(family="IPv6", address="2001:db8::5"),
                ],
            ),
        ]

    def _create_dialog(
        self,
        *,
        instance_dir: Path | None = None,
        create_side_effect=None,
        interface_info: list[InterfaceInfo] | None = None,
        interface_error: Exception | None = None,
        **kwargs,
    ) -> WebServerDialog:
        create_patch_kwargs = {"side_effect": create_side_effect} if create_side_effect is not None else {"return_value": instance_dir}
        interface_patch_kwargs = (
            {"side_effect": interface_error}
            if interface_error is not None
            else {"return_value": interface_info if interface_info is not None else self._interface_inventory()}
        )
        with (
            patch("snakesh.ui.web_server_dialog.create_web_server_instance_dir", **create_patch_kwargs),
            patch("snakesh.ui.web_server_dialog.collect_interface_info", **interface_patch_kwargs),
        ):
            return WebServerDialog(**kwargs)

    @staticmethod
    def _bind_host_items(dialog: WebServerDialog) -> list[tuple[str, str]]:
        return [
            (
                dialog.bind_host_preset_input.itemText(index),
                str(dialog.bind_host_preset_input.itemData(index)),
            )
            for index in range(dialog.bind_host_preset_input.count())
        ]

    def _select_bind_host_preset(self, dialog: WebServerDialog, value: str) -> None:
        index = dialog.bind_host_preset_input.findData(value)
        self.assertGreaterEqual(index, 0)
        dialog.bind_host_preset_input.setCurrentIndex(index)
        QApplication.processEvents()

    def _assert_runtime_badge(self, dialog: WebServerDialog, expected_text: str, expected_color: str) -> None:
        self.assertEqual(dialog.runtime_badge.text(), expected_text)
        self.assertIn(expected_color, dialog.runtime_badge.styleSheet())

    def test_dialog_restores_static_manual_tls_profile_on_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            root = Path(tmp) / "site"
            root.mkdir()
            profiles = [
                {
                    "id": "profile-a",
                    "name": "HTTPS Static",
                    "config": {
                        "bind_host": "127.0.0.1",
                        "port": 8443,
                        "mode": "static",
                        "document_root": str(root),
                        "index_page": "index.html",
                        "tls_mode": "manual",
                        "cert_file": str(Path(tmp) / "server.crt"),
                        "key_file": str(Path(tmp) / "server.key"),
                        "chain_file": str(Path(tmp) / "chain.pem"),
                        "allow_directory_listing": True,
                    },
                }
            ]

            dialog = self._create_dialog(instance_dir=instance_dir, profiles=profiles, selected_profile_id="profile-a")
            try:
                QApplication.processEvents()
                self.assertEqual(dialog.profile_input.currentData(), "profile-a")
                self.assertEqual(dialog.bind_host_input.text(), "127.0.0.1")
                self.assertEqual(dialog.bind_host_preset_input.currentData(), "127.0.0.1")
                self.assertTrue(dialog.bind_host_custom_input.isHidden())
                self.assertEqual(dialog.port_input.value(), 8443)
                self.assertEqual(dialog.mode_input.currentData(), "static")
                self.assertEqual(dialog.document_root_input.text(), str(root))
                self.assertEqual(dialog.index_page_input.text(), "index.html")
                self.assertTrue(dialog.allow_directory_listing.isChecked())
                self.assertEqual(dialog.tls_mode_input.currentData(), "manual")
                self.assertEqual(dialog.cert_input.text(), str(Path(tmp) / "server.crt"))
                self.assertEqual(dialog.key_input.text(), str(Path(tmp) / "server.key"))
                self.assertEqual(dialog.chain_input.text(), str(Path(tmp) / "chain.pem"))
                self.assertFalse(dialog.static_group.isHidden())
                self.assertTrue(dialog.proxy_group.isHidden())
                self.assertFalse(dialog.manual_tls_widget.isHidden())
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_dialog_restores_reverse_proxy_certbot_profile_on_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            profiles = [
                {
                    "id": "profile-b",
                    "name": "Proxy 443",
                    "config": {
                        "bind_host": "0.0.0.0",
                        "port": 443,
                        "mode": "reverse_proxy",
                        "tls_mode": "certbot",
                        "upstream_url": "https://127.0.0.1:9443",
                        "proxy_path_prefix": "/api",
                        "proxy_strip_prefix": True,
                        "proxy_preserve_host": False,
                        "proxy_send_x_forwarded": True,
                        "proxy_verify_upstream_tls": False,
                        "proxy_enable_websocket": True,
                        "proxy_connect_timeout": 12,
                        "proxy_read_timeout": 90,
                        "proxy_extra_headers": "X-Test: value",
                        "certbot_executable": "certbot",
                        "certbot_primary_domain": "example.com",
                        "certbot_additional_domains": "www.example.com",
                        "certbot_email": "admin@example.com",
                        "certbot_challenge_port": 80,
                        "certbot_staging": True,
                    },
                }
            ]

            dialog = self._create_dialog(instance_dir=instance_dir, profiles=profiles, selected_profile_id="profile-b")
            try:
                QApplication.processEvents()
                self.assertEqual(dialog.bind_host_input.text(), "0.0.0.0")
                self.assertEqual(dialog.bind_host_preset_input.currentData(), "0.0.0.0")
                self.assertTrue(dialog.bind_host_custom_input.isHidden())
                self.assertEqual(dialog.mode_input.currentData(), "reverse_proxy")
                self.assertEqual(dialog.tls_mode_input.currentData(), "certbot")
                self.assertEqual(dialog.port_input.value(), 443)
                self.assertEqual(dialog.upstream_url_input.text(), "https://127.0.0.1:9443")
                self.assertEqual(dialog.proxy_path_prefix_input.text(), "/api")
                self.assertTrue(dialog.proxy_strip_prefix.isChecked())
                self.assertFalse(dialog.proxy_preserve_host.isChecked())
                self.assertFalse(dialog.proxy_verify_upstream_tls.isChecked())
                self.assertEqual(dialog.proxy_connect_timeout.value(), 12)
                self.assertEqual(dialog.proxy_read_timeout.value(), 90)
                self.assertEqual(dialog.proxy_extra_headers.toPlainText(), "X-Test: value")
                self.assertEqual(dialog.certbot_primary_domain_input.text(), "example.com")
                self.assertEqual(dialog.certbot_additional_domains_input.text(), "www.example.com")
                self.assertEqual(dialog.certbot_email_input.text(), "admin@example.com")
                self.assertEqual(dialog.certbot_challenge_port.value(), 80)
                self.assertTrue(dialog.certbot_staging.isChecked())
                self.assertTrue(dialog.static_group.isHidden())
                self.assertFalse(dialog.proxy_group.isHidden())
                self.assertFalse(dialog.certbot_widget.isHidden())
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_mode_switch_updates_visible_sections_and_recommended_ports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            dialog = self._create_dialog(instance_dir=instance_dir)
            try:
                QApplication.processEvents()
                self.assertEqual(dialog.port_input.value(), 8000)
                dialog.mode_input.setCurrentIndex(1)
                QApplication.processEvents()
                self.assertEqual(dialog.port_input.value(), 80)
                self.assertFalse(dialog.proxy_group.isHidden())
                dialog.tls_mode_input.setCurrentIndex(1)
                QApplication.processEvents()
                self.assertEqual(dialog.port_input.value(), 443)
                dialog.port_input.setValue(8080)
                dialog.tls_mode_input.setCurrentIndex(0)
                QApplication.processEvents()
                self.assertEqual(dialog.port_input.value(), 8080)
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_apply_runtime_settings_updates_log_output_font_without_restyling_form_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            dialog = self._create_dialog(instance_dir=instance_dir)
            try:
                before_port_font = dialog.port_input.font().pointSize()
                before_log_font = dialog.log_output.font().pointSize()

                settings = AppSettings.defaults()
                settings.terminal_font_pt = max(before_log_font + 4, 12)

                dialog.apply_runtime_settings(settings)
                QApplication.processEvents()

                self.assertEqual(dialog.log_output.font().pointSize(), settings.terminal_font_pt)
                self.assertEqual(dialog.port_input.font().pointSize(), before_port_font)
                self.assertGreater(dialog.log_output.font().pointSize(), before_log_font)
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_profile_actions_save_load_update_rename_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            persisted: list[tuple[list[dict[str, object]], str]] = []

            def on_profiles_changed(profiles: list[dict[str, object]], selected_profile_id: str) -> None:
                copied = [
                    {
                        "id": str(profile["id"]),
                        "name": str(profile["name"]),
                        "config": dict(profile["config"]),
                    }
                    for profile in profiles
                ]
                persisted.append((copied, selected_profile_id))

            dialog = self._create_dialog(instance_dir=instance_dir, on_profiles_changed=on_profiles_changed)
            try:
                self._select_bind_host_preset(dialog, "0.0.0.0")
                dialog.mode_input.setCurrentIndex(1)
                dialog.tls_mode_input.setCurrentIndex(3)
                dialog.upstream_url_input.setText("http://127.0.0.1:9000")
                dialog.proxy_path_prefix_input.setText("/api")
                dialog.proxy_strip_prefix.setChecked(True)
                dialog.certbot_primary_domain_input.setText("example.com")
                dialog.certbot_email_input.setText("admin@example.com")

                with patch("snakesh.ui.web_server_dialog.QInputDialog.getText", return_value=("Lab Proxy", True)):
                    dialog._save_current_profile_as()
                QApplication.processEvents()

                self.assertEqual(dialog.profile_input.count(), 2)
                saved_profiles, saved_selected = persisted[-1]
                saved_id = str(saved_profiles[0]["id"])
                self.assertEqual(saved_selected, saved_id)
                self.assertEqual(saved_profiles[0]["name"], "Lab Proxy")
                self.assertEqual(saved_profiles[0]["config"]["mode"], "reverse_proxy")
                self.assertEqual(saved_profiles[0]["config"]["tls_mode"], "certbot")
                self.assertEqual(saved_profiles[0]["config"]["upstream_url"], "http://127.0.0.1:9000")

                dialog.mode_input.setCurrentIndex(0)
                dialog.tls_mode_input.setCurrentIndex(0)
                dialog._load_selected_profile()
                self.assertEqual(dialog.mode_input.currentData(), "reverse_proxy")
                self.assertEqual(dialog.tls_mode_input.currentData(), "certbot")
                self.assertEqual(dialog.upstream_url_input.text(), "http://127.0.0.1:9000")

                dialog.proxy_read_timeout.setValue(120)
                dialog._update_selected_profile()
                updated_profiles, updated_selected = persisted[-1]
                self.assertEqual(updated_selected, saved_id)
                self.assertEqual(updated_profiles[0]["config"]["proxy_read_timeout"], 120)

                with patch("snakesh.ui.web_server_dialog.QInputDialog.getText", return_value=("Production Proxy", True)):
                    dialog._rename_selected_profile()
                renamed_profiles, renamed_selected = persisted[-1]
                self.assertEqual(renamed_selected, saved_id)
                self.assertEqual(renamed_profiles[0]["name"], "Production Proxy")
                self.assertEqual(dialog.profile_input.currentText(), "Production Proxy")

                with patch("snakesh.ui.web_server_dialog.QMessageBox.question", return_value=QMessageBox.Yes):
                    dialog._delete_selected_profile()
                deleted_profiles, deleted_selected = persisted[-1]
                self.assertEqual(deleted_profiles, [])
                self.assertEqual(deleted_selected, "")
                self.assertEqual(dialog.profile_input.count(), 1)
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_splitter_state_is_persisted_and_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            saved_states: list[str] = []

            def on_splitter_state_changed(encoded: str) -> None:
                saved_states.append(encoded)

            dialog = self._create_dialog(instance_dir=instance_dir, on_splitter_state_changed=on_splitter_state_changed)
            try:
                QApplication.processEvents()
                dialog._main_splitter.setSizes([520, 260])
                dialog._persist_splitter_state()
                self.assertTrue(saved_states)
                encoded = saved_states[-1]
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

            restored_instance_dir = Path(tmp) / "instance-restored"
            restored_instance_dir.mkdir()
            restored = self._create_dialog(instance_dir=restored_instance_dir, splitter_state_b64=encoded)
            try:
                QApplication.processEvents()
                self.assertEqual(
                    bytes(restored._main_splitter.saveState().toBase64()).decode("ascii"),
                    encoded,
                )
            finally:
                restored._poll_timer.stop()
                restored.deleteLater()
                QApplication.processEvents()

    def test_close_requests_stop_when_instance_has_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            write_web_server_config(
                instance_dir,
                WebServerConfig(
                    bind_host="127.0.0.1",
                    port=8443,
                    mode="reverse_proxy",
                    upstream_url="http://127.0.0.1:9000",
                    tls_mode="self_signed",
                ),
            )

            dialog = self._create_dialog(instance_dir=instance_dir)
            try:
                with patch("snakesh.ui.web_server_dialog.request_web_server_stop") as mock_stop:
                    dialog.close()
                    QApplication.processEvents()
                mock_stop.assert_called_once_with(instance_dir)
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_runtime_badge_reflects_polled_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            dialog = self._create_dialog(instance_dir=instance_dir)
            try:
                QApplication.processEvents()
                self._assert_runtime_badge(dialog, "Stopped", "#b91c1c")

                cases = [
                    (
                        WebServerStatus(state="starting", message="Launching web server helper..."),
                        False,
                        "Starting",
                        "#92400e",
                    ),
                    (
                        WebServerStatus(
                            state="running",
                            message="Serving requests.",
                            pid=1234,
                            url="http://127.0.0.1:8080/",
                            bind_host="127.0.0.1",
                            port=8080,
                        ),
                        True,
                        "Running",
                        "#166534",
                    ),
                    (
                        WebServerStatus(state="error", message="Bind failed."),
                        False,
                        "Error",
                        "#b91c1c",
                    ),
                ]

                for status, is_running, expected_text, expected_color in cases:
                    with (
                        self.subTest(state=status.state),
                        patch("snakesh.ui.web_server_dialog.read_web_server_status", return_value=status),
                        patch("snakesh.ui.web_server_dialog.is_web_server_running", return_value=is_running),
                    ):
                        dialog._poll_runtime_state()
                        QApplication.processEvents()
                        self._assert_runtime_badge(dialog, expected_text, expected_color)
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_stop_requested_runtime_badge_overrides_stale_running_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            dialog = self._create_dialog(instance_dir=instance_dir)
            try:
                dialog._launch_requested = True
                status = WebServerStatus(
                    state="running",
                    message="Serving requests.",
                    pid=1234,
                    url="http://127.0.0.1:8080/",
                    bind_host="127.0.0.1",
                    port=8080,
                )

                def _request_stop(_instance_dir: Path) -> None:
                    dialog._paths.stop_path.write_text("stop\n", encoding="utf-8")

                with (
                    patch("snakesh.ui.web_server_dialog.read_web_server_status", return_value=status),
                    patch("snakesh.ui.web_server_dialog.is_web_server_running", return_value=True),
                    patch("snakesh.ui.web_server_dialog.request_web_server_stop", side_effect=_request_stop),
                ):
                    dialog._stop_server()
                    QApplication.processEvents()

                self._assert_runtime_badge(dialog, "Stopping", "#92400e")
                self.assertEqual(dialog.status_label.text(), "Stop requested. Waiting for the web server to exit.")
                self.assertNotEqual(dialog.status_label.text(), status.message)

                dialog._paths.stop_path.unlink()
                stopped_status = WebServerStatus(state="stopped", message="Server stopped.")
                with (
                    patch("snakesh.ui.web_server_dialog.read_web_server_status", return_value=stopped_status),
                    patch("snakesh.ui.web_server_dialog.is_web_server_running", return_value=False),
                ):
                    dialog._poll_runtime_state()
                    QApplication.processEvents()

                self._assert_runtime_badge(dialog, "Stopped", "#b91c1c")
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_unavailable_status_keeps_stop_available_when_listener_responds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            write_web_server_config(
                instance_dir,
                WebServerConfig(
                    bind_host="127.0.0.1",
                    port=8443,
                    mode="reverse_proxy",
                    upstream_url="http://127.0.0.1:9000",
                    tls_mode="self_signed",
                ),
            )
            (instance_dir / "status.json").write_text("{invalid", encoding="utf-8")

            dialog = self._create_dialog(instance_dir=instance_dir)
            try:
                dialog._launch_requested = True
                with patch.object(dialog, "_listener_looks_active", return_value=True):
                    dialog._poll_runtime_state()
                self.assertFalse(dialog.start_btn.isEnabled())
                self.assertTrue(dialog.stop_btn.isEnabled())
                self.assertIn("status is unavailable", dialog.status_label.text().lower())
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_loading_different_profile_while_active_warns_stops_and_replaces_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_a = Path(tmp) / "instance-a"
            instance_b = Path(tmp) / "instance-b"
            instance_a.mkdir()
            instance_b.mkdir()
            static_root = Path(tmp) / "static-root"
            static_root.mkdir()
            profiles = [
                {
                    "id": "profile-a",
                    "name": "Static",
                    "config": {
                        "bind_host": "127.0.0.1",
                        "port": 8000,
                        "mode": "static",
                        "document_root": str(static_root),
                        "tls_mode": "none",
                    },
                },
                {
                    "id": "profile-b",
                    "name": "Proxy",
                    "config": {
                        "bind_host": "0.0.0.0",
                        "port": 8081,
                        "mode": "reverse_proxy",
                        "upstream_url": "http://127.0.0.1:9000",
                        "tls_mode": "none",
                    },
                },
            ]

            dialog = self._create_dialog(
                create_side_effect=[instance_a, instance_b],
                profiles=profiles,
                selected_profile_id="profile-a",
            )
            try:
                dialog._launch_requested = True
                dialog.profile_input.setCurrentIndex(2)
                QApplication.processEvents()

                with (
                    patch.object(dialog, "_runtime_state", return_value=SimpleNamespace(can_request_stop=True)),
                    patch("snakesh.ui.web_server_dialog.create_web_server_instance_dir", return_value=instance_b),
                    patch("snakesh.ui.web_server_dialog.QMessageBox.question", return_value=QMessageBox.Yes),
                    patch("snakesh.ui.web_server_dialog.request_web_server_stop") as mock_stop,
                ):
                    dialog._load_selected_profile()

                mock_stop.assert_called_once_with(instance_a)
                self.assertEqual(dialog._instance_dir, instance_b)
                self.assertEqual(dialog.mode_input.currentData(), "reverse_proxy")
                self.assertEqual(dialog.upstream_url_input.text(), "http://127.0.0.1:9000")
                self.assertIn("Stopped the current web server and loaded web server profile Proxy.", dialog.status_label.text())
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_loading_different_profile_while_active_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_a = Path(tmp) / "instance-a"
            instance_b = Path(tmp) / "instance-b"
            instance_a.mkdir()
            instance_b.mkdir()
            static_root = Path(tmp) / "static-root"
            static_root.mkdir()
            profiles = [
                {
                    "id": "profile-a",
                    "name": "Static",
                    "config": {
                        "bind_host": "127.0.0.1",
                        "port": 8000,
                        "mode": "static",
                        "document_root": str(static_root),
                        "tls_mode": "none",
                    },
                },
                {
                    "id": "profile-b",
                    "name": "Proxy",
                    "config": {
                        "bind_host": "0.0.0.0",
                        "port": 8081,
                        "mode": "reverse_proxy",
                        "upstream_url": "http://127.0.0.1:9000",
                        "tls_mode": "none",
                    },
                },
            ]

            dialog = self._create_dialog(
                create_side_effect=[instance_a, instance_b],
                profiles=profiles,
                selected_profile_id="profile-a",
            )
            try:
                dialog._launch_requested = True
                dialog.profile_input.setCurrentIndex(2)
                QApplication.processEvents()

                with (
                    patch.object(dialog, "_runtime_state", return_value=SimpleNamespace(can_request_stop=True)),
                    patch("snakesh.ui.web_server_dialog.QMessageBox.question", return_value=QMessageBox.No),
                    patch("snakesh.ui.web_server_dialog.request_web_server_stop") as mock_stop,
                ):
                    dialog._load_selected_profile()

                mock_stop.assert_not_called()
                self.assertEqual(dialog._instance_dir, instance_a)
                self.assertEqual(dialog.mode_input.currentData(), "static")
                self.assertEqual(dialog.document_root_input.text(), str(static_root))
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_loading_same_effective_profile_while_active_does_not_stop_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_a = Path(tmp) / "instance-a"
            instance_a.mkdir()
            static_root = Path(tmp) / "static-root"
            static_root.mkdir()
            shared_config = {
                "bind_host": "127.0.0.1",
                "port": 8000,
                "mode": "static",
                "document_root": str(static_root),
                "tls_mode": "none",
            }
            profiles = [
                {"id": "profile-a", "name": "Static A", "config": shared_config},
                {"id": "profile-b", "name": "Static B", "config": dict(shared_config)},
            ]

            dialog = self._create_dialog(instance_dir=instance_a, profiles=profiles, selected_profile_id="profile-a")
            try:
                dialog._launch_requested = True
                dialog.profile_input.setCurrentIndex(2)
                QApplication.processEvents()

                with (
                    patch.object(dialog, "_runtime_state", return_value=SimpleNamespace(can_request_stop=True)),
                    patch("snakesh.ui.web_server_dialog.QMessageBox.question") as mock_question,
                    patch("snakesh.ui.web_server_dialog.request_web_server_stop") as mock_stop,
                ):
                    dialog._load_selected_profile()

                mock_question.assert_not_called()
                mock_stop.assert_not_called()
                self.assertEqual(dialog._instance_dir, instance_a)
                self.assertEqual(dialog.mode_input.currentData(), "static")
                self.assertEqual(dialog.document_root_input.text(), str(static_root))
                self.assertEqual(dialog.status_label.text(), "Loaded web server profile Static B.")
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_bind_address_preset_list_includes_fixed_discovered_and_custom_choices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            dialog = self._create_dialog(instance_dir=instance_dir)
            try:
                QApplication.processEvents()
                self.assertEqual(
                    self._bind_host_items(dialog),
                    [
                        ("All IPv4 interfaces (0.0.0.0)", "0.0.0.0"),
                        ("All IPv6 interfaces (::)", "::"),
                        ("Loopback IPv4 (127.0.0.1)", "127.0.0.1"),
                        ("Loopback IPv6 (::1)", "::1"),
                        ("eth0 - 192.0.2.22", "192.0.2.22"),
                        ("eth0 - fe80::1234", "fe80::1234"),
                        ("wifi0 - 198.51.100.5 (down)", "198.51.100.5"),
                        ("wifi0 - 2001:db8::5 (down)", "2001:db8::5"),
                        ("Custom...", "__custom_bind_host__"),
                    ],
                )
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_bind_address_unknown_profile_value_uses_custom_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            profiles = [
                {
                    "id": "profile-a",
                    "name": "Custom Bind",
                    "config": {
                        "bind_host": "198.51.100.42",
                        "port": 8000,
                        "mode": "static",
                        "document_root": str(Path(tmp)),
                        "tls_mode": "none",
                    },
                }
            ]

            dialog = self._create_dialog(instance_dir=instance_dir, profiles=profiles, selected_profile_id="profile-a")
            try:
                QApplication.processEvents()
                self.assertEqual(dialog.bind_host_input.text(), "198.51.100.42")
                self.assertEqual(dialog.bind_host_preset_input.currentText(), "Custom...")
                self.assertFalse(dialog.bind_host_custom_input.isHidden())
                self.assertEqual(dialog.bind_host_custom_input.text(), "198.51.100.42")
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_bind_address_preset_selection_is_saved_and_updated_in_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            persisted: list[tuple[list[dict[str, object]], str]] = []

            def on_profiles_changed(profiles: list[dict[str, object]], selected_profile_id: str) -> None:
                copied = [
                    {
                        "id": str(profile["id"]),
                        "name": str(profile["name"]),
                        "config": dict(profile["config"]),
                    }
                    for profile in profiles
                ]
                persisted.append((copied, selected_profile_id))

            dialog = self._create_dialog(instance_dir=instance_dir, on_profiles_changed=on_profiles_changed)
            try:
                self._select_bind_host_preset(dialog, "0.0.0.0")
                with patch("snakesh.ui.web_server_dialog.QInputDialog.getText", return_value=("Wildcard Bind", True)):
                    dialog._save_current_profile_as()
                QApplication.processEvents()

                self.assertEqual(persisted[-1][0][0]["config"]["bind_host"], "0.0.0.0")

                self._select_bind_host_preset(dialog, "::")
                dialog._update_selected_profile()
                QApplication.processEvents()

                self.assertEqual(persisted[-1][0][0]["config"]["bind_host"], "::")
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_bind_address_interface_enumeration_failure_uses_fixed_choices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            dialog = self._create_dialog(instance_dir=instance_dir, interface_error=RuntimeError("no interfaces"))
            try:
                QApplication.processEvents()
                self.assertEqual(
                    self._bind_host_items(dialog),
                    [
                        ("All IPv4 interfaces (0.0.0.0)", "0.0.0.0"),
                        ("All IPv6 interfaces (::)", "::"),
                        ("Loopback IPv4 (127.0.0.1)", "127.0.0.1"),
                        ("Loopback IPv6 (::1)", "::1"),
                        ("Custom...", "__custom_bind_host__"),
                    ],
                )
                dialog.bind_host_preset_input.setCurrentIndex(dialog.bind_host_preset_input.count() - 1)
                QApplication.processEvents()
                self.assertFalse(dialog.bind_host_custom_input.isHidden())
                dialog.bind_host_custom_input.setText("198.51.100.30")
                QApplication.processEvents()
                self.assertEqual(dialog.bind_host_input.text(), "198.51.100.30")
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()

    def test_bind_address_preset_order_is_platform_neutral(self) -> None:
        inventories = [
            ("Ethernet", "Wi-Fi"),
            ("en0", "bridge0"),
            ("eth0", "wlan0"),
        ]

        for primary_name, secondary_name in inventories:
            with self.subTest(primary_name=primary_name, secondary_name=secondary_name):
                with tempfile.TemporaryDirectory() as tmp:
                    instance_dir = Path(tmp) / "instance"
                    instance_dir.mkdir()
                    dialog = self._create_dialog(
                        instance_dir=instance_dir,
                        interface_info=self._interface_inventory(
                            primary_name=primary_name,
                            secondary_name=secondary_name,
                        ),
                    )
                    try:
                        QApplication.processEvents()
                        items = self._bind_host_items(dialog)
                        self.assertEqual(
                            items[:4],
                            [
                                ("All IPv4 interfaces (0.0.0.0)", "0.0.0.0"),
                                ("All IPv6 interfaces (::)", "::"),
                                ("Loopback IPv4 (127.0.0.1)", "127.0.0.1"),
                                ("Loopback IPv6 (::1)", "::1"),
                            ],
                        )
                        self.assertEqual(items[-1], ("Custom...", "__custom_bind_host__"))
                        expected_discovered = sorted(
                            [
                                (f"{primary_name} - 192.0.2.22", "192.0.2.22"),
                                (f"{primary_name} - fe80::1234", "fe80::1234"),
                                (f"{secondary_name} - 198.51.100.5 (down)", "198.51.100.5"),
                                (f"{secondary_name} - 2001:db8::5 (down)", "2001:db8::5"),
                            ],
                            key=lambda item: (
                                item[0].split(" - ", 1)[0].lower(),
                                0 if ":" not in item[1] else 1,
                                item[1],
                            ),
                        )
                        self.assertEqual(items[4:8], expected_discovered)

                        dialog.bind_host_preset_input.setCurrentIndex(dialog.bind_host_preset_input.count() - 1)
                        QApplication.processEvents()
                        self.assertFalse(dialog.bind_host_custom_input.isHidden())
                        self.assertEqual(dialog.bind_host_custom_input.text(), "127.0.0.1")
                    finally:
                        dialog._poll_timer.stop()
                        dialog.deleteLater()
                        QApplication.processEvents()

    def test_open_log_folder_uses_local_path_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instance_dir = Path(tmp) / "instance"
            instance_dir.mkdir()
            dialog = self._create_dialog(instance_dir=instance_dir)
            try:
                with (
                    patch(
                        "snakesh.ui.web_server_dialog.read_web_server_status",
                        return_value=SimpleNamespace(log_path=""),
                    ),
                    patch(
                        "snakesh.ui.web_server_dialog.web_server_logs_root",
                        return_value=Path("/tmp/snakesh-web-logs"),
                    ),
                    patch("snakesh.ui.web_server_dialog.open_local_path", return_value=True) as mock_open,
                ):
                    dialog._open_log_folder()
                self.assertEqual(mock_open.call_args.args[0], Path("/tmp/snakesh-web-logs"))
            finally:
                dialog._poll_timer.stop()
                dialog.deleteLater()
                QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
