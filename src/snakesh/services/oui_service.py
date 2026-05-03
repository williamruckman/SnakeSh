from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re

from snakesh import runtime


_HEX_RE = re.compile(r"[^0-9A-Fa-f]+")


@dataclass(frozen=True, slots=True)
class OUIRecord:
    prefix: str
    bits: int
    vendor: str


@dataclass(frozen=True, slots=True)
class OUILookupMatch:
    query: str
    normalized: str
    prefix: str
    bits: int
    vendor: str


def bundled_oui_snapshot_path() -> Path:
    return runtime.asset_path("oui_snapshot.json")


def bundled_oui_snapshot_available() -> bool:
    return bundled_oui_snapshot_path().exists()


def normalize_oui_query(raw: str) -> tuple[str, int | None]:
    text = raw.strip()
    if not text:
        raise ValueError("Enter a MAC address or OUI prefix.")

    bits: int | None = None
    value = text
    if "/" in text:
        value, raw_bits = text.rsplit("/", 1)
        try:
            bits = int(raw_bits.strip())
        except ValueError as exc:
            raise ValueError("OUI prefix length must be a number of bits.") from exc
        if bits <= 0 or bits > 48 or bits % 4 != 0:
            raise ValueError("OUI prefix length must be a multiple of 4 between 4 and 48 bits.")

    normalized = _HEX_RE.sub("", value).upper()
    if not normalized:
        raise ValueError("Enter a MAC address or OUI prefix.")
    if len(normalized) > 12:
        raise ValueError("MAC addresses may contain at most 12 hexadecimal digits.")

    if bits is not None:
        required_digits = bits // 4
        if len(normalized) < required_digits:
            raise ValueError("Input does not contain enough hexadecimal digits for the requested prefix length.")
        normalized = normalized[:required_digits]

    return normalized, bits


def load_oui_records(snapshot_path: Path | None = None) -> list[OUIRecord]:
    path = snapshot_path or bundled_oui_snapshot_path()
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    records: list[OUIRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        prefix = str(item.get("prefix", "")).strip().upper()
        vendor = str(item.get("vendor", "")).strip()
        if not prefix or not vendor:
            continue
        if _HEX_RE.search(prefix):
            continue
        records.append(OUIRecord(prefix=prefix, bits=len(prefix) * 4, vendor=vendor))
    records.sort(key=lambda item: (-len(item.prefix), item.prefix))
    return records


class OUILookupService:
    def __init__(self, records: list[OUIRecord]) -> None:
        mapping: dict[int, dict[str, OUIRecord]] = {}
        for record in records:
            mapping.setdefault(len(record.prefix), {})[record.prefix] = record
        self._records_by_length = mapping
        self._lengths_desc = sorted(mapping.keys(), reverse=True)

    def lookup(self, raw: str) -> OUILookupMatch | None:
        normalized, explicit_bits = normalize_oui_query(raw)
        max_digits = len(normalized)
        if explicit_bits is not None:
            max_digits = min(max_digits, explicit_bits // 4)

        for digits in self._lengths_desc:
            if digits > max_digits:
                continue
            candidate = normalized[:digits]
            record = self._records_by_length.get(digits, {}).get(candidate)
            if record is None:
                continue
            return OUILookupMatch(
                query=raw,
                normalized=normalized,
                prefix=record.prefix,
                bits=record.bits,
                vendor=record.vendor,
            )
        return None

    def lookup_vendor(self, raw: str) -> str | None:
        match = self.lookup(raw)
        if match is None:
            return None
        return match.vendor


@lru_cache(maxsize=1)
def bundled_oui_lookup_service() -> OUILookupService:
    try:
        records = load_oui_records()
    except FileNotFoundError:
        records = []
    return OUILookupService(records)
