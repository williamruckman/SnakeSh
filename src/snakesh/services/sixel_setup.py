"""Optional local Sixel image converters; the terminal decoder is built in."""
from __future__ import annotations

import platform
import shutil

from snakesh.services.platform_deps import PlatformDependency, attempt_auto_install


def ensure_local_sixel_tools() -> tuple[bool, str]:
    if shutil.which("img2sixel"):
        return True, "Local Sixel tools are ready."
    if platform.system().lower() != "linux":
        return True, "Sixel rendering enabled. Install img2sixel separately to convert local images."
    apt = shutil.which("apt-get")
    if apt is None:
        return False, "Install img2sixel with your system package manager, then save these settings again."
    return attempt_auto_install(
        PlatformDependency(
            id="img2sixel",
            display_name="Sixel image tools",
            command="img2sixel",
            required_for="Converting local images for Sixel terminal graphics",
            can_auto_install=True,
            install_command=[apt, "install", "-y", "libsixel-bin"],
        )
    )
