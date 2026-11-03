from __future__ import annotations

import ipaddress
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtNetwork import QHostAddress, QNetworkAddressEntry, QNetworkInterface
from PySide6.QtWidgets import QApplication

from snakesh.services import macos_local_network as network
from snakesh.ui.local_network_access_dialog import LocalNetworkAccessDialog
from snakesh.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("system,version,expected", [
    ("darwin", "15.0", True), ("darwin", "26.2", True),
    ("darwin", "14.7", False), ("darwin", "", False),
    ("linux", "26.2", False), ("win32", "26.2", False),
])
def test_supported_platforms(system, version, expected):
    with patch.object(network.sys, "platform", system), patch.object(
        network.platform, "mac_ver", return_value=(version, (), "")
    ):
        assert network.local_network_privacy_supported() is expected


def _interface(index, flags, *addresses):
    entries = []
    for address in addresses:
        entry = QNetworkAddressEntry()
        entry.setIp(QHostAddress(address))
        entries.append(entry)
    return SimpleNamespace(flags=lambda: flags, index=lambda: index, addressEntries=lambda: entries)


def test_request_uses_scoped_random_link_local_peers_and_sends_no_datagrams():
    flags = QNetworkInterface.InterfaceFlag
    active = flags.IsUp | flags.CanBroadcast
    interfaces = [
        _interface(4, active, "192.168.1.12", "2001:db8::1", "fe80::1234%en0"),
        _interface(9, active, "fe80::4321%en1"),
        _interface(10, flags.CanBroadcast, "fe80::1111"),
        _interface(1, flags.IsUp | flags.IsLoopBack, "::1"),
        _interface(0, active, "fe80::1111"),
    ]
    sockets = [MagicMock() for _ in range(4)]
    for probe in sockets:
        probe.__enter__.return_value = probe
    # Denial/network errors must not prevent trying another interface.
    sockets[0].connect.side_effect = PermissionError("Denied")
    with (
        patch.object(network, "local_network_privacy_supported", return_value=True),
        patch.object(network.QNetworkInterface, "allInterfaces", return_value=interfaces),
        patch.object(network.socket, "socket", side_effect=sockets) as factory,
        patch.object(network.secrets, "token_bytes", side_effect=[bytes([n]) * 8 for n in range(1, 5)]),
    ):
        assert network.request_local_network_access() == 4
    assert factory.call_count == 4
    assert all(c.args == (network.socket.AF_INET6, network.socket.SOCK_DGRAM)
               for c in factory.call_args_list)
    for probe, scope in zip(sockets, [4, 4, 9, 9]):
        probe.setblocking.assert_called_once_with(False)
        address, port, flow, scope_id = probe.connect.call_args.args[0]
        assert ipaddress.IPv6Address(address).is_link_local
        assert (port, flow, scope_id) == (9, 0, scope)
        probe.send.assert_not_called()
        probe.sendto.assert_not_called()
        probe.sendall.assert_not_called()
        probe.__exit__.assert_called_once()


def test_request_without_network_or_socket_resources_is_safe():
    with (
        patch.object(network, "local_network_privacy_supported", return_value=True),
        patch.object(network, "_probe_addresses", return_value=[]) as addresses,
        patch.object(network.socket, "socket", side_effect=OSError("No sockets")) as factory,
    ):
        assert network.request_local_network_access() == 0
        factory.assert_not_called()
        addresses.return_value = [("fe80::1234", 9, 0, 4)]
        assert network.request_local_network_access() == 0


def test_unsupported_platform_does_not_enumerate_or_probe():
    with (
        patch.object(network, "local_network_privacy_supported", return_value=False),
        patch.object(network, "_probe_addresses") as addresses,
        patch.object(network.socket, "socket") as factory,
    ):
        assert network.request_local_network_access() == 0
        addresses.assert_not_called()
        factory.assert_not_called()


@pytest.mark.parametrize("attempts", [0, 2])
def test_dialog_does_not_claim_granted_or_block_closing(app, attempts):
    dialog = LocalNetworkAccessDialog()
    try:
        dialog.show()
        with patch(
            "snakesh.ui.local_network_access_dialog.request_local_network_access", return_value=attempts
        ) as request:
            dialog.request_button.click()
            request.assert_called_once()
        assert not dialog.isModal()
        status = dialog.status_label.text()
        assert "System Settings" in status
        assert "granted" not in status.lower()
        if attempts:
            assert "allowed or denied" in status
        else:
            assert "current network" in status
        dialog.reject()
        assert not dialog.isVisible()
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_settings_shortcut_failure_keeps_manual_instructions(app):
    dialog = LocalNetworkAccessDialog()
    try:
        with patch(
            "snakesh.ui.local_network_access_dialog.QDesktopServices.openUrl", return_value=False
        ) as open_url:
            dialog.settings_button.click()
        assert open_url.call_args.args[0].scheme() == "x-apple.systempreferences"
        assert "Privacy & Security > Local Network" in dialog.status_label.text()
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_startup_request_remembers_explanation_only_once(app, tmp_path):
    preferences = QSettings(str(tmp_path / "local.ini"), QSettings.Format.IniFormat)
    dialog = Mock()
    window = SimpleNamespace(_safe_mode=False, _local_network_access_dialog=dialog,
                             _open_local_network_access=Mock())
    with (
        patch("snakesh.ui.main_window.local_network_privacy_supported", return_value=True),
        patch("snakesh.ui.main_window.QSettings", return_value=preferences),
    ):
        MainWindow._offer_local_network_access_on_startup(window)
        MainWindow._offer_local_network_access_on_startup(window)
    window._open_local_network_access.assert_called_once()
    dialog.request_access.assert_called_once()
    assert preferences.allKeys() == ["macos_local_network/explanation_shown"]


@pytest.mark.parametrize("supported,safe_mode", [(False, False), (True, True)])
def test_startup_request_skips_other_platforms_and_safe_mode(supported, safe_mode):
    window = SimpleNamespace(_safe_mode=safe_mode, _open_local_network_access=Mock())
    with (
        patch("snakesh.ui.main_window.local_network_privacy_supported", return_value=supported),
        patch("snakesh.ui.main_window.QSettings") as preferences,
    ):
        MainWindow._offer_local_network_access_on_startup(window)
    window._open_local_network_access.assert_not_called()
    preferences.assert_not_called()
