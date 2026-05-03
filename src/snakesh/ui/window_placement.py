from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QByteArray, QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(slots=True)
class WindowPlacement:
    geometry: QByteArray | None = None
    screen_name: str = ""
    screen_serial: str = ""
    frame_rect: QRect = field(default_factory=QRect)

    def has_data(self) -> bool:
        return bool(
            (self.geometry is not None and not self.geometry.isEmpty())
            or self.screen_name
            or self.screen_serial
            or self.frame_rect.isValid()
        )


def copy_geometry_bytes(value: QByteArray | None) -> QByteArray | None:
    if value is None:
        return None
    try:
        copied = QByteArray(value)
    except Exception:
        return None
    return copied if not copied.isEmpty() else None


def geometry_to_b64(value: QByteArray | None) -> str:
    geometry = copy_geometry_bytes(value)
    if geometry is None:
        return ""
    try:
        return bytes(geometry.toBase64()).decode("ascii")
    except Exception:
        return ""


def geometry_from_b64(encoded: str) -> QByteArray | None:
    cleaned = str(encoded).strip()
    if not cleaned:
        return None
    try:
        geometry = QByteArray.fromBase64(cleaned.encode("ascii"))
    except Exception:
        return None
    return geometry if not geometry.isEmpty() else None


def screen_name(screen: object | None) -> str:
    if screen is None:
        return ""
    getter = getattr(screen, "name", None)
    if not callable(getter):
        return ""
    try:
        return str(getter()).strip()
    except Exception:
        return ""


def screen_serial(screen: object | None) -> str:
    if screen is None:
        return ""
    getter = getattr(screen, "serialNumber", None)
    if not callable(getter):
        return ""
    try:
        return str(getter()).strip()
    except Exception:
        return ""


def frame_rect_to_list(rect: QRect) -> list[int]:
    if not rect.isValid():
        return []
    return [int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())]


def frame_rect_from_list(value: object) -> QRect:
    if not isinstance(value, list) or len(value) != 4:
        return QRect()
    try:
        x, y, width, height = (int(item) for item in value)
    except (TypeError, ValueError):
        return QRect()
    if width <= 0 or height <= 0:
        return QRect()
    return QRect(x, y, width, height)


def placement_to_payload(placement: WindowPlacement) -> dict[str, object]:
    return {
        "geometry_b64": geometry_to_b64(placement.geometry),
        "screen_name": placement.screen_name,
        "screen_serial": placement.screen_serial,
        "frame_rect": frame_rect_to_list(placement.frame_rect),
    }


def placement_from_payload(payload: object) -> WindowPlacement | None:
    if not isinstance(payload, dict):
        return None
    placement = WindowPlacement(
        geometry=geometry_from_b64(str(payload.get("geometry_b64", "")).strip()),
        screen_name=str(payload.get("screen_name", "")).strip(),
        screen_serial=str(payload.get("screen_serial", "")).strip(),
        frame_rect=frame_rect_from_list(payload.get("frame_rect")),
    )
    return placement if placement.has_data() else None


def window_screen(window: QWidget) -> object | None:
    handle_getter = getattr(window, "windowHandle", None)
    if callable(handle_getter):
        try:
            handle = handle_getter()
        except Exception:
            handle = None
        if handle is not None:
            handle_screen = getattr(handle, "screen", None)
            if callable(handle_screen):
                try:
                    screen = handle_screen()
                except Exception:
                    screen = None
                if screen is not None:
                    return screen
    screen_getter = getattr(window, "screen", None)
    if callable(screen_getter):
        try:
            screen = screen_getter()
        except Exception:
            screen = None
        if screen is not None:
            return screen
    try:
        frame = window.frameGeometry()
    except Exception:
        frame = QRect()
    if frame.isValid():
        try:
            screen = QApplication.screenAt(frame.center())
        except Exception:
            screen = None
        if screen is not None:
            return screen
    try:
        return QApplication.primaryScreen()
    except Exception:
        return None


