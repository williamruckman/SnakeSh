from __future__ import annotations

import re
import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from snakesh.services.settings_service import AppSettings

_CHECKBOX_ICON_PATH_CACHE: dict[str, str] = {}
_CLOSE_ICON_PATH_CACHE: dict[str, str] = {}
_ARROW_ICON_PATH_CACHE: dict[tuple[str, str], str] = {}
_PREFERRED_TERMINAL_FONT_FAMILIES = (
    "Courier New",
    "Consolas",
    "Cascadia Mono",
    "Cascadia Code",
    "Lucida Console",
    "Courier",
    "Liberation Mono",
    "DejaVu Sans Mono",
)


def _checked_checkbox_icon_path(text_color: str) -> str:
    color = text_color.strip() or "#111827"
    cached = _CHECKBOX_ICON_PATH_CACHE.get(color)
    if cached and Path(cached).exists():
        return cached
    token = re.sub(r"[^0-9A-Za-z]+", "_", color).strip("_") or "default"
    cache_dir = Path(tempfile.gettempdir()) / "snakesh-ui-cache"
    icon_path = cache_dir / f"checkbox-x-{token}.svg"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
        f'<path d="M3.25 3.25L10.75 10.75M10.75 3.25L3.25 10.75" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
        "</svg>"
    )
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        if not icon_path.exists():
            icon_path.write_text(svg, encoding="utf-8")
    except Exception:
        # Keep stylesheet valid even if cache write fails.
        return ""
    resolved = icon_path.as_posix()
    _CHECKBOX_ICON_PATH_CACHE[color] = resolved
    return resolved


def blend_colors(base: str, tint: str, ratio: float) -> str:
    base_color = QColor(base.strip() or "#ffffff")
    tint_color = QColor(tint.strip() or "#111827")
    if not base_color.isValid():
        base_color = QColor("#ffffff")
    if not tint_color.isValid():
        tint_color = QColor("#111827")
    amount = max(0.0, min(1.0, ratio))
    red = round(base_color.red() + (tint_color.red() - base_color.red()) * amount)
    green = round(base_color.green() + (tint_color.green() - base_color.green()) * amount)
    blue = round(base_color.blue() + (tint_color.blue() - base_color.blue()) * amount)
    return QColor(red, green, blue).name()


def _relative_luminance(color: QColor) -> float:
    def _channel(value: int) -> float:
        normalized = max(0.0, min(1.0, value / 255.0))
        if normalized <= 0.03928:
            return normalized / 12.92
        return ((normalized + 0.055) / 1.055) ** 2.4

    return (
        (0.2126 * _channel(color.red()))
        + (0.7152 * _channel(color.green()))
        + (0.0722 * _channel(color.blue()))
    )


def contrast_ratio(foreground: str, background: str) -> float:
    fg = QColor(foreground.strip() or "#ffffff")
    bg = QColor(background.strip() or "#000000")
    if not fg.isValid():
        fg = QColor("#ffffff")
    if not bg.isValid():
        bg = QColor("#000000")
    lighter = max(_relative_luminance(fg), _relative_luminance(bg))
    darker = min(_relative_luminance(fg), _relative_luminance(bg))
    return (lighter + 0.05) / (darker + 0.05)


def readable_foreground_color(foreground: str, background: str, *, minimum_ratio: float = 3.0) -> str:
    fg = QColor(foreground.strip() or "#ffffff")
    bg = QColor(background.strip() or "#000000")
    if not fg.isValid():
        fg = QColor("#ffffff")
    if not bg.isValid():
        bg = QColor("#000000")

    current = fg.name()
    if contrast_ratio(current, bg.name()) >= minimum_ratio:
        return current

    white = "#ffffff"
    black = "#000000"
    return white if contrast_ratio(white, bg.name()) >= contrast_ratio(black, bg.name()) else black


