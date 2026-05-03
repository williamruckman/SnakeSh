from __future__ import annotations

import base64
import codecs
import json
import random
import unittest
from pathlib import Path
from unittest.mock import patch

from snakesh.ui.main_window import TERMINAL_DEBUG_UNKNOWN_SEQUENCES_ENV, VT100Emulator, _TerminalFormattingCompactor


_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "terminal_traces"
_TRACE_FIXTURES = ("nano", "less", "vim", "man", "htop")


class TerminalTraceReplayTests(unittest.TestCase):
    def test_replay_fixtures_match_expected_screen_for_all_chunk_strategies(self) -> None:
        for fixture_name in _TRACE_FIXTURES:
            meta, events, expected_display = self._load_fixture(fixture_name)
            expected_cursor = (int(meta["expected_cursor_row"]), int(meta["expected_cursor_col"]))
            for strategy in ("recorded", "byte", "random"):
                with self.subTest(fixture=fixture_name, strategy=strategy):
                    emulator = self._replay_fixture(meta=meta, events=events, strategy=strategy)
                    self.assertEqual(list(emulator.screen.display), expected_display)
                    self.assertEqual((int(emulator.screen.cursor.y), int(emulator.screen.cursor.x)), expected_cursor)

    def test_fast_parser_matches_fallback_parser_for_all_fixtures(self) -> None:
        for fixture_name in _TRACE_FIXTURES:
            meta, events, _expected_display = self._load_fixture(fixture_name)
            for strategy in ("recorded", "byte", "random"):
                with self.subTest(fixture=fixture_name, strategy=strategy):
                    fast = self._replay_fixture(meta=meta, events=events, strategy=strategy, enable_fast_parser=True)
                    fallback = self._replay_fixture(meta=meta, events=events, strategy=strategy, enable_fast_parser=False)
                    self.assertEqual(list(fast.screen.display), list(fallback.screen.display))
                    self.assertEqual(
                        (int(fast.screen.cursor.y), int(fast.screen.cursor.x)),
                        (int(fallback.screen.cursor.y), int(fallback.screen.cursor.x)),
                    )

    def test_unknown_sequence_logging_records_counts_once_per_key(self) -> None:
        with (
            patch.dict("snakesh.ui.main_window.os.environ", {TERMINAL_DEBUG_UNKNOWN_SEQUENCES_ENV: "1"}, clear=False),
            patch("snakesh.ui.main_window._LOGGER.warning") as warning,
        ):
            emulator = VT100Emulator(cols=8, rows=4, history=100)
            emulator.feed("\x1b[?1z")
            emulator.feed("\x1b[?2z")
            emulator.feed("\x1bZ")
            emulator.feed("\x1b#7")

        self.assertEqual(emulator.unknown_sequence_counts()[("CSI", "?", "z")], 2)
        self.assertEqual(emulator.unknown_sequence_counts()[("ESC", "", "Z")], 1)
        self.assertEqual(emulator.unknown_sequence_counts()[("SHARP", "", "7")], 1)
        self.assertEqual(warning.call_count, 3)
        self.assertEqual(list(emulator.screen.display), [" " * 8] * 4)

    def test_unknown_sequence_logging_handles_split_sequences_across_feed_calls(self) -> None:
        with patch.dict("snakesh.ui.main_window.os.environ", {TERMINAL_DEBUG_UNKNOWN_SEQUENCES_ENV: "1"}, clear=False):
            emulator = VT100Emulator(cols=8, rows=4, history=100)
            emulator.feed("\x1b[?")
            emulator.feed("12z")
            emulator.feed("\x1b")
            emulator.feed("Z")

        self.assertEqual(emulator.unknown_sequence_counts()[("CSI", "?", "z")], 1)
        self.assertEqual(emulator.unknown_sequence_counts()[("ESC", "", "Z")], 1)

    def test_formatting_compactor_merges_redundant_sgr_and_charset_sequences_across_chunks(self) -> None:
        compactor = _TerminalFormattingCompactor()

        first = compactor.feed("\x1b(B\x1b[0;")
        second = compactor.feed("1m\x1b[36mA\x1b[39m\x1b(B\x1b[m")
        third = compactor.feed("B")

        self.assertEqual(first, "")
        self.assertEqual(second, "\x1b[0;1;36mA")
        self.assertEqual(third, "\x1b[mB")

    def _load_fixture(self, fixture_name: str) -> tuple[dict[str, object], list[dict[str, object]], list[str]]:
        trace_path = _FIXTURE_DIR / f"{fixture_name}.jsonl"
        expected_path = _FIXTURE_DIR / f"{fixture_name}.expected.txt"
        rows = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        meta = rows[0]
        events = rows[1:]
        expected_display = expected_path.read_text(encoding="utf-8").splitlines()
        return meta, events, expected_display

    def _replay_fixture(
        self,
        *,
        meta: dict[str, object],
        events: list[dict[str, object]],
        strategy: str,
        enable_fast_parser: bool = True,
    ) -> VT100Emulator:
        emulator = VT100Emulator(
            cols=int(meta["cols"]),
            rows=int(meta["rows"]),
            history=5000,
            enable_fast_parser=enable_fast_parser,
        )
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        random_source = random.Random(1337)

        for event in events:
            event_type = str(event["type"])
            if event_type == "resize":
                emulator.resize(int(event["cols"]), int(event["rows"]))
                continue
            if event_type != "output":
                continue
            payload = base64.b64decode(str(event["data_b64"]))
            for chunk in self._chunk_payload(payload, strategy=strategy, random_source=random_source):
                text = decoder.decode(chunk, final=False)
                if text:
                    emulator.feed(text)

        tail = decoder.decode(b"", final=True)
        if tail:
            emulator.feed(tail)
        return emulator

    @staticmethod
    def _chunk_payload(payload: bytes, *, strategy: str, random_source: random.Random) -> list[bytes]:
        if not payload:
            return []
        if strategy == "recorded":
            return [payload]
        if strategy == "byte":
            return [payload[index : index + 1] for index in range(len(payload))]
        if strategy == "random":
            chunks: list[bytes] = []
            index = 0
            while index < len(payload):
                width = random_source.randint(1, min(7, len(payload) - index))
                chunks.append(payload[index : index + width])
                index += width
            return chunks
        raise AssertionError(f"Unexpected chunk strategy: {strategy}")


if __name__ == "__main__":
    unittest.main()
