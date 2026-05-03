from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ToolRegistryEntry:
    key: str
    label: str
    opener_name: str
    menu_group: int


TOOL_REGISTRY: tuple[ToolRegistryEntry, ...] = (
    ToolRegistryEntry("resource_monitor", "Resource Monitor", "_open_resource_monitor_tool", 1),
    ToolRegistryEntry("network_inspector", "Network Inspector", "_open_network_inspector_tool", 1),
    ToolRegistryEntry("whois", "Whois", "_open_whois_tool", 2),
    ToolRegistryEntry("asn_lookup", "ASN Lookup", "_open_asn_lookup_tool", 2),
    ToolRegistryEntry("dig", "Dig", "_open_dig_tool", 2),
    ToolRegistryEntry("traceroute", "Traceroute", "_open_traceroute_tool", 2),
    ToolRegistryEntry("ping", "Ping", "_open_ping_tool", 2),
    ToolRegistryEntry("ip_scan", "IP Scan", "_open_ip_scan_tool", 2),
    ToolRegistryEntry("mtu_calculator", "MTU / MSS Calculator", "_open_mtu_calculator", 2),
    ToolRegistryEntry("file_hash", "File Hash", "_open_file_hash_tool", 3),
    ToolRegistryEntry("oui_lookup", "OUI Lookup", "_open_oui_lookup_tool", 3),
    ToolRegistryEntry("web_server", "Web Server", "_open_web_server_tool", 3),
    ToolRegistryEntry("syslog_snmp_monitor", "Syslog / SNMP Monitor", "_open_syslog_snmp_monitor_tool", 3),
    ToolRegistryEntry("subnet_calculator", "Subnet Calculator", "_open_subnet_calculator", 3),
    ToolRegistryEntry("password_generator", "Password Generator", "_open_password_generator", 3),
    ToolRegistryEntry("diff", "Diff Tool", "_open_diff_tool", 3),
    ToolRegistryEntry("help", "Help", "_open_help_tool", 4),
)

TOOL_REGISTRY_BY_KEY: dict[str, ToolRegistryEntry] = {entry.key: entry for entry in TOOL_REGISTRY}
PROFILE_STARTUP_DISABLED_TOOL_KEYS: frozenset[str] = frozenset()


def normalize_tool_keys(raw_keys: Iterable[object]) -> list[str]:
    requested = {
        cleaned
        for raw in raw_keys
        if isinstance(raw, str)
        for cleaned in [raw.strip()]
        if cleaned in TOOL_REGISTRY_BY_KEY
    }
    return [entry.key for entry in TOOL_REGISTRY if entry.key in requested]


def profile_startup_tool_entries() -> tuple[ToolRegistryEntry, ...]:
    return tuple(
        entry
        for entry in TOOL_REGISTRY
        if entry.key not in PROFILE_STARTUP_DISABLED_TOOL_KEYS
    )


def normalize_profile_startup_tool_keys(raw_keys: Iterable[object]) -> list[str]:
    requested = set(normalize_tool_keys(raw_keys))
    return [
        entry.key
        for entry in profile_startup_tool_entries()
        if entry.key in requested
    ]
