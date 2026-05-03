from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

THEME_COLOR_FIELDS: tuple[str, ...] = (
    "app_bg_start",
    "app_bg_end",
    "text_color",
    "field_bg",
    "field_border",
    "accent_color",
    "accent_hover",
    "accent_pressed",
    "terminal_bg",
    "terminal_fg",
    "tab_active_bg",
    "tab_active_fg",
    "tab_inactive_bg",
    "tab_inactive_fg",
)

DEFAULT_THEME_ID = "onyx"
CUSTOM_THEME_ID = "custom"


@dataclass(frozen=True, slots=True)
class ThemePreset:
    theme_id: str
    label: str
    colors: dict[str, str]


THEME_PRESETS: tuple[ThemePreset, ...] = (
    ThemePreset(
        theme_id="purple",
        label="Violet Viper",
        colors={
            "app_bg_start": "#000000",
            "app_bg_end": "#000000",
            "text_color": "#ffffff",
            "field_bg": "#101010",
            "field_border": "#5f008f",
            "accent_color": "#4d0074",
            "accent_hover": "#5e008d",
            "accent_pressed": "#5e008d",
            "terminal_bg": "#101010",
            "terminal_fg": "#00aa00",
            "tab_active_bg": "#4d0074",
            "tab_active_fg": "#e2e8f0",
            "tab_inactive_bg": "#31004c",
            "tab_inactive_fg": "#666666",
        },
    ),
    ThemePreset(
        theme_id="midnight",
        label="Midnight Blue",
        colors={
            "app_bg_start": "#0b1220",
            "app_bg_end": "#111c32",
            "text_color": "#e5edf8",
            "field_bg": "#162238",
            "field_border": "#2f4f78",
            "accent_color": "#1f6feb",
            "accent_hover": "#2b7bfa",
            "accent_pressed": "#1558be",
            "terminal_bg": "#091120",
            "terminal_fg": "#7dd3fc",
            "tab_active_bg": "#1f6feb",
            "tab_active_fg": "#f8fbff",
            "tab_inactive_bg": "#13213a",
            "tab_inactive_fg": "#a8b7d1",
        },
    ),
    ThemePreset(
        theme_id="slate",
        label="Slate Teal",
        colors={
            "app_bg_start": "#0f1720",
            "app_bg_end": "#15252e",
            "text_color": "#e7f3f1",
            "field_bg": "#1a2f36",
            "field_border": "#3d5f67",
            "accent_color": "#0f766e",
            "accent_hover": "#0f8a81",
            "accent_pressed": "#0c5e58",
            "terminal_bg": "#102126",
            "terminal_fg": "#6ee7b7",
            "tab_active_bg": "#0f766e",
            "tab_active_fg": "#ecfdf8",
            "tab_inactive_bg": "#18323a",
            "tab_inactive_fg": "#9eb7b3",
        },
    ),
    ThemePreset(
        theme_id="evergreen",
        label="Evergreen",
        colors={
            "app_bg_start": "#0b1712",
            "app_bg_end": "#13221a",
            "text_color": "#ecf7ef",
            "field_bg": "#163126",
            "field_border": "#2f5e46",
            "accent_color": "#1f8a5b",
            "accent_hover": "#26a36c",
            "accent_pressed": "#186d48",
            "terminal_bg": "#0e2218",
            "terminal_fg": "#86efac",
            "tab_active_bg": "#1f8a5b",
            "tab_active_fg": "#f0fff5",
            "tab_inactive_bg": "#173429",
            "tab_inactive_fg": "#a8c9b6",
        },
    ),
    ThemePreset(
        theme_id="graphite",
        label="Graphite",
        colors={
            "app_bg_start": "#111315",
            "app_bg_end": "#1a1d21",
            "text_color": "#f5f3ee",
            "field_bg": "#22262b",
            "field_border": "#4a4f59",
            "accent_color": "#8c5a00",
            "accent_hover": "#a96d00",
            "accent_pressed": "#744b00",
            "terminal_bg": "#16181b",
            "terminal_fg": "#facc15",
            "tab_active_bg": "#8c5a00",
            "tab_active_fg": "#fff9ed",
            "tab_inactive_bg": "#2a2e34",
            "tab_inactive_fg": "#b5b8bf",
        },
    ),
    ThemePreset(
        theme_id="burgundy",
        label="Burgundy",
        colors={
            "app_bg_start": "#161218",
            "app_bg_end": "#231a24",
            "text_color": "#f4edf5",
            "field_bg": "#2a2230",
            "field_border": "#58415f",
            "accent_color": "#a63d66",
            "accent_hover": "#bf4775",
            "accent_pressed": "#8b3255",
            "terminal_bg": "#1e1622",
            "terminal_fg": "#f9a8d4",
            "tab_active_bg": "#a63d66",
            "tab_active_fg": "#fff1f7",
            "tab_inactive_bg": "#342938",
            "tab_inactive_fg": "#bea6c3",
        },
    ),
    ThemePreset(
        theme_id="onyx",
        label="Onyx Blue (Default)",
        colors={
            "app_bg_start": "#0e1116",
            "app_bg_end": "#141a22",
            "text_color": "#e7edf5",
            "field_bg": "#1a222d",
            "field_border": "#3b4d66",
            "accent_color": "#2d6cdf",
            "accent_hover": "#3b7be9",
            "accent_pressed": "#2459b8",
            "terminal_bg": "#0f151e",
            "terminal_fg": "#93c5fd",
            "tab_active_bg": "#2d6cdf",
            "tab_active_fg": "#f8fbff",
            "tab_inactive_bg": "#1c2632",
            "tab_inactive_fg": "#a8b3c2",
        },
    ),
    ThemePreset(
        theme_id="charcoal",
        label="Charcoal Teal",
        colors={
            "app_bg_start": "#111718",
            "app_bg_end": "#182326",
            "text_color": "#e9f2f0",
            "field_bg": "#1f2d31",
            "field_border": "#3f5f66",
            "accent_color": "#0f7a80",
            "accent_hover": "#12939a",
            "accent_pressed": "#0c6267",
            "terminal_bg": "#121f22",
            "terminal_fg": "#5eead4",
            "tab_active_bg": "#0f7a80",
            "tab_active_fg": "#f0fdfa",
            "tab_inactive_bg": "#223338",
            "tab_inactive_fg": "#a7bbb8",
        },
    ),
    ThemePreset(
        theme_id="steel",
        label="Steel Indigo",
        colors={
            "app_bg_start": "#10131a",
            "app_bg_end": "#181e2a",
            "text_color": "#e8ebf5",
            "field_bg": "#202738",
            "field_border": "#46506d",
            "accent_color": "#4f5bd5",
            "accent_hover": "#606ce4",
            "accent_pressed": "#3f49af",
            "terminal_bg": "#131827",
            "terminal_fg": "#a5b4fc",
            "tab_active_bg": "#4f5bd5",
            "tab_active_fg": "#f5f7ff",
            "tab_inactive_bg": "#232b3e",
            "tab_inactive_fg": "#a8b0c9",
        },
    ),
    ThemePreset(
        theme_id="espresso",
        label="Espresso Bronze",
        colors={
            "app_bg_start": "#16120f",
            "app_bg_end": "#211a15",
            "text_color": "#f3ece5",
            "field_bg": "#2a221c",
            "field_border": "#5a4638",
            "accent_color": "#9a6b3f",
            "accent_hover": "#b47d49",
            "accent_pressed": "#7f5734",
            "terminal_bg": "#1a1410",
            "terminal_fg": "#f4b57a",
            "tab_active_bg": "#9a6b3f",
            "tab_active_fg": "#fff7ed",
            "tab_inactive_bg": "#322821",
            "tab_inactive_fg": "#c4b2a2",
        },
    ),
    ThemePreset(
        theme_id="olive",
        label="Olive Alloy",
        colors={
            "app_bg_start": "#12160f",
            "app_bg_end": "#1b2317",
            "text_color": "#edf2e6",
            "field_bg": "#242d1e",
            "field_border": "#506042",
            "accent_color": "#6a7f38",
            "accent_hover": "#7c9442",
            "accent_pressed": "#586b2f",
            "terminal_bg": "#151c12",
            "terminal_fg": "#c4f07a",
            "tab_active_bg": "#6a7f38",
            "tab_active_fg": "#f7fce9",
            "tab_inactive_bg": "#2a3423",
            "tab_inactive_fg": "#b5c1a8",
        },
    ),
    ThemePreset(
        theme_id="atlantic",
        label="Atlantic Cyan",
        colors={
            "app_bg_start": "#0d1419",
            "app_bg_end": "#16232b",
            "text_color": "#e6f1f6",
            "field_bg": "#1a2d36",
            "field_border": "#3c6474",
            "accent_color": "#1d8ea8",
            "accent_hover": "#24a5c2",
            "accent_pressed": "#17748a",
            "terminal_bg": "#0f2027",
            "terminal_fg": "#67e8f9",
            "tab_active_bg": "#1d8ea8",
            "tab_active_fg": "#effcff",
            "tab_inactive_bg": "#20353f",
            "tab_inactive_fg": "#a6bdc7",
        },
    ),
)

