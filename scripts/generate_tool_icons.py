#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from snakesh.core.tool_registry import TOOL_REGISTRY  # noqa: E402


ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_TYPES_BY_SIZE = (
    (16, b"icp4"),
    (32, b"icp5"),
    (64, b"icp6"),
    (128, b"ic07"),
    (256, b"ic08"),
    (512, b"ic09"),
    (1024, b"ic10"),
)


def _png_bytes(source: QImage, size: int) -> bytes:
    canvas = QImage(size, size, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter = QPainter(canvas)
    try:
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    finally:
        painter.end()

    payload = QByteArray()
    buffer = QBuffer(payload)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Unable to open image output buffer.")
    try:
        if not canvas.save(buffer, "PNG"):
            raise RuntimeError("Unable to encode PNG image.")
    finally:
        buffer.close()
    return bytes(payload)


def _load_source(path: Path) -> QImage:
    image = QImage(str(path))
    if image.isNull():
        raise RuntimeError(f"Unable to load source icon: {path}")
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _write_ico(source: QImage, output_path: Path) -> None:
    images = [_png_bytes(source, size) for size in ICO_SIZES]
    header_size = 6 + (16 * len(images))
    offset = header_size
    entries: list[bytes] = []
    for size, payload in zip(ICO_SIZES, images, strict=True):
        dimension = 0 if size >= 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)
    output_path.write_bytes(
        b"".join(
            (
                struct.pack("<HHH", 0, 1, len(images)),
                *entries,
                *images,
            )
        )
    )


def _write_icns(source: QImage, output_path: Path) -> None:
    chunks: list[bytes] = []
    for size, chunk_type in ICNS_TYPES_BY_SIZE:
        payload = _png_bytes(source, size)
        chunks.append(chunk_type + struct.pack(">I", len(payload) + 8) + payload)
    output_path.write_bytes(
        b"icns"
        + struct.pack(">I", 8 + sum(len(chunk) for chunk in chunks))
        + b"".join(chunks)
    )


def generate_icons(assets_dir: Path) -> None:
    for entry in TOOL_REGISTRY:
        source_path = assets_dir / f"{entry.key}.png"
        if not source_path.exists():
            raise RuntimeError(f"Missing PNG source for {entry.key}: {source_path}")
        source = _load_source(source_path)
        _write_ico(source, assets_dir / f"{entry.key}.ico")
        _write_icns(source, assets_dir / f"{entry.key}.icns")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate per-tool SnakeSh launcher icons.")
    parser.add_argument(
        "--assets-dir",
        type=Path,
        default=PROJECT_ROOT / "src" / "snakesh" / "assets",
        help="Directory containing <tool_key>.png source icons.",
    )
    args = parser.parse_args(argv)
    generate_icons(args.assets_dir.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
