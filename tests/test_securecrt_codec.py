from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from snakesh.core.models import Protocol, Session
from snakesh.services.securecrt_codec import SecureCRTCodecService


class SecureCRTCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codec = SecureCRTCodecService()

    def test_imports_securecrt_ini_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Ops").mkdir(parents=True, exist_ok=True)
            (root / "__FolderData__.ini").write_text('S:"Folder"=data\n', encoding="utf-8")
            (root / "Ops" / "AngolaSSH.ini").write_text(
                "\n".join(
                    [
                        'S:"Session Name"=AngolaSSH',
                        'S:"Protocol Name"=SSH2',
                        'S:"Hostname"=angola.example.com',
                        'S:"Username"=ops',
                        'D:"[SSH2] Port"=00000016',
                        'B:"Forward X11"=00000001',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "Ops" / "DC-RDP.ini").write_text(
                "\n".join(
                    [
                        'S:"Protocol Name"=RDP',
                        'S:"Hostname"=rdp.example.com',
                        'S:"Username"=admin',
                        'D:"[RDP] Port"=00000d3d',
                        'B:"Full Screen"=00000001',
                        'D:"Desktop Width"=00000780',
                        'D:"Desktop Height"=00000438',
                        'D:"Color Depth"=00000020',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = self.codec.import_from_path(root)

            self.assertEqual(report.scanned_files, 3)
            self.assertEqual(report.imported_count, 2)
            self.assertIn("Ops", report.folders)
            by_name = {session.name: session for session in report.imported_sessions}
            self.assertEqual(by_name["AngolaSSH"].protocol, Protocol.SSH)
            self.assertEqual(by_name["AngolaSSH"].port, 22)
            self.assertEqual(by_name["AngolaSSH"].folder, "Ops")
            self.assertEqual(by_name["DC-RDP"].protocol, Protocol.RDP)
            self.assertTrue(by_name["DC-RDP"].display_fullscreen)
            self.assertEqual(by_name["DC-RDP"].display_resolution, "1920x1080")
            self.assertEqual(by_name["DC-RDP"].display_color_depth, 32)
            self.assertIn("Ops", report.folders)

    def test_import_preserves_folder_structure_including_empty_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Prod" / "Linux").mkdir(parents=True, exist_ok=True)
            (root / "Prod" / "Windows" / "Unused").mkdir(parents=True, exist_ok=True)
            (root / "Prod" / "Linux" / "JumpHost.ini").write_text(
                "\n".join(
                    [
                        'S:"Session Name"=JumpHost',
                        'S:"Protocol Name"=SSH2',
                        'S:"Hostname"=jump.prod.example.com',
                        'D:"[SSH2] Port"=00000016',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = self.codec.import_from_path(root)

            self.assertEqual(report.imported_count, 1)
            self.assertIn("Prod", report.folders)
            self.assertIn("Prod/Linux", report.folders)
            self.assertIn("Prod/Windows", report.folders)
            self.assertIn("Prod/Windows/Unused", report.folders)

    def test_export_then_import_roundtrip_preserves_core_fields(self) -> None:
        sessions = [
            Session(
                name="DB:SSH",
                host="db.example.com",
                protocol=Protocol.SSH,
                port=22,
                username="root",
                notes="primary database host",
                folder="Default",
                use_key_auth=True,
                x11_forwarding=True,
            ),
            Session(
                name="NOC-VNC",
                host="203.0.113.8",
                protocol=Protocol.VNC,
                port=5901,
                display_resolution="1600x900",
                display_fullscreen=True,
                display_color_depth=24,
                folder="Sites/NOC",
            ),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_path = root / "securecrt-export.xml"
            export_report = self.codec.export_xml(sessions, export_path)
            self.assertEqual(export_report.exported_count, 2)
            self.assertTrue(export_path.exists())

            import_report = self.codec.import_from_path(export_path)
            self.assertEqual(import_report.imported_count, 2)
            by_host = {session.host: session for session in import_report.imported_sessions}
            self.assertEqual(by_host["db.example.com"].protocol, Protocol.SSH)
            self.assertEqual(by_host["db.example.com"].username, "root")
            self.assertEqual(by_host["203.0.113.8"].protocol, Protocol.VNC)
            self.assertEqual(by_host["203.0.113.8"].display_resolution, "1600x900")
            self.assertTrue(by_host["203.0.113.8"].display_fullscreen)
            self.assertEqual(by_host["203.0.113.8"].display_color_depth, 24)
            self.assertEqual(by_host["203.0.113.8"].folder, "Sites/NOC")
            self.assertIn("Sites", import_report.folders)
            self.assertIn("Sites/NOC", import_report.folders)

    def test_imports_sessions_from_securecrt_xml_text_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_path = root / "SecureCRT-Export.xml"
            xml_path.write_text(
                "\n".join(
                    [
                        "<settings>",
                        "  <entry><![CDATA[",
                        'S:"Session Name"=XML-SSH',
                        'S:"Protocol Name"=SSH2',
                        'S:"Hostname"=xml-ssh.example.com',
                        'S:"Username"=xmluser',
                        'D:"[SSH2] Port"=00000016',
                        "  ]]></entry>",
                        "  <entry><![CDATA[",
                        'S:"Session Name"=XML-RDP',
                        'S:"Protocol Name"=RDP',
                        'S:"Hostname"=xml-rdp.example.com',
                        'D:"[RDP] Port"=00000d3d',
                        'B:"Full Screen"=00000001',
                        "  ]]></entry>",
                        "</settings>",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = self.codec.import_from_path(xml_path)

            self.assertEqual(report.imported_count, 2)
            by_name = {session.name: session for session in report.imported_sessions}
            self.assertEqual(by_name["XML-SSH"].protocol, Protocol.SSH)
            self.assertEqual(by_name["XML-SSH"].port, 22)
            self.assertEqual(by_name["XML-RDP"].protocol, Protocol.RDP)
            self.assertEqual(by_name["XML-RDP"].port, 3389)
            self.assertTrue(by_name["XML-RDP"].display_fullscreen)

    def test_imports_securecrt_hierarchical_key_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            xml_path = Path(tmp) / "securecrt-hierarchy.xml"
            xml_path.write_text(
                "\n".join(
                    [
                        "<?xml version='1.0' encoding='utf-8'?>",
                        "<root>",
                        '  <key name="Sessions">',
                        '    <key name="Broadworks">',
                        '      <key name="Lab">',
                        '        <key name="Broadworks - ADP10CommLab">',
                        '          <string name="Protocol Name">SSH2</string>',
                        '          <string name="[SSH2] Hostname">adp10.example.com</string>',
                        '          <string name="[SSH2] Username">labops</string>',
                        '          <dword name="[SSH2] Port">00000016</dword>',
                        "        </key>",
                        "      </key>",
                        "    </key>",
                        "  </key>",
                        "</root>",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            report = self.codec.import_from_path(xml_path)

            self.assertEqual(report.imported_count, 1)
            self.assertIn("Broadworks", report.folders)
            self.assertIn("Broadworks/Lab", report.folders)
            session = report.imported_sessions[0]
            self.assertEqual(session.name, "Broadworks - ADP10CommLab")
            self.assertEqual(session.folder, "Broadworks/Lab")
            self.assertEqual(session.host, "adp10.example.com")

    def test_export_then_import_roundtrip_preserves_auto_resolution_marker(self) -> None:
        session = Session(
            name="RDP-Auto",
            host="rdp-auto.example.com",
            protocol=Protocol.RDP,
            port=3389,
            display_resolution="auto",
            folder="Default",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_path = root / "securecrt-export-auto.xml"
            export_report = self.codec.export_xml([session], export_path)
            self.assertEqual(export_report.exported_count, 1)
            self.assertTrue(export_path.exists())

            import_report = self.codec.import_from_path(export_path)
            self.assertEqual(import_report.imported_count, 1)
            restored = import_report.imported_sessions[0]
            self.assertEqual(restored.display_resolution, "auto")


if __name__ == "__main__":
    unittest.main()
