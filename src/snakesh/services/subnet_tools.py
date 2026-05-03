from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from itertools import islice


@dataclass(slots=True)
class SubnetSummary:
    input_value: str
    ip_address: str
    network: str
    prefix: int
    netmask: str
    wildcard: str
    broadcast: str
    first_host: str
    last_host: str
    total_addresses: int
    usable_hosts: int
    host_bits: int


@dataclass(slots=True)
class PlannedSubnet:
    subnet: str
    prefix: int
    netmask: str
    wildcard: str
    broadcast: str
    first_host: str
    last_host: str
    total_addresses: int
    usable_hosts: int


def summarize_cidr(value: str, *, ip_version: int | None = None) -> SubnetSummary:
    raw = value.strip()
    if not raw:
        raise ValueError(_input_help(ip_version, kind="address"))

    interface_text = _normalize_interface_input(raw, ip_version=ip_version)
    try:
        interface = ipaddress.ip_interface(interface_text)
    except ValueError as exc:
        raise ValueError(_invalid_input_message(ip_version, kind="address")) from exc

    _ensure_version(interface.version, ip_version)

    network = interface.network
    max_bits = _max_prefix_bits_for_version(interface.version)
    first_host, last_host, usable_hosts = _host_range_for_network(network)
    return SubnetSummary(
        input_value=raw,
        ip_address=str(interface.ip),
        network=str(network.network_address),
        prefix=network.prefixlen,
        netmask=str(network.netmask),
        wildcard=str(network.hostmask),
        broadcast=str(network.broadcast_address),
        first_host=first_host,
        last_host=last_host,
        total_addresses=network.num_addresses,
        usable_hosts=usable_hosts,
        host_bits=max(0, max_bits - network.prefixlen),
    )


def split_network(
    cidr: str,
    *,
    new_prefix: int,
    max_results: int = 256,
    ip_version: int | None = None,
) -> list[PlannedSubnet]:
    raw = cidr.strip()
    if not raw:
        raise ValueError(_input_help(ip_version, kind="network"))
    if max_results <= 0:
        raise ValueError("max_results must be greater than zero.")

    try:
        network = ipaddress.ip_network(raw, strict=False)
    except ValueError as exc:
        raise ValueError(_invalid_input_message(ip_version, kind="network")) from exc

    _ensure_version(network.version, ip_version)
    max_bits = _max_prefix_bits_for_version(network.version)
    if new_prefix < network.prefixlen:
        raise ValueError(
            f"Target prefix /{new_prefix} is broader than base network /{network.prefixlen}."
        )
    if new_prefix > max_bits:
        raise ValueError(f"Target prefix must be /{max_bits} or less.")

    subnets = (
        [network]
        if new_prefix == network.prefixlen
        else list(islice(network.subnets(new_prefix=new_prefix), max_results))
    )

    planned: list[PlannedSubnet] = []
    for subnet in subnets:
        first_host, last_host, usable_hosts = _host_range_for_network(subnet)
        planned.append(
            PlannedSubnet(
                subnet=str(subnet.network_address),
                prefix=subnet.prefixlen,
                netmask=str(subnet.netmask),
                wildcard=str(subnet.hostmask),
                broadcast=str(subnet.broadcast_address),
                first_host=first_host,
                last_host=last_host,
                total_addresses=subnet.num_addresses,
                usable_hosts=usable_hosts,
            )
        )
    return planned


def _host_range_for_network(
    network: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> tuple[str, str, int]:
    if network.version == 4:
        if network.prefixlen == 32:
            only = str(network.network_address)
            return only, only, 1
        if network.prefixlen == 31:
            first = str(network.network_address)
            last = str(network.broadcast_address)
            return first, last, 2
        first = str(network.network_address + 1)
        last = str(network.broadcast_address - 1)
        usable = max(0, network.num_addresses - 2)
        return first, last, usable

    first = str(network.network_address)
    last = str(network.broadcast_address)
    return first, last, network.num_addresses


def _max_prefix_bits_for_version(version: int) -> int:
    return 32 if version == 4 else 128


def _normalize_interface_input(raw: str, ip_version: int | None) -> str:
    if "/" in raw:
        return raw
    if ip_version == 4:
        return f"{raw}/32"
    if ip_version == 6:
        return f"{raw}/128"
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return raw
    suffix = 32 if addr.version == 4 else 128
    return f"{raw}/{suffix}"


def _ensure_version(actual: int, requested: int | None) -> None:
    if requested is None:
        return
    if requested not in (4, 6):
        raise ValueError("ip_version must be 4 or 6.")
    if actual != requested:
        if requested == 4:
            raise ValueError("Expected IPv4 input, but received IPv6.")
        raise ValueError("Expected IPv6 input, but received IPv4.")


def _input_help(ip_version: int | None, *, kind: str) -> str:
    if kind == "address":
        if ip_version == 4:
            return "Enter an IPv4 address or CIDR (example: 198.51.100.10/24)."
        if ip_version == 6:
            return "Enter an IPv6 address or CIDR (example: 2001:db8::1/64)."
        return "Enter an IP address or CIDR."
    if ip_version == 4:
        return "Enter a base IPv4 network in CIDR format (example: 198.18.0.0/16)."
    if ip_version == 6:
        return "Enter a base IPv6 network in CIDR format (example: 2001:db8::/48)."
    return "Enter a base network in CIDR format."


def _invalid_input_message(ip_version: int | None, *, kind: str) -> str:
    if kind == "address":
        if ip_version == 4:
            return "Invalid IPv4 address/CIDR input."
        if ip_version == 6:
            return "Invalid IPv6 address/CIDR input."
        return "Invalid IP address/CIDR input."
    if ip_version == 4:
        return "Invalid base IPv4 network."
    if ip_version == 6:
        return "Invalid base IPv6 network."
    return "Invalid base network."