def close_icon_path(stroke_color: str) -> str:
    normalized = QColor(stroke_color.strip() or "#111827")
    if not normalized.isValid():
        normalized = QColor("#111827")
    color_name = normalized.name()
    cached = _CLOSE_ICON_PATH_CACHE.get(color_name)
    if cached and Path(cached).exists():
        return cached
    token = re.sub(r"[^0-9A-Za-z]+", "_", color_name).strip("_") or "default"
    cache_dir = Path(tempfile.gettempdir()) / "snakesh-ui-cache"
    icon_path = cache_dir / f"tab-close-{token}.svg"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12">'
        f'<path d="M3 3L9 9M9 3L3 9" stroke="{color_name}" stroke-width="1.8" stroke-linecap="round"/>'
        "</svg>"
    )
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        if not icon_path.exists():
            icon_path.write_text(svg, encoding="utf-8")
    except Exception:
        return ""
    resolved = icon_path.as_posix()
    _CLOSE_ICON_PATH_CACHE[color_name] = resolved
    return resolved


def _arrow_icon_path(direction: str, stroke_color: str) -> str:
    normalized_direction = direction.strip().lower()
    if normalized_direction not in {"up", "down"}:
        return ""
    normalized = QColor(stroke_color.strip() or "#111827")
    if not normalized.isValid():
        normalized = QColor("#111827")
    color_name = normalized.name()
    cache_key = (normalized_direction, color_name)
    cached = _ARROW_ICON_PATH_CACHE.get(cache_key)
    if cached and Path(cached).exists():
        return cached

    token = re.sub(r"[^0-9A-Za-z]+", "_", color_name).strip("_") or "default"
    cache_dir = Path(tempfile.gettempdir()) / "snakesh-ui-cache"
    icon_path = cache_dir / f"arrow-{normalized_direction}-{token}.svg"
    polyline = "2,6.5 5,3.5 8,6.5" if normalized_direction == "up" else "2,3.5 5,6.5 8,3.5"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
        f'<polyline points="{polyline}" fill="none" stroke="{color_name}" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    )
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        if not icon_path.exists():
            icon_path.write_text(svg, encoding="utf-8")
    except Exception:
        return ""
    resolved = icon_path.as_posix()
    _ARROW_ICON_PATH_CACHE[cache_key] = resolved
    return resolved


def build_terminal_output_font(settings: AppSettings) -> QFont:
    desired = (settings.terminal_font_family or "").strip()
    families = [
        name for name in QFontDatabase.families() if QFontDatabase.isFixedPitch(name) and name.lower() != "fixedsys"
    ]

    ordered: list[str] = []
    for name in _PREFERRED_TERMINAL_FONT_FAMILIES:
        if name in families and name not in ordered:
            ordered.append(name)
    for name in families:
        if name not in ordered:
            ordered.append(name)

    family = ""
    if desired:
        for name in ordered:
            if name.lower() == desired.lower():
                family = name
                break
    if not family and ordered:
        family = ordered[0]
    if not family:
        family = "Courier New"

    font = QFont()
    font.setFamily(family)
    font.setPointSize(max(8, settings.terminal_font_pt))
    font.setKerning(False)
    return font


def apply_terminal_output_font(widget: QWidget, settings: AppSettings) -> QFont:
    font = build_terminal_output_font(settings)
    widget.setFont(QFont(font))
    return font