THEME_PRESET_BY_ID = {preset.theme_id: preset for preset in THEME_PRESETS}
VALID_THEME_IDS = frozenset([*THEME_PRESET_BY_ID.keys(), CUSTOM_THEME_ID])


def normalize_theme_id(raw_theme: str) -> str:
    token = raw_theme.strip().lower()
    if token in VALID_THEME_IDS:
        return token
    return DEFAULT_THEME_ID


def theme_colors_for(theme_id: str) -> dict[str, str] | None:
    preset = THEME_PRESET_BY_ID.get(theme_id.strip().lower())
    if preset is None:
        return None
    return dict(preset.colors)


def infer_theme_id_from_colors(values: Mapping[str, str]) -> str:
    for preset in THEME_PRESETS:
        if all(
            _normalized_color(values.get(key, "")) == _normalized_color(preset.colors[key])
            for key in THEME_COLOR_FIELDS
        ):
            return preset.theme_id
    return CUSTOM_THEME_ID


def theme_matches_colors(theme_id: str, values: Mapping[str, str]) -> bool:
    preset = THEME_PRESET_BY_ID.get(theme_id.strip().lower())
    if preset is None:
        return False
    return all(
        _normalized_color(values.get(key, "")) == _normalized_color(preset.colors[key])
        for key in THEME_COLOR_FIELDS
    )


def _normalized_color(raw: str) -> str:
    return raw.strip().lower()
