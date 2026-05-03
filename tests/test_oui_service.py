from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from snakesh.services import oui_service
from snakesh.services.oui_service import (
    OUILookupService,
    OUIRecord,
    bundled_oui_lookup_service,
    bundled_oui_snapshot_available,
    load_oui_records,
    normalize_oui_query,
)


class OUIServiceTests(unittest.TestCase):
    def test_normalize_query_strips_separators(self) -> None:
        normalized, bits = normalize_oui_query("70-B3-D5-F2-F/36")
        self.assertEqual(normalized, "70B3D5F2F")
        self.assertEqual(bits, 36)

    def test_lookup_prefers_longest_matching_prefix(self) -> None:
        service = OUILookupService(
            [
                OUIRecord(prefix="001122", bits=24, vendor="Vendor 24"),
                OUIRecord(prefix="0011223", bits=28, vendor="Vendor 28"),
                OUIRecord(prefix="001122334", bits=36, vendor="Vendor 36"),
            ]
        )

        match = service.lookup("00:11:22:33:44:55")

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.vendor, "Vendor 36")
        self.assertEqual(match.bits, 36)

    def test_load_records_from_snapshot_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "oui.json"
            path.write_text(
                json.dumps(
                    [
                        {"prefix": "001122", "vendor": "Vendor A"},
                        {"prefix": "0011223", "vendor": "Vendor B"},
                    ]
                ),
                encoding="utf-8",
            )

            records = load_oui_records(path)

        self.assertEqual([record.prefix for record in records], ["0011223", "001122"])

    def test_bundled_lookup_service_returns_empty_when_snapshot_is_missing(self) -> None:
        bundled_oui_lookup_service.cache_clear()
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "missing.json"
            with patch("snakesh.services.oui_service.bundled_oui_snapshot_path", return_value=missing_path):
                self.assertFalse(bundled_oui_snapshot_available())
                service = bundled_oui_lookup_service()

        self.assertIsNone(service.lookup_vendor("00:11:22:33:44:55"))
        oui_service.bundled_oui_lookup_service.cache_clear()


if __name__ == "__main__":
    unittest.main()
