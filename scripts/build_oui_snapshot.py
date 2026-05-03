#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import Request, urlopen


OFFICIAL_IEEE_SOURCES: tuple[tuple[str, str], ...] = (
    ("oui.csv", "https://standards-oui.ieee.org/oui/oui.csv"),
    ("mam.csv", "https://standards-oui.ieee.org/oui28/mam.csv"),
    ("oui36.csv", "https://standards-oui.ieee.org/oui36/oui36.csv"),
    ("iab.csv", "https://standards-oui.ieee.org/iab/iab.csv"),
)
_HEX_RE = re.compile(r"[^0-9A-Fa-f]+")
_PREFIX_COLUMNS = ("Assignment", "Mac Prefix", "Prefix", "OUI")
_VENDOR_COLUMNS = ("Organization Name", "Vendor Name", "Vendor", "Organization")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a compact bundled OUI snapshot from IEEE CSV files or compatible vendor exports.",
    )
    parser.add_argument(
        "--input",
        action="append",
        dest="inputs",
        help=(
            "Source CSV file (repeatable). Supports IEEE-style Assignment/Organization Name columns "
            "and vendor-export Mac Prefix/Vendor Name columns."
        ),
    )
    parser.add_argument(
        "--download-official",
        action="store_true",
        help="Download the latest official IEEE registry CSV feeds before building the snapshot.",
    )
    parser.add_argument(
        "--cache-dir",
        help="Directory used to store downloaded official CSVs. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output JSON file path.",
    )
    return parser


def _download_official_sources(target_dir: Path) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded_paths: list[Path] = []
    for file_name, url in OFFICIAL_IEEE_SOURCES:
        request = Request(url, headers={"User-Agent": "SnakeSh OUI Snapshot Builder/1.0"})
        output_path = target_dir / file_name
        try:
            with urlopen(request, timeout=30) as response, output_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        except (OSError, URLError) as exc:
            raise SystemExit(f"Failed to download {url}: {exc}") from exc
        downloaded_paths.append(output_path)
    return downloaded_paths


def _load_entries(paths: list[Path]) -> list[dict[str, str]]:
    vendors_by_prefix: dict[str, str] = {}
    for path in paths:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                entry = _extract_entry(row)
                if entry is None:
                    continue
                prefix, vendor = entry
                vendors_by_prefix[prefix] = vendor

    entries = [
        {"prefix": prefix, "vendor": vendor}
        for prefix, vendor in vendors_by_prefix.items()
    ]
    entries.sort(key=lambda item: (-len(item["prefix"]), item["prefix"]))
    return entries


def _extract_entry(row: dict[str, object]) -> tuple[str, str] | None:
    prefix = _normalize_prefix(_first_row_value(row, _PREFIX_COLUMNS))
    vendor = _first_row_value(row, _VENDOR_COLUMNS)
    if not prefix or not vendor:
        return None
    return prefix, vendor


def _first_row_value(row: dict[str, object], candidates: tuple[str, ...]) -> str:
    for key in candidates:
        value = str(row.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _normalize_prefix(raw: str) -> str:
    normalized = _HEX_RE.sub("", raw).upper()
    if not normalized or len(normalized) > 12:
        return ""
    return normalized


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cache_dir and not args.download_official:
        raise SystemExit("--cache-dir requires --download-official.")

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    requested_inputs = [Path(item).expanduser() for item in args.inputs or []]

    with TemporaryDirectory(prefix="snakesh-oui-") as temp_dir:
        input_paths = list(requested_inputs)
        if args.download_official:
            download_dir = Path(args.cache_dir).expanduser() if args.cache_dir else Path(temp_dir)
            input_paths.extend(_download_official_sources(download_dir))

        if not input_paths:
            raise SystemExit("Provide at least one --input file or use --download-official.")

        missing = [path for path in input_paths if not path.exists()]
        if missing:
            raise SystemExit(f"Missing input file(s): {', '.join(str(path) for path in missing)}")

        payload = _load_entries(input_paths)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))

    source_summary = f"{len(requested_inputs)} local source(s)"
    if args.download_official:
        source_summary += f" + {len(OFFICIAL_IEEE_SOURCES)} official IEEE feed(s)"
    print(f"Wrote {len(payload)} OUI entries to {output_path} from {source_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
