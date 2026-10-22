from __future__ import annotations

import subprocess
import sys
from unittest.mock import Mock, patch

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from snakesh.core.models import Protocol, Session
from snakesh.services.rdp_certificates import (
    RdpCertificateChange, certificate_change_from_output, replace_reviewed_certificate,
)
from snakesh.services.viewer_output import ViewerOutput
from snakesh.ui.main_window import RemoteViewerTab

OLD = ":".join(["72"] * 32)
NEW = ":".join(["40"] * 32)
OUTPUT = f"""Certificate for example.test:3389 has changed!
New Certificate details:
    Subject: CN = Windows
    Thumbprint: {NEW}
Old Certificate details:
    Subject: CN = Windows
    Thumbprint: {OLD}
The above X.509 certificate does not match the certificate used for previous connections.
"""
_APP = None


def test_certificate_change_updates_only_the_reviewed_host(tmp_path):
    session = Session(id="rdp", name="Test", host="example.test", protocol=Protocol.RDP, port=3389)
    path = tmp_path / "known_hosts2"
    other = f"other.test 3389 {OLD} subject issuer\n"
    path.write_text(f"example.test 3389 {OLD} subject issuer\n" + other)
    change = certificate_change_from_output(OUTPUT)
    assert change == RdpCertificateChange(OLD, NEW)
    replace_reviewed_certificate(session, change, known_hosts_path=path)
    assert path.read_text() == f"example.test 3389 {NEW} subject issuer\n" + other


def test_reviewed_certificate_must_still_match_the_saved_pin(tmp_path):
    session = Session(id="rdp", name="Test", host="example.test", protocol=Protocol.RDP, port=3389)
    path = tmp_path / "known_hosts2"
    original = f"example.test 3389 {NEW} subject issuer\n"
    path.write_text(original)
    with pytest.raises(ValueError, match="changed while"):
        replace_reviewed_certificate(session, RdpCertificateChange(OLD, NEW), known_hosts_path=path)
    assert path.read_text() == original
    assert certificate_change_from_output("TLS connection failed") is None


@pytest.mark.parametrize("approve", [False, True])
def test_changed_certificate_dialog_defaults_to_no_and_only_retries_after_approval(approve):
    global _APP
    _APP = QApplication.instance() or QApplication([])
    session = Session(id="rdp", name="Test", host="example.test", protocol=Protocol.RDP, port=3389)
    tab = RemoteViewerTab(session=session, protocol_name="RDP", detached_command_builder=lambda: ([], "", None))
    try:
        tab._last_viewer_error = OUTPUT
        tab._last_exit_mode = "embedded"
        with (
            patch("snakesh.ui.main_window.QMessageBox.question", return_value=QMessageBox.Yes if approve else QMessageBox.No) as prompt,
            patch("snakesh.ui.main_window.replace_reviewed_certificate") as replace,
            patch.object(tab, "attach_viewer", return_value=(True, "attached")) as reconnect,
        ):
            tab._offer_changed_rdp_certificate()
        assert prompt.call_args.args[-1] == QMessageBox.No
        assert OLD in prompt.call_args.args[2] and NEW in prompt.call_args.args[2]
        assert replace.call_count == int(approve)
        assert reconnect.call_count == int(approve)
    finally:
        tab.shutdown()
        tab.deleteLater()
        QApplication.processEvents()


def test_viewer_output_drains_large_output_and_keeps_certificate_failure():
    script = "import sys; sys.stdout.write('x' * 200000 + '\\n' + " + repr(OUTPUT) + "); sys.exit(143)"
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = ViewerOutput(process.stdout)
    assert process.wait(timeout=10) == 143
    text = output.tail()
    assert len(text) <= ViewerOutput.MAX_CHARS
    assert certificate_change_from_output(text) == RdpCertificateChange(OLD, NEW)