def capture_window_placement(
    window: QWidget,
    *,
    geometry_override: QByteArray | None = None,
    frame_rect_override: QRect | None = None,
    screen_override: object | None = None,
) -> WindowPlacement:
    geometry = copy_geometry_bytes(geometry_override)
    if geometry is None:
        save_geometry = getattr(window, "saveGeometry", None)
        if callable(save_geometry):
            try:
                geometry = copy_geometry_bytes(save_geometry())
            except Exception:
                geometry = None
    frame_rect = QRect()
    if frame_rect_override is not None and frame_rect_override.isValid():
        frame_rect = QRect(frame_rect_override)
    else:
        try:
            current_frame = window.frameGeometry()
        except Exception:
            current_frame = QRect()
        if current_frame.isValid():
            frame_rect = QRect(current_frame)
    screen = screen_override
    if screen is None and frame_rect.isValid():
        try:
            screen = QApplication.screenAt(frame_rect.center())
        except Exception:
            screen = None
    if screen is None:
        screen = window_screen(window)
    return WindowPlacement(
        geometry=geometry,
        screen_name=screen_name(screen),
        screen_serial=screen_serial(screen),
        frame_rect=frame_rect,
    )


def resolve_screen_for_window_placement(placement: WindowPlacement) -> object | None:
    try:
        screens = list(QApplication.screens())
    except Exception:
        screens = []
    if placement.screen_serial:
        for screen in screens:
            if screen_serial(screen) == placement.screen_serial:
                return screen
    if placement.screen_name:
        for screen in screens:
            if screen_name(screen) == placement.screen_name:
                return screen
    if placement.frame_rect.isValid():
        try:
            screen = QApplication.screenAt(placement.frame_rect.center())
        except Exception:
            screen = None
        if screen is not None:
            return screen
    try:
        return QApplication.primaryScreen()
    except Exception:
        return None


def window_is_maximized_or_fullscreen(window: QWidget) -> bool:
    try:
        if window.isMaximized() or window.isFullScreen():
            return True
    except Exception:
        pass
    state_getter = getattr(window, "windowState", None)
    if not callable(state_getter):
        return False
    try:
        state = state_getter()
    except Exception:
        return False
    state_value = getattr(state, "value", state)
    try:
        normalized = int(state_value)
    except (TypeError, ValueError):
        return False
    return bool(
        normalized
        & (
            int(Qt.WindowState.WindowMaximized.value)
            | int(Qt.WindowState.WindowFullScreen.value)
        )
    )


def clamp_top_level_window_to_screen(window: QWidget, screen: object | None) -> None:
    if screen is None:
        return
    available_getter = getattr(screen, "availableGeometry", None)
    if not callable(available_getter):
        return
    try:
        available = available_getter()
    except Exception:
        return
    if not isinstance(available, QRect) or not available.isValid():
        return
    try:
        target_width = max(1, min(int(window.width()), available.width()))
        target_height = max(1, min(int(window.height()), available.height()))
        window.resize(target_width, target_height)
    except Exception:
        pass
    try:
        frame = window.frameGeometry()
    except Exception:
        frame = QRect()
    if not frame.isValid():
        return
    max_x = available.right() - frame.width() + 1
    max_y = available.bottom() - frame.height() + 1
    target_x = min(max(frame.x(), available.x()), max_x)
    target_y = min(max(frame.y(), available.y()), max_y)
    if target_x == frame.x() and target_y == frame.y():
        return
    try:
        window.move(target_x, target_y)
    except Exception:
        pass


def apply_window_placement(window: QWidget, placement: WindowPlacement) -> bool:
    if not placement.has_data():
        return False
    handle_getter = getattr(window, "windowHandle", None)
    handle = handle_getter() if callable(handle_getter) else None
    if handle is None:
        return False
    screen = resolve_screen_for_window_placement(placement)
    set_screen = getattr(handle, "setScreen", None)
    if screen is not None and callable(set_screen):
        try:
            set_screen(screen)
        except Exception:
            pass
    if placement.geometry is not None and not placement.geometry.isEmpty():
        try:
            window.restoreGeometry(placement.geometry)
        except Exception:
            return False
    elif placement.frame_rect.isValid():
        try:
            window.setGeometry(placement.frame_rect)
        except Exception:
            return False
    else:
        return False
    if not window_is_maximized_or_fullscreen(window):
        clamp_top_level_window_to_screen(window, screen)
    return True


def restore_or_defer_window_placement(window: QWidget, placement: WindowPlacement | None) -> bool:
    if placement is None or not placement.has_data():
        setattr(window, "_pending_window_placement", None)
        return False
    if apply_window_placement(window, placement):
        setattr(window, "_pending_window_placement", None)
        return True
    setattr(window, "_pending_window_placement", placement)
    return False


def apply_pending_window_placement(window: QWidget) -> bool:
    placement = getattr(window, "_pending_window_placement", None)
    if not isinstance(placement, WindowPlacement):
        return False
    if apply_window_placement(window, placement):
        setattr(window, "_pending_window_placement", None)
        return True
    return False
