from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import time


SUPPORTED_HASH_ALGORITHMS: tuple[str, ...] = (
    "md5",
    "sha1",
    "sha256",
    "sha384",
    "sha512",
    "blake2b",
)

_GNU_CHECKSUM_RE = re.compile(r"^([0-9A-Fa-f]+)\s+[\*\ ](.+)$")
_BSD_CHECKSUM_RE = re.compile(r"^([A-Za-z0-9_-]+)\s+\((.+)\)\s+=\s+([0-9A-Fa-f]+)$")
_HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")


@dataclass(frozen=True, slots=True)
class FileHashResult:
    file_path: str
    algorithm: str
    digest: str
    size_bytes: int
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class ChecksumEntry:
    digest: str
    filename: str | None = None
    algorithm: str | None = None


@dataclass(frozen=True, slots=True)
class FileHashVerificationResult:
    matched: bool
    algorithm: str
    expected_digest: str
    actual_digest: str
    source: str
    matched_filename: str | None = None


def compute_file_hash(file_path: str | Path, algorithm: str, *, chunk_size: int = 1024 * 1024) -> FileHashResult:
    normalized_algorithm = normalize_hash_algorithm(algorithm)
    target = Path(file_path).expanduser()
    if not target.exists() or not target.is_file():
        raise ValueError(f"File does not exist: {target}")

    hasher = hashlib.new(normalized_algorithm)
    size_bytes = 0
    started = time.perf_counter()
    with target.open("rb") as handle:
        while True:
            chunk = handle.read(max(1024, int(chunk_size)))
            if not chunk:
                break
            size_bytes += len(chunk)
            hasher.update(chunk)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return FileHashResult(
        file_path=str(target),
        algorithm=normalized_algorithm,
        digest=hasher.hexdigest(),
        size_bytes=size_bytes,
        elapsed_ms=elapsed_ms,
    )


def normalize_hash_algorithm(raw: str) -> str:
    algorithm = raw.strip().lower()
    if algorithm not in SUPPORTED_HASH_ALGORITHMS:
        raise ValueError(f"Unsupported hash algorithm: {raw}")
    return algorithm


def normalize_digest(raw: str) -> str:
    digest = "".join(raw.split()).lower()
    if not digest:
        raise ValueError("Enter a hash value to compare.")
    if not _HEX_RE.fullmatch(digest):
        raise ValueError("Hash values must contain hexadecimal characters only.")
    return digest


def verify_file_hash(file_path: str | Path, algorithm: str, expected_digest: str) -> FileHashVerificationResult:
    normalized_algorithm = normalize_hash_algorithm(algorithm)
    expected = normalize_digest(expected_digest)
    actual = compute_file_hash(file_path, normalized_algorithm).digest
    return FileHashVerificationResult(
        matched=(actual == expected),
        algorithm=normalized_algorithm,
        expected_digest=expected,
        actual_digest=actual,
        source="manual",
    )


def parse_checksum_entries(raw_text: str) -> list[ChecksumEntry]:
    entries: list[ChecksumEntry] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        bsd_match = _BSD_CHECKSUM_RE.fullmatch(stripped)
        if bsd_match:
            entries.append(
                ChecksumEntry(
                    digest=normalize_digest(bsd_match.group(3)),
                    filename=bsd_match.group(2).strip(),
                    algorithm=bsd_match.group(1).strip().lower(),
                )
            )
            continue

        gnu_match = _GNU_CHECKSUM_RE.fullmatch(stripped)
        if gnu_match:
            entries.append(
                ChecksumEntry(
                    digest=normalize_digest(gnu_match.group(1)),
                    filename=gnu_match.group(2).strip(),
                )
            )
            continue

        if _HEX_RE.fullmatch(stripped):
            entries.append(ChecksumEntry(digest=normalize_digest(stripped)))
            continue

        raise ValueError(f"Unsupported checksum line format: {line}")

    if not entries:
        raise ValueError("Checksum file did not contain any usable hash entries.")
    return entries


def parse_checksum_file(checksum_file_path: str | Path) -> list[ChecksumEntry]:
    path = Path(checksum_file_path).expanduser()
    if not path.exists() or not path.is_file():
        raise ValueError(f"Checksum file does not exist: {path}")
    return parse_checksum_entries(path.read_text(encoding="utf-8"))


def verify_file_against_checksum_file(
    file_path: str | Path,
    algorithm: str,
    checksum_file_path: str | Path,
) -> FileHashVerificationResult:
    normalized_algorithm = normalize_hash_algorithm(algorithm)
    entries = parse_checksum_file(checksum_file_path)
    target = Path(file_path).expanduser()
    entry = select_checksum_entry(entries, target)
    if entry.algorithm and entry.algorithm.lower() != normalized_algorithm:
        raise ValueError(
            f"Checksum entry uses {entry.algorithm.upper()}, but {normalized_algorithm.upper()} is selected."
        )

    actual = compute_file_hash(target, normalized_algorithm).digest
    return FileHashVerificationResult(
        matched=(actual == entry.digest),
        algorithm=normalized_algorithm,
        expected_digest=entry.digest,
        actual_digest=actual,
        source="checksum_file",
        matched_filename=entry.filename,
    )


def select_checksum_entry(entries: list[ChecksumEntry], file_path: Path) -> ChecksumEntry:
    named_entries = [entry for entry in entries if entry.filename]
    if not named_entries:
        if len(entries) == 1:
            return entries[0]
        raise ValueError("Checksum file contains multiple unnamed hashes; unable to determine which one to use.")

    target_resolved = str(file_path.resolve())
    normalized_target = target_resolved.replace("\\", "/")
    basename = file_path.name.lower()

    exact_matches: list[ChecksumEntry] = []
    basename_matches: list[ChecksumEntry] = []
    for entry in named_entries:
        assert entry.filename is not None
        candidate = entry.filename.strip()
        if not candidate:
            continue
        normalized_candidate = candidate.replace("\\", "/")
        if normalized_candidate == normalized_target:
            exact_matches.append(entry)
            continue
        if Path(candidate).name.lower() == basename:
            basename_matches.append(entry)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise ValueError("Checksum file contains multiple entries that exactly match this file path.")
    if len(basename_matches) == 1:
        return basename_matches[0]
    if len(basename_matches) > 1:
        raise ValueError("Checksum file contains multiple entries with the same file name.")
    raise ValueError(f"No checksum entry matched {file_path.name}.")
