"""Best-effort macOS Local Network request, following Apple technote TN3179.

https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy
"""
from __future__ import annotations

import ipaddress
import platform
import secrets
import socket
import sys

from PySide6.QtNetwork import QNetworkInterface


def local_network_privacy_supported() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        return int(platform.mac_ver()[0].split(".", 1)[0]) >= 15
    except ValueError:
        return False


def _probe_addresses() -> list[tuple[str, int, int, int]]:
    addresses = []
    flags = QNetworkInterface.InterfaceFlag
    for interface in QNetworkInterface.allInterfaces():
        if not interface.flags() & flags.IsUp or not interface.flags() & flags.CanBroadcast:
            continue
        scope = interface.index()
        if scope <= 0:
            continue
        for entry in interface.addressEntries():
            try:
                address = ipaddress.ip_address(entry.ip().toString().split("%", 1)[0])
            except ValueError:
                continue
            if not isinstance(address, ipaddress.IPv6Address) or not address.is_link_local:
                continue
            # Apple recommends two random peers per link-local address. UDP
            # connect only selects a route; no datagram is sent, even to port 9.
            for _ in range(2):
                peer = ipaddress.IPv6Address(address.packed[:8] + secrets.token_bytes(8))
                addresses.append((str(peer), 9, 0, scope))
    return addresses


def request_local_network_access() -> int:
    """Try to trigger the system alert; return attempts, never permission state.

    There is no general permission-query API. A socket error can mean denial,
    unavailable networking, or another failure. Existing denials need Settings.
    This runs in SnakeSh itself so the request is attributed to the GUI app.
    """
    if not local_network_privacy_supported():
        return 0
    attempts = 0
    for address in _probe_addresses():
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as probe:
                probe.setblocking(False)
                attempts += 1
                probe.connect(address)
        except OSError:
            # A failed connect can still trigger the prompt. Continue to the
            # next interface, without polling or blocking the GUI for a reply.
            continue
    return attempts
