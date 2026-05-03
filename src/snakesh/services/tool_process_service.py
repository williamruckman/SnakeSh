from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import threading

from snakesh import runtime
from snakesh.core.tool_registry import TOOL_REGISTRY, TOOL_REGISTRY_BY_KEY
from snakesh.services.settings_service import AppSettings
from snakesh.services.tool_instance_service import (
    activate_active_tool_instances,
    activate_tool_instance,
    tool_activation_payload,
)


@dataclass(frozen=True)
class ToolLaunchResult:
    process: subprocess.Popen[bytes] | None = None
    activated_existing: bool = False

    @property
    def spawned_new(self) -> bool:
        return self.process is not None


class _ToolSettingsSyncDispatcher:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._pending: tuple[AppSettings, bool] | None = None
        self._worker: threading.Thread | None = None

    def queue(self, settings: AppSettings, *, preview: bool) -> None:
        payload = (AppSettings.from_dict(settings.to_dict()), bool(preview))
        with self._condition:
            self._pending = payload
            worker = self._worker
            if worker is None or not worker.is_alive():
                worker = threading.Thread(
                    target=self._run,
                    name="snakesh-tool-settings-sync",
                    daemon=True,
                )
                self._worker = worker
                worker.start()
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None:
                    self._condition.wait(timeout=0.5)
                    if self._pending is None:
                        self._worker = None
                        return
                settings, preview = self._pending
                self._pending = None
            try:
                broadcast_tool_settings_sync(settings, preview=preview)
            except Exception:
                continue


_TOOL_SETTINGS_SYNC_DISPATCHER = _ToolSettingsSyncDispatcher()


def supported_tool_keys() -> list[str]:
    return [entry.key for entry in TOOL_REGISTRY]


def ping_tool_arguments(
    *,
    packet_size: int | None = None,
    ipv6: bool | None = None,
) -> list[str]:
    arguments: list[str] = []
    if packet_size is not None:
        arguments.extend(["--packet-size", str(max(0, int(packet_size)))])
    if bool(ipv6):
        arguments.append("--ipv6")
    return arguments


def activate_existing_tool_instance(
    tool_key: str,
    *,
    arguments: Sequence[str] | None = None,
) -> bool:
    if tool_key not in TOOL_REGISTRY_BY_KEY:
        raise KeyError(tool_key)
    return activate_tool_instance(
        tool_key,
        payload=tool_activation_payload(tool_key, arguments=tuple(str(value) for value in (arguments or ()))),
    )


def tool_settings_sync_payload(settings: AppSettings, *, preview: bool) -> dict[str, object]:
    return {
        "kind": "settings_sync",
        "preview": bool(preview),
        "settings": settings.to_dict(),
    }


def broadcast_tool_settings_sync(settings: AppSettings, *, preview: bool) -> dict[str, bool]:
    payload = tool_settings_sync_payload(settings, preview=preview)
    return activate_active_tool_instances(payload_factory=lambda _tool_key: payload)


def queue_tool_settings_sync(settings: AppSettings, *, preview: bool) -> None:
    _TOOL_SETTINGS_SYNC_DISPATCHER.queue(settings, preview=preview)


def standalone_tool_command(
    tool_key: str,
    *,
    arguments: Sequence[str] | None = None,
) -> list[str]:
    if tool_key not in TOOL_REGISTRY_BY_KEY:
        raise KeyError(tool_key)
    return runtime.self_launch_command(["tool", tool_key, *(str(value) for value in (arguments or ()))])


def windows_detached_creationflags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def detached_popen(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if cwd is not None:
        popen_kwargs["cwd"] = str(cwd)
    if env is not None:
        popen_kwargs["env"] = env
    creationflags = windows_detached_creationflags()
    if creationflags:
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen([str(part) for part in command], **popen_kwargs)  # noqa: S603


def launch_standalone_tool(
    tool_key: str,
    *,
    arguments: Sequence[str] | None = None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> ToolLaunchResult:
    activation_arguments = tuple(str(value) for value in (arguments or ()))
    if activate_existing_tool_instance(tool_key, arguments=activation_arguments):
        return ToolLaunchResult(process=None, activated_existing=True)
    command = standalone_tool_command(tool_key, arguments=arguments)
    sanitized_env = runtime.sanitized_self_launch_environment(env)
    process = detached_popen(command, cwd=cwd, env=sanitized_env)
    return ToolLaunchResult(process=process, activated_existing=False)
