from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "build_oui_snapshot.py"


class BuildOUISnapshotScriptTests(unittest.TestCase):
    def test_script_merges_ieee_and_vendor_export_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ieee_csv = tmp_path / "ieee.csv"
            ieee_csv.write_text(
                "Assignment,Organization Name\n001122,Vendor A\n",
                encoding="utf-8",
            )
            vendor_export_csv = tmp_path / "mac-vendors-export.csv"
            vendor_export_csv.write_text(
                (
                    "Mac Prefix,Vendor Name,Private,Block Type,Last Update\n"
                    "00:11:22,Vendor Override,false,MA-L,2024/01/01\n"
                    "C8:7F:54,ASUSTek COMPUTER INC.,false,MA-L,2022/08/13\n"
                ),
                encoding="utf-8",
            )
            output_path = tmp_path / "merged.json"

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--input",
                    str(ieee_csv),
                    "--input",
                    str(vendor_export_csv),
                    "--output",
                    str(output_path),
                ],
                check=True,
                cwd=PROJECT_ROOT,
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            vendors = {entry["prefix"]: entry["vendor"] for entry in payload}

        self.assertEqual(vendors["001122"], "Vendor Override")
        self.assertEqual(vendors["C87F54"], "ASUSTek COMPUTER INC.")


if __name__ == "__main__":
    unittest.main()
