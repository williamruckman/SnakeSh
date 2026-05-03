from __future__ import annotations

import os
from pathlib import Path
import sys


if getattr(sys, "frozen", False):
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    plugin_root_candidates = (
        bundle_root / "PySide6" / "Qt" / "plugins",
        bundle_root / "PySide6" / "plugins",
    )
    for plugin_root in plugin_root_candidates:
        if not plugin_root.exists():
            continue
        os.environ.setdefault("QT_PLUGIN_PATH", str(plugin_root))
        platform_plugins = plugin_root / "platforms"
        if platform_plugins.exists():
            os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(platform_plugins))
        break
