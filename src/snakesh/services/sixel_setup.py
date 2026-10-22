"""Optional local Sixel image converters; the terminal decoder is built in."""
from __future__ import annotations

import base64
import platform
import shutil

from snakesh.services.external_tools import resolve_executable
from snakesh.services.platform_deps import PlatformDependency, attempt_auto_install
from snakesh.services.privilege_service import run_command

WINDOWS_SIXEL_VERSION = "0.7.1"


def _ensure_windows_sixel_tools() -> tuple[bool, str]:
    shells = list(dict.fromkeys(filter(None, (
        resolve_executable(("pwsh.exe",)), resolve_executable(("powershell.exe",)),
    ))))
    if not shells:
        return False, "PowerShell was not found. Install PowerShell, then save Local Sixel again."
    # Install into each available shell's CurrentUser module directory. Windows
    # PowerShell 5.1 and PowerShell 7 use different user module locations.
    script = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    if (-not (Get-Module -ListAvailable -Name Sixel | Where-Object { $_.Version -eq [version]'__VERSION__' })) {
        if (-not (Get-PackageProvider -Name NuGet -ListAvailable -ErrorAction SilentlyContinue)) {
            Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force -Confirm:$false | Out-Null
        }
        Install-Module -Name Sixel -RequiredVersion __VERSION__ -Scope CurrentUser -Repository PSGallery -Force -Confirm:$false
    }
    Import-Module Sixel -RequiredVersion __VERSION__ -ErrorAction Stop
    Get-Command ConvertTo-Sixel -Module Sixel -ErrorAction Stop | Out-Null
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
'''.replace("__VERSION__", WINDOWS_SIXEL_VERSION)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    for shell in shells:
        result = run_command(
            [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            require_elevation=False, timeout=600,
        )
        if not result.success:
            return False, f"Sixel setup failed in {shell}: {result.message}"
    return True, "Local Sixel tools are ready. In PowerShell, run Import-Module Sixel, then ConvertTo-Sixel."


def ensure_local_sixel_tools() -> tuple[bool, str]:
    system = platform.system().lower()
    if system == "windows":
        return _ensure_windows_sixel_tools()
    if resolve_executable(("img2sixel",), platform_name=system):
        return True, "Local Sixel tools are ready."
    if system == "darwin":
        brew = resolve_executable(("brew",), platform_name=system)
        if brew is None:
            return False, (
                "Sixel rendering is built in. To install the local image converter on macOS, "
                "install Homebrew from https://brew.sh, then save these settings again."
            )
        # Homebrew must run as the current user, never through sudo/osascript.
        result = run_command([brew, "install", "libsixel"], require_elevation=False)
        if not result.success:
            return False, result.message
        if not resolve_executable(("img2sixel",), platform_name=system):
            return False, "Homebrew completed, but img2sixel was not found. Check brew install libsixel, then retry."
        return True, "Local Sixel tools are ready. Open a new local terminal to use img2sixel."
    if system != "linux":
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
