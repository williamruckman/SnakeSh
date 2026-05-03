from __future__ import annotations

from pathlib import Path

from snakesh import runtime
from snakesh.core.tool_registry import TOOL_REGISTRY_BY_KEY


TOOL_ICON_FORMATS: tuple[str, ...] = ("png", "ico", "icns")
_APP_ICON_FILENAMES: dict[str, str] = {
    "png": "snakesh-icon.png",
    "ico": "snakesh-icon.ico",
    "icns": "snakesh-icon.icns",
}


def tool_icon_filename(tool_key: str, icon_format: str = "png") -> str:
    normalized_format = _normalize_icon_format(icon_format)
    cleaned_key = str(tool_key).strip()
    if cleaned_key in TOOL_REGISTRY_BY_KEY:
        return f"{cleaned_key}.{normalized_format}"
    return _APP_ICON_FILENAMES[normalized_format]


def tool_icon_path(tool_key: str, icon_format: str = "png", *, fallback: bool = True) -> Path:
    normalized_format = _normalize_icon_format(icon_format)
    cleaned_key = str(tool_key).strip()
    if cleaned_key in TOOL_REGISTRY_BY_KEY:
        candidate = runtime.asset_path(f"{cleaned_key}.{normalized_format}")
        if candidate.exists() or not fallback:
            return candidate
    return app_icon_path(normalized_format)


def app_icon_path(icon_format: str = "png") -> Path:
    normalized_format = _normalize_icon_format(icon_format)
    return runtime.asset_path(_APP_ICON_FILENAMES[normalized_format])


def _normalize_icon_format(icon_format: str) -> str:
    normalized = str(icon_format).strip().lower().lstrip(".")
    if normalized not in TOOL_ICON_FORMATS:
        raise ValueError(f"Unsupported tool icon format: {icon_format!r}")
    return normalized
