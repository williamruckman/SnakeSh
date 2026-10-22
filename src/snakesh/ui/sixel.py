"""Bounded, incremental DEC Sixel graphics for the terminal canvas.

No external decoder or executable is required. Limits apply before allocation
and to repeat/paint work, including malformed remote input.
"""
from __future__ import annotations

import colorsys
from dataclasses import dataclass
import re

from PySide6.QtGui import QImage

MAX_SIDE = 2048
MAX_PAYLOAD = 2 * 1024 * 1024
MAX_PAINT_PIXELS = 16 * 1024 * 1024
_PARAMETERS = re.compile(r"[0-9;]*")


@dataclass
class TerminalGraphic:
    image: QImage
    column: int
    row: int
    rows: int


def decode_sixel(payload: str, *, transparent: bool = False) -> QImage | None:
    if len(payload) > MAX_PAYLOAD:
        return None
    # Measure and validate before allocating. Keeping a Python tuple for every
    # drawing command both wasted memory and rejected ordinary detailed images
    # after 100,000 commands, even when well within the pixel/work limits.
    measured = _sixel_pass(payload)
    if measured is None:
        return None
    width, height, background_rgb = measured
    if not width or not height:
        return None
    background = bytes((*background_rgb, 0 if transparent else 255))
    pixels = bytearray(background * (width * height))
    _sixel_pass(payload, pixels=pixels, stride=width)
    return QImage(bytes(pixels), width, height, width * 4, QImage.Format_RGBA8888).copy()


def _sixel_pass(payload: str, *, pixels: bytearray | None = None,
                stride: int = 0) -> tuple[int, int, tuple[int, int, int]] | None:
    palette = [(0, 0, 0), (51, 51, 204), (204, 33, 33), (51, 204, 51),
               (204, 51, 204), (51, 204, 204), (204, 204, 51), (136, 136, 136),
               (68, 68, 68), (85, 85, 255), (255, 85, 85), (85, 255, 85),
               (255, 85, 255), (85, 255, 255), (255, 255, 85), (255, 255, 255)]
    palette += [(0, 0, 0)] * (256 - len(palette))
    x = y = width = height = index = paint_work = 0
    color = 7
    while index < len(payload):
        char = payload[index]
        index += 1
        repeat = 1
        if char in '!#"':
            match = _PARAMETERS.match(payload, index)
            fields = match.group()
            index += len(fields)
            try:
                values = [int(value or '0') for value in fields.split(';')]
            except ValueError:
                return None
            if any(value > 65535 for value in values):
                return None
            if char == '#':
                color = values[0]
                if color >= len(palette):
                    return None
                if len(values) >= 5:
                    mode, a, b, c = values[1:5]
                    if mode == 2:
                        if max(a, b, c) > 100:
                            return None
                        palette[color] = tuple(round(value * 255 / 100) for value in (a, b, c))
                    elif mode == 1:
                        if a > 360 or max(b, c) > 100:
                            return None
                        palette[color] = tuple(round(value * 255) for value in
                            colorsys.hls_to_rgb(((a + 240) % 360) / 360, b / 100, c / 100))
                continue
            if char == '"':
                if len(values) >= 4:
                    width, height = max(width, values[2]), max(height, values[3])
                    if width > MAX_SIDE or height > MAX_SIDE:
                        return None
                continue
            repeat = max(1, values[0])
            if index >= len(payload):
                return None
            char = payload[index]
            index += 1
        if char == '$':
            x = 0
        elif char == '-':
            x = 0
            y += 6
        elif '?' <= char <= '~':
            bits = ord(char) - 63
            if x + repeat > MAX_SIDE or y + 6 > MAX_SIDE:
                return None
            paint_work += repeat * bits.bit_count()
            if paint_work > MAX_PAINT_PIXELS:
                return None
            if pixels is not None and bits:
                stripe = bytes((*palette[color], 255)) * repeat
                for bit in range(6):
                    if bits & (1 << bit):
                        start = ((y + bit) * stride + x) * 4
                        pixels[start:start + len(stripe)] = stripe
            x += repeat
            width = max(width, x)
            height = max(height, y + max(1, bits.bit_length()))
    return width, height, palette[0]


class SixelStream:
    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled
        self.pending = ""
        self.in_dcs = False
        self.discard = False

    def feed(self, text: str):
        """Yield text and image events in their original terminal order."""
        self.pending += text
        while self.pending:
            if not self.in_dcs:
                start = re.search(r"\x1bP|\x90", self.pending)
                if start is None:
                    keep = 1 if self.pending.endswith('\x1b') else 0
                    chunk = self.pending[:-keep] if keep else self.pending
                    self.pending = self.pending[-keep:] if keep else ''
                    if chunk:
                        yield chunk
                    return
                if start.start():
                    yield self.pending[:start.start()]
                self.pending = self.pending[start.end():]
                self.in_dcs = True
            end = re.search(r"\x1b\\|\x9c|\x18|\x1a", self.pending)
            if end is None:
                if len(self.pending) > MAX_PAYLOAD:
                    self.discard = True
                    self.pending = '\x1b' if self.pending.endswith('\x1b') else ''
                return
            body = self.pending[:end.start()]
            self.pending = self.pending[end.end():]
            if self.enabled and not self.discard and end.group() not in ('\x18', '\x1a'):
                header = re.match(r"([0-9;]*)q", body)
                if header:
                    params = header.group(1).split(';')
                    transparent = len(params) > 1 and params[1] == '1'
                    image = decode_sixel(body[header.end():], transparent=transparent)
                    if image is not None:
                        yield image
            self.in_dcs = False
            self.discard = False
