from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from snakesh.core.paths import data_dir
from snakesh.core.tool_registry import TOOL_REGISTRY_BY_KEY
from snakesh.services._instance_activation import (
    InstanceClaimResult,
    InstanceLease,
    InstanceState,
    activate_instance,
    claim_instance,
    has_active_instance,
    read_instance_state,
)


_TOOL_KEY_FIELD = "tool_key"


@dataclass(frozen=True)
class ToolInstanceState:
    tool_key: str
    pid: int
    port: int
    token: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ToolInstanceState" | None:
        tool_key = str(raw.get("tool_key", "")).strip()
        if tool_key not in TOOL_REGISTRY_BY_KEY:
            return None
        state = InstanceState.from_dict(raw, key_field=_TOOL_KEY_FIELD, expected_key=tool_key)
        if state is None:
            return None
        return cls(tool_key=state.instance_key, pid=state.pid, port=state.port, token=state.token)

    @classmethod
    def from_instance_state(cls, state: InstanceState) -> "ToolInstanceState":
        return cls(tool_key=state.instance_key, pid=state.pid, port=state.port, token=state.token)

    def to_instance_state(self) -> InstanceState:
        return InstanceState(instance_key=self.tool_key, pid=self.pid, port=self.port, token=self.token)

    def to_dict(self) -> dict[str, object]:
        return self.to_instance_state().to_dict(key_field=_TOOL_KEY_FIELD)


@dataclass(frozen=True)
class ToolInstanceClaimResult:
    lease: "ToolInstanceLease" | None = None
    activated_existing: bool = False


class ToolInstanceLease:
    def __init__(self, lease: InstanceLease) -> None:
        self._lease = lease
        self.tool_key = lease.instance_key
        self.state = ToolInstanceState.from_instance_state(lease.state)

    def release(self) -> None:
        self._lease.release()


def tool_instance_runtime_dir() -> Path:
    directory = data_dir() / "runtime" / "tool-instances"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def tool_instance_state_path(tool_key: str) -> Path:
    _validate_tool_key(tool_key)
    return tool_instance_runtime_dir() / f"{tool_key}.json"


def tool_instance_lock_path(tool_key: str) -> Path:
    _validate_tool_key(tool_key)
    return tool_instance_runtime_dir() / f"{tool_key}.lock"


def tool_activation_payload(
    tool_key: str,
    *,
    arguments: list[str] | tuple[str, ...] | None = None,
) -> dict[str, object] | None:
    _validate_tool_key(tool_key)
    values = [str(value) for value in (arguments or ())]
    if not values:
        return None
    return {"arguments": values}


def read_tool_instance_state(tool_key: str) -> ToolInstanceState | None:
    _validate_tool_key(tool_key)
    state = read_instance_state(
        state_path=tool_instance_state_path(tool_key),
        lock_path=tool_instance_lock_path(tool_key),
        instance_key=tool_key,
        key_field=_TOOL_KEY_FIELD,
    )
    return ToolInstanceState.from_instance_state(state) if state is not None else None


def has_active_tool_instance(tool_key: str) -> bool:
    _validate_tool_key(tool_key)
    return has_active_instance(
        state_path=tool_instance_state_path(tool_key),
        lock_path=tool_instance_lock_path(tool_key),
        instance_key=tool_key,
        key_field=_TOOL_KEY_FIELD,
    )


def activate_tool_instance(
    tool_key: str,
    *,
    payload: Mapping[str, object] | None = None,
) -> bool:
    _validate_tool_key(tool_key)
    return activate_instance(
        state_path=tool_instance_state_path(tool_key),
        lock_path=tool_instance_lock_path(tool_key),
        instance_key=tool_key,
        key_field=_TOOL_KEY_FIELD,
        payload=payload,
    )


def activate_active_tool_instances(
    *,
    payload_factory: Callable[[str], Mapping[str, object] | None] | None = None,
) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for tool_key in TOOL_REGISTRY_BY_KEY:
        payload = payload_factory(tool_key) if payload_factory is not None else None
        if activate_tool_instance(tool_key, payload=payload):
            results[tool_key] = True
    return results


def claim_tool_instance(
    tool_key: str,
    *,
    on_activate: Callable[[dict[str, object] | None], bool],
    activation_payload: Mapping[str, object] | None = None,
) -> ToolInstanceClaimResult:
    _validate_tool_key(tool_key)
    result: InstanceClaimResult = claim_instance(
        state_path=tool_instance_state_path(tool_key),
        lock_path=tool_instance_lock_path(tool_key),
        instance_key=tool_key,
        key_field=_TOOL_KEY_FIELD,
        on_activate=on_activate,
        activation_payload=activation_payload,
    )
    lease = ToolInstanceLease(result.lease) if result.lease is not None else None
    return ToolInstanceClaimResult(lease=lease, activated_existing=result.activated_existing)


def _validate_tool_key(tool_key: str) -> None:
    if tool_key not in TOOL_REGISTRY_BY_KEY:
        raise KeyError(tool_key)
