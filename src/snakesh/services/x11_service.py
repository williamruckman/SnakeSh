from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess

from snakesh.services.privilege_service import run_command


@dataclass(slots=True)
class X11Provider:
    name: str
    process_names: list[str]
    executable_candidates: list[str]
    launch_args: list[str]


class X11Service:
    _PROVIDERS: list[X11Provider] = [
        X11Provider(
            name="VcXsrv",
            process_names=["vcxsrv.exe"],
            executable_candidates=[
                "vcxsrv.exe",
                r"C:\Program Files\VcXsrv\vcxsrv.exe",
                r"C:\Program Files (x86)\VcXsrv\vcxsrv.exe",
            ],
            launch_args=[":0", "-multiwindow", "-clipboard", "-silent-dup-error"],
        ),
        X11Provider(
            name="Xming",
            process_names=["xming.exe"],
            executable_candidates=[
                "xming.exe",
                r"C:\Program Files\Xming\Xming.exe",
                r"C:\Program Files (x86)\Xming\Xming.exe",
            ],
            launch_args=[":0", "-multiwindow", "-clipboard"],
        ),
        X11Provider(
            name="X410",
            process_names=["x410.exe"],
            executable_candidates=["x410.exe"],
            launch_args=[],
        ),
    ]

    _WINGET_IDS = [
        "marha.VcXsrv",
        "VcXsrv.VcXsrv",
    ]

    def ensure_windows_x_server(self, *, allow_install: bool) -> tuple[bool, str]:
        if platform.system().lower() != "windows":
            return True, "Windows X11 handling not required."

        running = self._running_provider()
        if running:
            self._ensure_display_env()
            return True, f"{running.name} is already running."

        preferred = self._provider_by_name("VcXsrv")
        assert preferred is not None
        preferred_exe = self._find_executable(preferred)
        if preferred_exe:
            self._launch_provider(preferred, preferred_exe)
            self._ensure_display_env()
            return True, "Launched VcXsrv."

        fallback = self._find_any_installed_provider(excluding={preferred.name})
        if fallback:
            provider, executable = fallback
            self._launch_provider(provider, executable)
            self._ensure_display_env()
            return True, f"Launched {provider.name}."

        if not allow_install:
            return False, "No local X server found."

        installed, install_message = self._install_vcxsrv()
        if not installed:
            return False, install_message

        preferred_exe = self._find_executable(preferred)
        if not preferred_exe:
            return False, "VcXsrv was installed, but executable was not found on PATH."

        self._launch_provider(preferred, preferred_exe)
        self._ensure_display_env()
        return True, "Installed and launched VcXsrv."

    def _running_provider(self) -> X11Provider | None:
        for provider in self._PROVIDERS:
            if any(self._is_process_running(process) for process in provider.process_names):
                return provider
        return None

    def _provider_by_name(self, name: str) -> X11Provider | None:
        for provider in self._PROVIDERS:
            if provider.name == name:
                return provider
        return None

    def _find_any_installed_provider(self, *, excluding: set[str]) -> tuple[X11Provider, str] | None:
        for provider in self._PROVIDERS:
            if provider.name in excluding:
                continue
            executable = self._find_executable(provider)
            if executable:
                return provider, executable
        return None

    @staticmethod
    def _find_executable(provider: X11Provider) -> str | None:
        for candidate in provider.executable_candidates:
            path_hit = shutil.which(candidate)
            if path_hit:
                return path_hit
            expanded = os.path.expandvars(candidate)
            if Path(expanded).exists():
                return expanded
        return None

    @staticmethod
    def _launch_provider(provider: X11Provider, executable: str) -> None:
        subprocess.Popen([executable, *provider.launch_args], creationflags=subprocess.DETACHED_PROCESS)  # type: ignore[attr-defined]

    def _install_vcxsrv(self) -> tuple[bool, str]:
        winget = shutil.which("winget")
        if not winget:
            return False, "VcXsrv is missing and winget is not available for automatic install."

        failures: list[str] = []
        for package_id in self._WINGET_IDS:
            try:
                result = run_command(
                    [winget, "install", "--id", package_id, "-e", "--source", "winget"],
                    require_elevation=True,
                )
                if result.success:
                    return True, f"Installed {package_id}."
                failures.append(f"{package_id}: {result.message}")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{package_id}: {exc}")
        return False, "Failed to install VcXsrv with winget.\n" + "\n".join(failures)

    @staticmethod
    def _is_process_running(image_name: str) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}"],
                check=False,
                capture_output=True,
                text=True,
            )
            output = (result.stdout or "").lower()
            return image_name.lower() in output
        except Exception:
            return False

    @staticmethod
    def _ensure_display_env() -> None:
        if not os.environ.get("DISPLAY"):
            os.environ["DISPLAY"] = "127.0.0.1:0.0"
