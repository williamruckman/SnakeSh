from __future__ import annotations

from PySide6.QtGui import QImage

from snakesh.ui.sixel import SixelStream, decode_sixel, MAX_PAYLOAD
from snakesh.ui.main_window import VT100Emulator


def test_incremental_sixel_keeps_surrounding_text_and_pixels():
    data = 'before\x1bP0;1q#1;2;100;0;0!3~\x1b\\after'
    for step in (1, 3, len(data)):
        stream = SixelStream()
        events = []
        for offset in range(0, len(data), step):
            events.extend(stream.feed(data[offset:offset + step]))
        assert ''.join(e for e in events if isinstance(e, str)) == 'beforeafter'
        images = [e for e in events if isinstance(e, QImage)]
        assert len(images) == 1
        image = images[0]
        assert (image.width(), image.height()) == (3, 6)
        assert image.pixelColor(2, 5).getRgb() == (255, 0, 0, 255)


def test_raster_transparency_and_color_planes():
    image = decode_sixel('"1;1;4;6#1;2;100;0;0@$#2;2;0;100;0A', transparent=True)
    assert image.pixelColor(0, 0).getRgb() == (255, 0, 0, 255)
    assert image.pixelColor(0, 1).getRgb() == (0, 255, 0, 255)
    assert image.pixelColor(3, 5).alpha() == 0


def test_hls_color_definition():
    image = decode_sixel('#1;1;120;50;100@')
    assert image.pixelColor(0, 0).getRgb() == (255, 0, 0, 255)


def test_bad_or_oversized_graphics_are_bounded():
    assert decode_sixel('!65535~') is None
    assert decode_sixel('"1;1;65535;65535~') is None
    assert decode_sixel('#999;2;100;0;0~') is None
    stream = SixelStream()
    assert list(stream.feed('\x1bPq' + '~' * (MAX_PAYLOAD + 1))) == []
    assert len(stream.pending) <= 1
    assert list(stream.feed('\x1b\\ok')) == ['ok']


def test_cancel_and_non_graphic_dcs_never_print_escape_payloads():
    stream = SixelStream()
    assert list(stream.feed('\x1bPq#1\x18ok')) == ['ok']
    assert list(stream.feed('\x90unsupported\x9ctext')) == ['text']


def test_emulator_places_scrolls_and_clears_graphics():
    emulator = VT100Emulator(40, 10)
    emulator.cell_height = 6
    emulator.feed('\x1bPq#1;2;100;0;0!3~\x1b\\')
    assert len(emulator.screen.graphics) == 1
    graphic = emulator.screen.graphics[0]
    assert (graphic.row, graphic.column) == (0, 0)
    assert emulator.screen.cursor.y == 1
    emulator.feed('\x1b[2J')
    assert emulator.screen.graphics == []
    emulator.feed('\x1bPq~\x1b\\\x1bc')
    assert emulator.screen.graphics == []
