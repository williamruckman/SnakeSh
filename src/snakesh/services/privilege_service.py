from __future__ import annotations

from dataclasses import dataclass
import json
import os
import platform
import shlex
import shutil
import subprocess
from typing import Sequence


@dataclass(slots=True)
class CommandResult:
    success: bool
    message: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    elevated: bool = False
    cancelled: bool = False


def is_elevated() -> bool:
    system = platform.system().lower()
    if system == "windows":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    if system in {"linux", "darwin"}:
        try:
            return os.geteuid() == 0
        except Exception:
            return False
    return False


def command_to_display(command: Sequence[str]) -> str:
    parts = [str(part) for part in command]
    if not parts:
        return ""
    if platform.system().lower() == "windows":
        return subprocess.list2cmdline(parts)
    try:
        import shlex

        return shlex.join(parts)
    except Exception:
        return " ".join(parts)


def run_command(
    command: Sequence[str],
    *,
    require_elevation: bool = False,
    timeout: float | None = None,
) -> CommandResult:
    normalized = [str(part) for part in command if str(part)]
    if not normalized:
        return CommandResult(success=False, message="No command was provided.")

    if require_elevation:
        system = platform.system().lower()
        if system == "windows":
            return _run_windows_elevated(normalized)
        if system == "linux":
            return _run_linux_elevated(normalized, timeout=timeout)
        if system == "darwin":
            return _run_macos_elevated(normalized, timeout=timeout)
    return _run_plain(normalized, timeout=timeout)


def _run_plain(command: list[str], *, timeout: float | None = None) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return CommandResult(success=False, message=str(exc), exit_code=None)
    return _to_result(completed, elevated=False)


def _run_linux_elevated(command: list[str], *, timeout: float | None = None) -> CommandResult:
    base = _strip_sudo(command)
    if not base:
        return CommandResult(success=False, message="Invalid privileged command.")
    if is_elevated():
        return _run_plain(base, timeout=timeout)

    executable = base[0]
    resolved_executable = executable if os.path.isabs(executable) else (shutil.which(executable) or executable)
    elevated_target = [resolved_executable, *base[1:]]

    if shutil.which("pkexec"):
        runner = ["pkexec", *elevated_target]
    elif shutil.which("sudo"):
        runner = ["sudo", "-k", *elevated_target]
    else:
        return CommandResult(
            success=False,
            message="No privilege escalation tool is available (pkexec/sudo not found).",
        )

    try:
        completed = subprocess.run(
            runner,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return CommandResult(success=False, message=str(exc), exit_code=None, elevated=True)
    result = _to_result(completed, elevated=True)
    if not result.success and "authentication" in (result.stderr or "").lower():
        result.cancelled = True
    return result


def _run_windows_elevated(command: list[str]) -> CommandResult:
    if is_elevated():
        return _run_plain(command)

    payload = json.dumps(command)
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$cmd = ConvertFrom-Json -InputObject @'\n"
        f"{payload}\n"
        "'@\n"
        "if ($cmd -isnot [System.Array]) { $cmd = @($cmd) }\n"
        "if ($cmd.Count -lt 1) { exit 1 }\n"
        "$exe = [string]$cmd[0]\n"
        "$args = @()\n"
        "if ($cmd.Count -gt 1) { $args = @($cmd[1..($cmd.Count - 1)]) }\n"
        "try {\n"
        "  $p = Start-Process -FilePath $exe -ArgumentList $args -Verb RunAs -Wait -PassThru\n"
        "  if ($null -eq $p) { exit 1 }\n"
        "  exit [int]$p.ExitCode\n"
        "} catch {\n"
        "  $m = ($_.Exception.Message | Out-String)\n"
        "  if ($m -match 'cancel') { exit 1223 }\n"
        "  Write-Error $_\n"
        "  exit 1\n"
        "}\n"
    )
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # noqa: BLE001
        return CommandResult(success=False, message=str(exc), elevated=True)

    result = _to_result(completed, elevated=True)
    if completed.returncode == 1223:
        result.cancelled = True
        result.message = "Elevation was cancelled by the user."
    return result


def _run_macos_elevated(command: list[str], *, timeout: float | None = None) -> CommandResult:
    base = _strip_sudo(command)
    if not base:
        return CommandResult(success=False, message="Invalid privileged command.")
    if is_elevated():
        return _run_plain(base, timeout=timeout)
    if shutil.which("osascript") is None:
        return CommandResult(
            success=False,
            message="osascript is required to request administrator privileges on macOS.",
        )

    shell_command = shlex.join(base)
    script = f'do shell script "{_escape_applescript(shell_command)}" with administrator privileges'
    try:
        completed = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        return CommandResult(success=False, message=str(exc), elevated=True)

    result = _to_result(completed, elevated=True)
    lowered = f"{result.stderr} {result.stdout}".lower()
    if not result.success and "cancel" in lowered:
        result.cancelled = True
        result.message = "Elevation was cancelled by the user."
    return result


def _to_result(completed: subprocess.CompletedProcess[str], *, elevated: bool) -> CommandResult:
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    ok = completed.returncode == 0
    if ok:
        message = stdout or "Command completed successfully."
    else:
        message = stderr or stdout or f"Command failed with exit code {completed.returncode}."
    return CommandResult(
        success=ok,
        message=message,
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        elevated=elevated,
    )


def _strip_sudo(command: Sequence[str]) -> list[str]:
    items = [str(part) for part in command if str(part)]
    if not items:
        return []
    if items[0] != "sudo":
        return items

    index = 1
    while index < len(items) and items[index].startswith("-"):
        option = items[index]
        index += 1
        if option in ("-u", "-g", "-h", "-p", "-C", "-T", "--user", "--group", "--host", "--prompt"):
            if index < len(items):
                index += 1
    return items[index:]


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
