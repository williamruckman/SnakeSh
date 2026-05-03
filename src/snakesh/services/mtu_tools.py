from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MtuOverheadPreset:
    preset_id: str
    label: str
    extra_bytes: int
    uses_custom_value: bool = False


@dataclass(frozen=True, slots=True)
class MtuCalculationRequest:
    outer_mtu: int
    ip_version: int = 4
    overhead_preset_id: str = "none"
    custom_overhead: int = 0


@dataclass(frozen=True, slots=True)
class MtuCalculationResult:
    outer_mtu: int
    ip_version: int
    preset_label: str
    extra_overhead_bytes: int
    effective_mtu: int
    ip_header_bytes: int
    max_ping_payload: int
    max_udp_payload: int
    tcp_mss: int | None
    advisories: tuple[str, ...]


MTU_OVERHEAD_PRESETS: tuple[MtuOverheadPreset, ...] = (
    MtuOverheadPreset("none", "None (0)", 0),
    MtuOverheadPreset("vlan", "802.1Q VLAN (+4)", 4),
    MtuOverheadPreset("pppoe", "PPPoE (+8)", 8),
    MtuOverheadPreset("pppoe_vlan", "PPPoE + 802.1Q (+12)", 12),
    MtuOverheadPreset("custom", "Custom", 0, uses_custom_value=True),
)


def calculate_mtu(request: MtuCalculationRequest) -> MtuCalculationResult:
    outer_mtu = int(request.outer_mtu)
    if outer_mtu <= 0:
        raise ValueError("Outer / Interface MTU must be greater than zero.")

    ip_version = int(request.ip_version)
    if ip_version not in (4, 6):
        raise ValueError("IP version must be IPv4 or IPv6.")

    preset = overhead_preset_by_id(request.overhead_preset_id)
    custom_overhead = int(request.custom_overhead)
    if custom_overhead < 0:
        raise ValueError("Custom extra bytes cannot be negative.")
    extra_overhead_bytes = custom_overhead if preset.uses_custom_value else preset.extra_bytes
    effective_mtu = outer_mtu - extra_overhead_bytes

    ip_header_bytes = 20 if ip_version == 4 else 40
    minimum_payload_mtu = ip_header_bytes + 8
    if effective_mtu <= minimum_payload_mtu:
        raise ValueError(
            f"Effective MTU must be greater than {minimum_payload_mtu} bytes for IPv{ip_version} ICMP/UDP."
        )

    max_ping_payload = effective_mtu - ip_header_bytes - 8
    max_udp_payload = effective_mtu - ip_header_bytes - 8
    tcp_mss_value = effective_mtu - ip_header_bytes - 20
    tcp_mss = tcp_mss_value if tcp_mss_value > 0 else None

    advisories: list[str] = []
    if ip_version == 4 and effective_mtu < 68:
        advisories.append("Effective MTU is below the typical IPv4 minimum of 68 bytes.")
    if ip_version == 6 and effective_mtu < 1280:
        advisories.append("Effective MTU is below the IPv6 minimum MTU of 1280 bytes.")

    return MtuCalculationResult(
        outer_mtu=outer_mtu,
        ip_version=ip_version,
        preset_label=preset.label,
        extra_overhead_bytes=extra_overhead_bytes,
        effective_mtu=effective_mtu,
        ip_header_bytes=ip_header_bytes,
        max_ping_payload=max_ping_payload,
        max_udp_payload=max_udp_payload,
        tcp_mss=tcp_mss,
        advisories=tuple(advisories),
    )


def format_mtu_summary(result: MtuCalculationResult) -> str:
    lines = [
        "SnakeSh MTU / MSS Calculator",
        f"Outer / Interface MTU: {result.outer_mtu}",
        f"IP Version: IPv{result.ip_version}",
        f"Extra Overhead Preset: {result.preset_label}",
        f"Total Extra Overhead: {result.extra_overhead_bytes}",
        f"Effective MTU: {result.effective_mtu}",
        f"Max Ping Payload: {result.max_ping_payload}",
        f"Max UDP Payload: {result.max_udp_payload}",
        f"TCP MSS (no options): {result.tcp_mss if result.tcp_mss is not None else 'N/A'}",
    ]
    if result.advisories:
        lines.append("Advisories:")
        lines.extend(f"- {message}" for message in result.advisories)
    return "\n".join(lines)


def overhead_preset_by_id(preset_id: str) -> MtuOverheadPreset:
    normalized = preset_id.strip().lower()
    for preset in MTU_OVERHEAD_PRESETS:
        if preset.preset_id == normalized:
            return preset
    raise ValueError(f"Unsupported MTU overhead preset: {preset_id!r}")
