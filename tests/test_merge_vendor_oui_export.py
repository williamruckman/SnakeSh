from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "merge_vendor_oui_export.py"


class MergeVendorOUIExportScriptTests(unittest.TestCase):
    def test_script_merges_vendor_export_into_standard_ieee_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ieee_dir = tmp_path / "ieee-data"
            ieee_dir.mkdir()
            (ieee_dir / "oui.csv").write_text(
                "Assignment,Organization Name\n001122,Vendor A\n",
                encoding="utf-8",
            )
            (ieee_dir / "mam.csv").write_text(
                "Assignment,Organization Name\n0011223,Vendor 28\n",
                encoding="utf-8",
            )
            (ieee_dir / "oui36.csv").write_text(
                "Assignment,Organization Name\n001122334,Vendor 36\n",
                encoding="utf-8",
            )
            (ieee_dir / "iab.csv").write_text(
                "Assignment,Organization Name\nAABBCC,Vendor IAB\n",
                encoding="utf-8",
            )
            vendor_export_csv = tmp_path / "mac-vendors-export.csv"
            vendor_export_csv.write_text(
                (
                    "Mac Prefix,Vendor Name,Private,Block Type,Last Update\n"
                    "C8:7F:54,ASUSTek COMPUTER INC.,false,MA-L,2022/08/13\n"
                ),
                encoding="utf-8",
            )
            output_path = tmp_path / "merged.json"

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    str(vendor_export_csv),
                    "--ieee-dir",
                    str(ieee_dir),
                    "--output",
                    str(output_path),
                ],
                check=True,
                cwd=PROJECT_ROOT,
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            vendors = {entry["prefix"]: entry["vendor"] for entry in payload}

        self.assertEqual(vendors["001122"], "Vendor A")
        self.assertEqual(vendors["0011223"], "Vendor 28")
        self.assertEqual(vendors["001122334"], "Vendor 36")
        self.assertEqual(vendors["AABBCC"], "Vendor IAB")
        self.assertEqual(vendors["C87F54"], "ASUSTek COMPUTER INC.")


if __name__ == "__main__":
    unittest.main()