def apply_theme(app: QApplication, settings: AppSettings | None = None) -> None:
    s = settings or AppSettings.defaults()
    checkbox_checked_icon = _checked_checkbox_icon_path(s.text_color)
    checked_indicator_image = f'image: url("{checkbox_checked_icon}");' if checkbox_checked_icon else ""
    subtle_alt_row_bg = blend_colors(s.field_bg, s.text_color, 0.05)
    field_control_bg = blend_colors(s.field_bg, s.text_color, 0.08)
    field_control_hover_bg = blend_colors(s.field_bg, s.text_color, 0.14)
    field_control_icon_color = readable_foreground_color(s.text_color, field_control_bg, minimum_ratio=4.5)
    down_arrow_icon = _arrow_icon_path("down", field_control_icon_color)
    up_arrow_icon = _arrow_icon_path("up", field_control_icon_color)
    down_arrow_image = f'image: url("{down_arrow_icon}");' if down_arrow_icon else ""
    up_arrow_image = f'image: url("{up_arrow_icon}");' if up_arrow_icon else ""
    app.setStyleSheet(
        f"""
        QWidget {{
            background-color: {s.app_bg_start};
            color: {s.text_color};
            font-family: "Segoe UI", "Noto Sans", sans-serif;
            font-size: 10pt;
        }}
        QMainWindow {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                        stop:0 {s.app_bg_start}, stop:1 {s.app_bg_end});
            border: 1px solid {s.field_border};
        }}
        QWidget#mainWindowRoot {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                        stop:0 {s.app_bg_start}, stop:1 {s.app_bg_end});
            border: 1px solid {s.field_border};
        }}
        QDialog {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                        stop:0 {s.app_bg_start}, stop:1 {s.app_bg_end});
            border: 1px solid {s.field_border};
        }}
        QPushButton {{
            background-color: {s.accent_color};
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: {s.accent_hover};
        }}
        QPushButton:pressed {{
            background-color: {s.accent_pressed};
        }}
        QListWidget, QTreeWidget, QLineEdit, QTextEdit, QComboBox, QSpinBox {{
            background-color: {s.field_bg};
            border: 1px solid {s.field_border};
            border-radius: 8px;
            padding: 6px;
        }}
        QComboBox, QSpinBox {{
            padding-right: 28px;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 24px;
            border-left: 1px solid {s.field_border};
            background-color: {field_control_bg};
            border-top-right-radius: 8px;
            border-bottom-right-radius: 8px;
        }}
        QComboBox::drop-down:hover {{
            background-color: {field_control_hover_bg};
        }}
        QComboBox::down-arrow {{
            width: 10px;
            height: 10px;
            {down_arrow_image}
        }}
        QSpinBox::up-arrow {{
            width: 10px;
            height: 10px;
            {up_arrow_image}
        }}
        QSpinBox::down-arrow {{
            width: 10px;
            height: 10px;
            {down_arrow_image}
        }}
        QSpinBox::up-button {{
            subcontrol-origin: border;
            subcontrol-position: top right;
            width: 24px;
            border-left: 1px solid {s.field_border};
            border-top-right-radius: 8px;
            background-color: {field_control_bg};
        }}
        QSpinBox::up-button:hover {{
            background-color: {field_control_hover_bg};
        }}
        QSpinBox::down-button {{
            subcontrol-origin: border;
            subcontrol-position: bottom right;
            width: 24px;
            border-left: 1px solid {s.field_border};
            border-top: 1px solid {s.field_border};
            border-bottom-right-radius: 8px;
            background-color: {field_control_bg};
        }}
        QSpinBox::down-button:hover {{
            background-color: {field_control_hover_bg};
        }}
        QListWidget, QTreeWidget {{
            alternate-background-color: {subtle_alt_row_bg};
        }}
        QCheckBox {{
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {s.field_border};
            border-radius: 3px;
            background-color: {s.field_bg};
        }}
        QCheckBox::indicator:checked {{
            border: 1px solid {s.accent_hover};
            background-color: {s.field_bg};
            {checked_indicator_image}
        }}
        QCheckBox::indicator:unchecked:hover {{
            border: 1px solid {s.accent_hover};
        }}
        QStatusBar {{
            border-top: 1px solid {s.field_border};
            min-height: 18px;
            padding: 0px 4px;
        }}
        QStatusBar::item {{
            border: none;
        }}
        QWidget#footerCommandBar QLineEdit {{
            padding: 4px 6px;
            min-height: 22px;
            border-radius: 6px;
        }}
        QWidget#footerCommandBar QPushButton {{
            padding: 6px 12px;
            min-height: 24px;
            border-radius: 6px;
        }}
        QWidget#statusProgressWidget QLabel {{
            padding: 0px;
        }}
        QWidget#statusProgressWidget QProgressBar {{
            min-height: 16px;
            max-height: 18px;
        }}
        QWidget#statusProgressWidget QPushButton {{
            padding: 4px 10px;
            min-height: 22px;
            border-radius: 6px;
        }}
        """
    )
