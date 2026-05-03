from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from snakesh.services.file_hash_service import (
    compute_file_hash,
    parse_checksum_entries,
    verify_file_against_checksum_file,
    verify_file_hash,
)


class FileHashServiceTests(unittest.TestCase):
    def test_compute_file_hash_streams_large_file(self) -> None:
        payload = (b"SnakeSh" * 200000) + b"tail"
        expected = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "payload.bin"
            target.write_bytes(payload)

            result = compute_file_hash(target, "sha256", chunk_size=4096)

        self.assertEqual(result.digest, expected)
        self.assertEqual(result.size_bytes, len(payload))

    def test_verify_file_hash_pass_and_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "data.txt"
            target.write_text("hello world", encoding="utf-8")
            digest = hashlib.sha1(b"hello world").hexdigest()

            matched = verify_file_hash(target, "sha1", digest)
            mismatched = verify_file_hash(target, "sha1", "0" * len(digest))

        self.assertTrue(matched.matched)
        self.assertFalse(mismatched.matched)

    def test_parse_checksum_entries_supports_plain_gnu_and_bsd_formats(self) -> None:
        plain = parse_checksum_entries("a" * 64)
        gnu = parse_checksum_entries(("b" * 64) + "  file.txt")
        bsd = parse_checksum_entries("SHA256 (file.txt) = " + ("c" * 64))

        self.assertIsNone(plain[0].filename)
        self.assertEqual(gnu[0].filename, "file.txt")
        self.assertEqual(bsd[0].algorithm, "sha256")

    def test_verify_file_against_checksum_file_matches_unique_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "payload.txt"
            target.write_text("payload", encoding="utf-8")
            digest = hashlib.sha256(b"payload").hexdigest()
            checksum = root / "payload.sha256"
            checksum.write_text(f"{digest}  payload.txt\n", encoding="utf-8")

            result = verify_file_against_checksum_file(target, "sha256", checksum)

        self.assertTrue(result.matched)
        self.assertEqual(result.matched_filename, "payload.txt")

    def test_verify_file_against_checksum_file_rejects_ambiguous_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "payload.txt"
            target.write_text("payload", encoding="utf-8")
            digest = hashlib.sha256(b"payload").hexdigest()
            checksum = root / "payload.sha256"
            checksum.write_text(
                f"{digest}  one/payload.txt\n{digest}  two/payload.txt\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "same file name"):
                verify_file_against_checksum_file(target, "sha256", checksum)


if __name__ == "__main__":
    unittest.main()
