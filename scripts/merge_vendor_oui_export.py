#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_oui_snapshot.py"
DEFAULT_OUTPUT = PROJECT_ROOT / "src" / "snakesh" / "assets" / "oui_snapshot.json"
DEFAULT_IEEE_DIR = Path("/usr/share/ieee-data")
DEFAULT_IEEE_FILES = ("oui.csv", "mam.csv", "oui36.csv", "iab.csv")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Merge one or more vendor-export CSV files into SnakeSh's bundled OUI snapshot "
            "using the standard IEEE base data."
        ),
    )
    parser.add_argument(
        "vendor_exports",
        nargs="+",
        help="Vendor export CSV file(s) to merge. Expected columns: Mac Prefix, Vendor Name.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Bundled snapshot output path. Defaults to {DEFAULT_OUTPUT}.",
    )
    parser.add_argument(
        "--ieee-dir",
        default=str(DEFAULT_IEEE_DIR),
        help=f"Directory containing oui.csv, mam.csv, oui36.csv, and iab.csv. Defaults to {DEFAULT_IEEE_DIR}.",
    )
    parser.add_argument(
        "--download-official",
        action="store_true",
        help="Download the current official IEEE registry feeds instead of reading local IEEE CSV files.",
    )
    parser.add_argument(
        "--cache-dir",
        help="Optional download cache directory passed through to build_oui_snapshot.py when using --download-official.",
    )
    return parser


def _resolve_local_ieee_sources(ieee_dir: Path) -> list[Path]:
    paths = [ieee_dir / name for name in DEFAULT_IEEE_FILES]
    missing = [path for path in paths if not path.exists()]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise SystemExit(
            "Missing IEEE source file(s): "
            f"{missing_text}. Install ieee-data or rerun with --download-official."
        )
    return paths


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = [sys.executable, str(BUILD_SCRIPT)]

    if args.download_official:
        command.append("--download-official")
        if args.cache_dir:
            command.extend(["--cache-dir", str(Path(args.cache_dir).expanduser())])
    else:
        ieee_dir = Path(args.ieee_dir).expanduser()
        for path in _resolve_local_ieee_sources(ieee_dir):
            command.extend(["--input", str(path)])

    for vendor_export in args.vendor_exports:
        vendor_path = Path(vendor_export).expanduser()
        if not vendor_path.exists():
            raise SystemExit(f"Missing vendor export file: {vendor_path}")
        command.extend(["--input", str(vendor_path)])

    output_path = Path(args.output).expanduser()
    command.extend(["--output", str(output_path)])
    subprocess.run(command, check=True, cwd=PROJECT_ROOT)
    print(f"Merged {len(args.vendor_exports)} vendor export file(s) into {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
