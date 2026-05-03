from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from snakesh.core.paths import data_dir
from snakesh.services._instance_activation import (
    InstanceClaimResult,
    InstanceLease,
    InstanceState,
    activate_instance,
    claim_instance,
    has_active_instance,
    read_instance_state,
)


_MAIN_INSTANCE_KEY = "main"
_MAIN_KEY_FIELD = "app_key"


@dataclass(frozen=True)
class MainInstanceState:
    pid: int
    port: int
    token: str

    @classmethod
    def from_instance_state(cls, state: InstanceState) -> "MainInstanceState":
        return cls(pid=state.pid, port=state.port, token=state.token)

    def to_dict(self) -> dict[str, object]:
        return {
            _MAIN_KEY_FIELD: _MAIN_INSTANCE_KEY,
            "pid": self.pid,
            "port": self.port,
            "token": self.token,
        }


@dataclass(frozen=True)
class MainInstanceClaimResult:
    lease: "MainInstanceLease" | None = None
    activated_existing: bool = False


class MainInstanceLease:
    def __init__(self, lease: InstanceLease) -> None:
        self._lease = lease
        self.state = MainInstanceState.from_instance_state(lease.state)

    def release(self) -> None:
        self._lease.release()


def main_instance_state_path() -> Path:
    directory = data_dir() / "runtime"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "main-instance.json"


def main_instance_lock_path() -> Path:
    directory = data_dir() / "runtime"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "main-instance.lock"


def main_activation_payload(import_file: str | None = None) -> dict[str, object] | None:
    if not import_file:
        return None
    path = Path(import_file).expanduser()
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    return {"import_file": str(resolved)}


def read_main_instance_state() -> MainInstanceState | None:
    state = read_instance_state(
        state_path=main_instance_state_path(),
        lock_path=main_instance_lock_path(),
        instance_key=_MAIN_INSTANCE_KEY,
        key_field=_MAIN_KEY_FIELD,
    )
    return MainInstanceState.from_instance_state(state) if state is not None else None


def has_active_main_instance() -> bool:
    return has_active_instance(
        state_path=main_instance_state_path(),
        lock_path=main_instance_lock_path(),
        instance_key=_MAIN_INSTANCE_KEY,
        key_field=_MAIN_KEY_FIELD,
    )


def activate_existing_main_instance(import_file: str | None = None) -> bool:
    return activate_instance(
        state_path=main_instance_state_path(),
        lock_path=main_instance_lock_path(),
        instance_key=_MAIN_INSTANCE_KEY,
        key_field=_MAIN_KEY_FIELD,
        payload=main_activation_payload(import_file),
    )


def claim_main_instance(
    *,
    on_activate: Callable[[dict[str, object] | None], bool],
    activation_payload: Mapping[str, object] | None = None,
) -> MainInstanceClaimResult:
    result: InstanceClaimResult = claim_instance(
        state_path=main_instance_state_path(),
        lock_path=main_instance_lock_path(),
        instance_key=_MAIN_INSTANCE_KEY,
        key_field=_MAIN_KEY_FIELD,
        on_activate=on_activate,
        activation_payload=activation_payload,
    )
    lease = MainInstanceLease(result.lease) if result.lease is not None else None
    return MainInstanceClaimResult(lease=lease, activated_existing=result.activated_existing)
