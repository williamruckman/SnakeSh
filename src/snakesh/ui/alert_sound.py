from __future__ import annotations

import array
import math
from pathlib import Path
import tempfile
import time
import wave

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWidgets import QApplication

try:
    from PySide6.QtMultimedia import QSoundEffect
except Exception:
    QSoundEffect = None  # type: ignore[assignment]


_TERMINAL_BELL_WAV = Path(tempfile.gettempdir()) / "snakesh-terminal-bell.wav"


def ensure_terminal_bell_wav() -> Path | None:
    try:
        if _TERMINAL_BELL_WAV.exists() and _TERMINAL_BELL_WAV.stat().st_size > 44:
            try:
                with wave.open(str(_TERMINAL_BELL_WAV), "rb") as existing:
                    if (
                        existing.getnchannels() == 2
                        and existing.getsampwidth() == 2
                        and existing.getframerate() == 44100
                    ):
                        return _TERMINAL_BELL_WAV
            except Exception:
                pass
        _TERMINAL_BELL_WAV.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 44100
        duration_seconds = 0.11
        frequency_hz = 1080.0
        total_samples = max(1, int(sample_rate * duration_seconds))
        samples = array.array("h")
        attack = max(1, int(sample_rate * 0.006))
        release = max(1, int(sample_rate * 0.04))
        amplitude = 10000

        for index in range(total_samples):
            envelope = 1.0
            if index < attack:
                envelope = index / attack
            elif index > total_samples - release:
                envelope = max(0.0, (total_samples - index) / release)
            phase = (2.0 * math.pi * frequency_hz * index) / sample_rate
            value = int(math.sin(phase) * amplitude * envelope)
            samples.append(value)
            samples.append(value)

        with wave.open(str(_TERMINAL_BELL_WAV), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(samples.tobytes())
        return _TERMINAL_BELL_WAV
    except Exception:
        return None


class BellSoundPlayer(QObject):
    _DEBOUNCE_SECONDS = 0.12
    _SOUND_VOLUME = 0.6
    _LOAD_RETRY_INTERVAL_MS = 50
    _LOAD_RETRY_ATTEMPTS = 12

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._last_play_time = 0.0
        self._sound_effect = None
        self._sound_effect_initialized = False
        self._pending_sound_effect_play = False
        self._sound_retry_attempts_remaining = 0
        self._sound_retry_timer = QTimer(self)
        self._sound_retry_timer.setSingleShot(True)
        self._sound_retry_timer.timeout.connect(self._retry_pending_sound)

    def play(self) -> None:
        now = time.monotonic()
        if (now - self._last_play_time) < self._DEBOUNCE_SECONDS:
            return
        self._last_play_time = now
        if self._play_sound_effect():
            return
        self._fallback_app_beep()

    def _play_sound_effect(self) -> bool:
        effect = self._get_or_create_sound_effect()
        if effect is None:
            return False
        if self._try_play_sound_effect(effect):
            return True
        self._queue_sound_retry()
        return True

    def _try_play_sound_effect(self, effect) -> bool:
        try:
            is_loaded = getattr(effect, "isLoaded", None)
            loaded = bool(is_loaded()) if callable(is_loaded) else True
            if not loaded:
                return False
            effect.stop()
            effect.play()
            self._pending_sound_effect_play = False
            self._sound_retry_attempts_remaining = 0
            return True
        except Exception:
            return False

    def _queue_sound_retry(self) -> None:
        self._pending_sound_effect_play = True
        self._sound_retry_attempts_remaining = self._LOAD_RETRY_ATTEMPTS
        if not self._sound_retry_timer.isActive():
            self._sound_retry_timer.start(self._LOAD_RETRY_INTERVAL_MS)

    def _retry_pending_sound(self) -> None:
        if not self._pending_sound_effect_play:
            return
        effect = self._get_or_create_sound_effect()
        if effect is None:
            self._pending_sound_effect_play = False
            self._fallback_app_beep()
            return
        if self._try_play_sound_effect(effect):
            return
        self._sound_retry_attempts_remaining = max(0, self._sound_retry_attempts_remaining - 1)
        if self._sound_retry_attempts_remaining > 0:
            self._sound_retry_timer.start(self._LOAD_RETRY_INTERVAL_MS)
            return
        self._pending_sound_effect_play = False
        self._fallback_app_beep()

    @staticmethod
    def _fallback_app_beep() -> None:
        app = QApplication.instance()
        if app is None:
            return
        try:
            app.beep()
        except Exception:
            return

    @classmethod
    def _create_sound_effect(cls):
        if QSoundEffect is None:
            return None
        app = QApplication.instance()
        if app is None:
            return None
        sound_path = ensure_terminal_bell_wav()
        if sound_path is None:
            return None
        try:
            effect = QSoundEffect(app)
            effect.setLoopCount(1)
            effect.setVolume(cls._SOUND_VOLUME)
            effect.setSource(QUrl.fromLocalFile(str(sound_path)))
            return effect
        except Exception:
            return None

    def _get_or_create_sound_effect(self):
        if self._sound_effect_initialized:
            return self._sound_effect
        self._sound_effect_initialized = True
        self._sound_effect = self._create_sound_effect()
        return self._sound_effect
