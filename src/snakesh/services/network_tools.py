from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import ipaddress
import math
import platform
from queue import Empty, Queue
import re
import shutil
import socket
import subprocess
import threading
import time


DNS_RECORD_TYPES: tuple[str, ...] = (
    "A",
    "AAAA",
    "CNAME",
    "MX",
    "NS",
    "TXT",
    "SOA",
    "PTR",
    "SRV",
    "CAA",
    "ANY",
)

IP_SCAN_PRESET_COMMON_20 = "common20"
IP_SCAN_PRESET_COMMON_100 = "common100"
IP_SCAN_PRESET_CUSTOM = "custom"
IP_SCAN_MAX_HOSTS = 4096
IP_SCAN_DEFAULT_TIMEOUT_MS = 400
IP_SCAN_DEFAULT_CONCURRENCY = 128

COMMON_TCP_20_PORTS: tuple[int, ...] = (
    21,
    22,
    23,
    25,
    53,
    80,
    110,
    123,
    135,
    139,
    143,
    389,
    443,
    445,
    465,
    587,
    993,
    995,
    3389,
    5900,
)

COMMON_TCP_100_PORTS: tuple[int, ...] = (
    20,
    21,
    22,
    23,
    25,
    37,
    53,
    69,
    79,
    80,
    81,
    88,
    110,
    111,
    119,
    123,
    135,
    137,
    138,
    139,
    143,
    161,
    179,
    389,
    427,
    443,
    445,
    465,
    513,
    514,
    515,
    548,
    554,
    587,
    631,
    636,
    873,
    902,
    989,
    990,
    993,
    995,
    1025,
    1080,
    1194,
    1433,
    1521,
    1723,
    1883,
    2049,
    2082,
    2083,
    2086,
    2087,
    2095,
    2096,
    2375,
    2376,
    2483,
    2484,
    3306,
    3389,
    3478,
    3690,
    4369,
    5000,
    5060,
    5061,
    5222,
    5432,
    5672,
    5900,
    5985,
    5986,
    6379,
    6443,
    6514,
    6667,
    7001,
    7002,
    8000,
    8008,
    8080,
    8081,
    8086,
    8087,
    8090,
    8091,
    8118,
    8161,
    8200,
    8443,
    8500,
    8530,
    8531,
    8888,
    9000,
    9042,
    9092,
    9200,
)


@dataclass(slots=True)
class PingRequest:
    target: str
    count: int = 4
    timeout_ms: int = 1000
    packet_size: int = 56
    ipv6: bool = False


@dataclass(slots=True)
class DNSLookupRequest:
    query: str
    record_type: str = "A"
    nameserver: str = ""
    timeout_ms: int = 5000
    use_tcp: bool = False


@dataclass(slots=True)
class DNSLookupResult:
    query: str
    record_type: str
    resolver: str
    status: str
    elapsed_ms: float
    answer_lines: list[str]
    authority_lines: list[str]
    additional_lines: list[str]


@dataclass(slots=True)
class WhoisLookupRequest:
    query: str
    server: str = ""
    timeout_ms: int = 8000
    follow_referral: bool = True
    max_referrals: int = 3


@dataclass(slots=True)
class WhoisLookupResult:
    query: str
    sections: list[tuple[str, str]]


@dataclass(slots=True)
class ASNLookupRequest:
    query: str
    server: str = ""
    timeout_ms: int = 8000
    follow_referral: bool = True
    max_referrals: int = 3


@dataclass(slots=True)
class ASNLookupResult:
    query: str
    normalized_asn: str
    as_name: str = ""
    organization: str = ""
    description: str = ""
    country: str = ""
    registry_server: str = ""
    remarks: list[str] = field(default_factory=list)
    sections: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class ResolvedScanTarget:
    address: str
    family: int
    display_name: str = ""


@dataclass(slots=True)
class IPScanRequest:
    target: str
    port_preset: str = IP_SCAN_PRESET_COMMON_20
    custom_ports: str = ""
    timeout_ms: int = IP_SCAN_DEFAULT_TIMEOUT_MS
    concurrency: int = IP_SCAN_DEFAULT_CONCURRENCY
    resolve_names: bool = True


@dataclass(slots=True)
class IPScanHostResult:
    host: str
    status: str
    resolved_name: str
    open_port_count: int
    elapsed_ms: float


@dataclass(slots=True)
class IPScanPortResult:
    host: str
    resolved_name: str
    port: int
    service_name: str


@dataclass(slots=True)
class IPScanProgress:
    total_hosts: int
    completed_hosts: int
    total_probes: int
    completed_probes: int
    current_host: str = ""
    current_port: int = 0
    open_ports_found: int = 0


@dataclass(slots=True)
class IPScanResult:
    target: str
    hosts: list[IPScanHostResult]
    open_ports: list[IPScanPortResult]
    total_hosts: int
    scanned_hosts: int
    total_probes: int
    scanned_probes: int
    canceled: bool
    elapsed_ms: float


def build_ping_command(request: PingRequest, *, platform_name: str | None = None) -> list[str]:
    target = request.target.strip()
    if not target:
        raise ValueError("Enter a host or IP address.")

    count = max(1, int(request.count))
    timeout_ms = max(100, int(request.timeout_ms))
    packet_size = max(1, int(request.packet_size))
    system = _platform_name(platform_name)

    if system == "windows":
        command = ["ping", "-n", str(count), "-w", str(timeout_ms), "-l", str(packet_size)]
        if request.ipv6:
            command.insert(1, "-6")
        command.append(target)
        return command

    timeout_seconds = max(1, math.ceil(timeout_ms / 1000.0))
    command = ["ping", "-c", str(count), "-W", str(timeout_seconds), "-s", str(packet_size)]
    if request.ipv6:
        command.insert(1, "-6")
    command.append(target)
    return command


def expected_cli_name(tool: str, *, platform_name: str | None = None) -> str:
    normalized = tool.strip().lower()
    if normalized == "ping":
        return "ping"
    raise ValueError(f"Unsupported network CLI tool: {tool!r}")


def perform_dns_lookup(request: DNSLookupRequest) -> DNSLookupResult:
    try:
        import dns.rdataclass
        import dns.rdatatype
        import dns.rcode
        import dns.resolver
    except Exception as exc:  # noqa: BLE001
        return _perform_dns_lookup_with_nslookup(request, import_error=exc)

    query = request.query.strip()
    if not query:
        raise ValueError("Enter a hostname or domain name.")

    record_type = request.record_type.strip().upper() or "A"
    if record_type not in DNS_RECORD_TYPES:
        raise ValueError(f"Unsupported record type: {record_type}")

    resolver = dns.resolver.Resolver(configure=True)
    timeout_seconds = max(0.1, request.timeout_ms / 1000.0)
    resolver.timeout = timeout_seconds
    resolver.lifetime = timeout_seconds

    resolver_name = "(system default)"
    nameserver = request.nameserver.strip()
    if nameserver:
        _validate_nameserver(nameserver)
        resolver.nameservers = [nameserver]
        resolver_name = nameserver
    elif resolver.nameservers:
        resolver_name = str(resolver.nameservers[0])

    started = time.perf_counter()
    try:
        answer = resolver.resolve(
            query,
            record_type,
            tcp=request.use_tcp,
            raise_on_no_answer=False,
            search=False,
        )
    except dns.resolver.NXDOMAIN as exc:
        raise ValueError(f"Domain does not exist: {query}") from exc
    except dns.resolver.Timeout as exc:
        raise ValueError("DNS lookup timed out.") from exc
    except dns.resolver.NoNameservers as exc:
        raise ValueError("No DNS nameserver could answer this query.") from exc
    except dns.exception.DNSException as exc:
        raise ValueError(f"DNS lookup failed: {exc}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    response = answer.response
    status = dns.rcode.to_text(response.rcode())
    answer_lines = _rrsets_to_lines(response.answer)
    authority_lines = _rrsets_to_lines(response.authority)
    additional_lines = _rrsets_to_lines(response.additional)

    return DNSLookupResult(
        query=query,
        record_type=record_type,
        resolver=resolver_name,
        status=status,
        elapsed_ms=elapsed_ms,
        answer_lines=answer_lines,
        authority_lines=authority_lines,
        additional_lines=additional_lines,
    )


def format_dns_result(result: DNSLookupResult) -> str:
    lines: list[str] = [
        f";; SnakeSh Dig - {result.query}",
        f";; Record Type: {result.record_type}",
        f";; Resolver: {result.resolver}",
        f";; Status: {result.status}",
        f";; Query Time: {result.elapsed_ms:.1f} msec",
        "",
        ";; ANSWER SECTION:",
    ]
    if result.answer_lines:
        lines.extend(result.answer_lines)
    else:
        lines.append("; (no answer records)")

    lines.append("")
    lines.append(";; AUTHORITY SECTION:")
    if result.authority_lines:
        lines.extend(result.authority_lines)
    else:
        lines.append("; (none)")

    lines.append("")
    lines.append(";; ADDITIONAL SECTION:")
    if result.additional_lines:
        lines.extend(result.additional_lines)
    else:
        lines.append("; (none)")
    return "\n".join(lines).strip() + "\n"


def perform_whois_lookup(request: WhoisLookupRequest) -> WhoisLookupResult:
    query = request.query.strip()
    if not query:
        raise ValueError("Enter a domain, IP address, or ASN.")

    timeout_seconds = max(0.5, request.timeout_ms / 1000.0)
    first_server = request.server.strip() or "whois.iana.org"
    sections: list[tuple[str, str]] = []
    visited: set[str] = set()
    follow_referral = bool(request.follow_referral and not request.server.strip())
    referral_budget = max(0, int(request.max_referrals))
    current_server = first_server

    while True:
        normalized_server = current_server.lower().strip()
        if not normalized_server:
            break
        if normalized_server in visited:
            break
        visited.add(normalized_server)
        payload = _query_whois_server(current_server, query, timeout_seconds)
        sections.append((current_server, payload))

        if not follow_referral or referral_budget <= 0:
            break
        referral = _extract_referral_server(payload)
        if not referral or referral.lower() in visited:
            break
        current_server = referral
        referral_budget -= 1

    return WhoisLookupResult(query=query, sections=sections)


def format_whois_result(result: WhoisLookupResult) -> str:
    lines = [f"WHOIS lookup for {result.query}", ""]
    for index, (server, payload) in enumerate(result.sections, start=1):
        lines.append(f"===== Response {index}: {server} =====")
        text = payload.rstrip()
        lines.append(text if text else "(no response)")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def normalize_asn_query(value: str) -> str:
    cleaned = value.strip().upper()
    if not cleaned:
        raise ValueError("Enter an ASN such as AS15169 or 15169.")
    if cleaned.startswith("AS"):
        cleaned = cleaned[2:].strip()
    if not cleaned.isdigit():
        raise ValueError("ASN must be numeric, with or without the AS prefix.")
    number = int(cleaned)
    if number <= 0 or number > 4294967295:
        raise ValueError("ASN must be between 1 and 4294967295.")
    return f"AS{number}"


def perform_asn_lookup(request: ASNLookupRequest) -> ASNLookupResult:
    normalized_asn = normalize_asn_query(request.query)
    whois_result = perform_whois_lookup(
        WhoisLookupRequest(
            query=normalized_asn,
            server=request.server,
            timeout_ms=request.timeout_ms,
            follow_referral=request.follow_referral,
            max_referrals=request.max_referrals,
        )
    )
    return _parse_asn_lookup_result(
        normalized_asn=normalized_asn,
        sections=whois_result.sections,
    )


def format_asn_result(result: ASNLookupResult) -> str:
    return format_whois_result(WhoisLookupResult(query=result.normalized_asn, sections=result.sections))


def expand_ip_scan_targets(
    target: str,
    *,
    max_hosts: int = IP_SCAN_MAX_HOSTS,
) -> list[str]:
    return [item.address for item in _resolve_ip_scan_targets(target, max_hosts=max_hosts)]


def parse_ip_scan_ports(spec: str) -> list[int]:
    cleaned = spec.strip()
    if not cleaned:
        raise ValueError("Enter one or more ports or ranges, for example 22,80,443,8000-8100.")

    ports: set[int] = set()
    for raw_part in cleaned.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            if not start_text.strip() or not end_text.strip():
                raise ValueError(f"Invalid port range: {part}")
            try:
                start_port = int(start_text)
                end_port = int(end_text)
            except ValueError as exc:
                raise ValueError(f"Invalid port range: {part}") from exc
            if start_port <= 0 or end_port <= 0 or start_port > 65535 or end_port > 65535:
                raise ValueError("Ports must be between 1 and 65535.")
            if start_port > end_port:
                raise ValueError(f"Invalid port range: {part}")
            for port in range(start_port, end_port + 1):
                ports.add(port)
            continue
        try:
            port = int(part)
        except ValueError as exc:
            raise ValueError(f"Invalid port value: {part}") from exc
        if port <= 0 or port > 65535:
            raise ValueError("Ports must be between 1 and 65535.")
        ports.add(port)

    if not ports:
        raise ValueError("Enter at least one port to scan.")
    return sorted(ports)


def resolve_ip_scan_ports(request: IPScanRequest) -> list[int]:
    preset = request.port_preset.strip().lower() or IP_SCAN_PRESET_COMMON_20
    if preset == IP_SCAN_PRESET_COMMON_20:
        return list(COMMON_TCP_20_PORTS)
    if preset == IP_SCAN_PRESET_COMMON_100:
        return list(COMMON_TCP_100_PORTS)
    if preset == IP_SCAN_PRESET_CUSTOM:
        return parse_ip_scan_ports(request.custom_ports)
    raise ValueError(f"Unsupported IP scan port preset: {request.port_preset}")


def perform_ip_scan(
    request: IPScanRequest,
    *,
    progress_callback: Callable[[IPScanProgress], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> IPScanResult:
    targets = _resolve_ip_scan_targets(request.target, max_hosts=IP_SCAN_MAX_HOSTS)
    ports = resolve_ip_scan_ports(request)
    timeout_ms = max(50, int(request.timeout_ms))
    max_workers = max(1, min(int(request.concurrency), 512, len(targets) * len(ports)))
    total_hosts = len(targets)
    total_probes = total_hosts * len(ports)
    if total_probes == 0:
        raise ValueError("Nothing to scan.")

    started = time.perf_counter()
    cancellation = cancel_event or threading.Event()
    task_queue: Queue[tuple[ResolvedScanTarget, int]] = Queue()
    for target in targets:
        for port in ports:
            task_queue.put((target, port))

    target_lookup = {target.address: target for target in targets}
    host_state: dict[str, _IPScanHostAccumulator] = {
        target.address: _IPScanHostAccumulator(source_name=target.display_name)
        for target in targets
    }
    state_lock = threading.Lock()
    completed_probes = 0
    completed_hosts = 0
    open_ports_found = 0
    last_progress_emit = 0.0

    def emit_progress(*, current_host: str, current_port: int, force: bool = False) -> None:
        nonlocal last_progress_emit
        if progress_callback is None:
            return
        now = time.monotonic()
        if not force and (now - last_progress_emit) < 0.05:
            return
        last_progress_emit = now
        progress_callback(
            IPScanProgress(
                total_hosts=total_hosts,
                completed_hosts=completed_hosts,
                total_probes=total_probes,
                completed_probes=completed_probes,
                current_host=current_host,
                current_port=current_port,
                open_ports_found=open_ports_found,
            )
        )

    def worker() -> None:
        nonlocal completed_probes, completed_hosts, open_ports_found
        while not cancellation.is_set():
            try:
                target, port = task_queue.get_nowait()
            except Empty:
                return

            probe_started = time.perf_counter()
            is_open = False
            try:
                is_open = _probe_ip_scan_port(target, port, timeout_ms)
            finally:
                finished_at = time.perf_counter()
                with state_lock:
                    accumulator = host_state[target.address]
                    if accumulator.started_at == 0.0:
                        accumulator.started_at = probe_started
                    accumulator.last_update_at = finished_at
                    accumulator.completed_probes += 1
                    if is_open:
                        accumulator.open_ports.append(port)
                        open_ports_found += 1
                    completed_probes += 1
                    host_complete = accumulator.completed_probes >= len(ports)
                    if host_complete:
                        completed_hosts += 1
                emit_progress(current_host=target.address, current_port=port, force=host_complete)
                task_queue.task_done()

    threads: list[threading.Thread] = []
    for index in range(max_workers):
        thread = threading.Thread(target=worker, name=f"ip-scan-{index}", daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()

    canceled = cancellation.is_set() and completed_probes < total_probes
    hosts: list[IPScanHostResult] = []
    open_port_results: list[IPScanPortResult] = []
    for target in targets:
        accumulator = host_state[target.address]
        if canceled and accumulator.completed_probes <= 0:
            continue
        resolved_name = _resolve_ip_scan_name(
            target,
            accumulator,
            resolve_names=request.resolve_names,
        )
        if accumulator.completed_probes >= len(ports):
            status = "Open Ports Found" if accumulator.open_ports else "No Open TCP Ports"
        else:
            status = "Canceled"
        elapsed_ms = 0.0
        if accumulator.started_at > 0.0 and accumulator.last_update_at >= accumulator.started_at:
            elapsed_ms = (accumulator.last_update_at - accumulator.started_at) * 1000.0
        unique_open_ports = sorted(set(accumulator.open_ports))
        hosts.append(
            IPScanHostResult(
                host=target.address,
                status=status,
                resolved_name=resolved_name,
                open_port_count=len(unique_open_ports),
                elapsed_ms=elapsed_ms,
            )
        )
        for port in unique_open_ports:
            open_port_results.append(
                IPScanPortResult(
                    host=target.address,
                    resolved_name=resolved_name,
                    port=port,
                    service_name=_service_name_for_port(port),
                )
            )

    if progress_callback is not None:
        progress_callback(
            IPScanProgress(
                total_hosts=total_hosts,
                completed_hosts=completed_hosts,
                total_probes=total_probes,
                completed_probes=completed_probes,
                current_host="",
                current_port=0,
                open_ports_found=open_ports_found,
            )
        )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return IPScanResult(
        target=request.target.strip(),
        hosts=hosts,
        open_ports=sorted(open_port_results, key=lambda item: (item.host, item.port)),
        total_hosts=total_hosts,
        scanned_hosts=len(hosts),
        total_probes=total_probes,
        scanned_probes=completed_probes,
        canceled=canceled,
        elapsed_ms=elapsed_ms,
    )


def _platform_name(platform_name: str | None) -> str:
    if platform_name is None:
        return platform.system().strip().lower()
    return platform_name.strip().lower()


@dataclass(slots=True)
class _IPScanHostAccumulator:
    source_name: str = ""
    started_at: float = 0.0
    last_update_at: float = 0.0
    completed_probes: int = 0
    open_ports: list[int] = field(default_factory=list)


def _parse_asn_lookup_result(
    *,
    normalized_asn: str,
    sections: list[tuple[str, str]],
) -> ASNLookupResult:
    asn = normalized_asn
    as_name = ""
    organization = ""
    description_values: list[str] = []
    country = ""
    remarks: list[str] = []

    for _server, payload in sections:
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if not line or ":" not in line:
                continue
            key, value = line.split(":", 1)
            normalized_key = key.strip().lower()
            cleaned_value = value.strip()
            if not cleaned_value:
                continue
            if normalized_key in {"aut-num", "originas", "asnumber", "asn", "as-handle"}:
                try:
                    asn = normalize_asn_query(cleaned_value)
                except ValueError:
                    pass
            elif normalized_key in {"as-name", "asname", "name"} and not as_name:
                as_name = cleaned_value
            elif normalized_key in {"org-name", "organization", "orgname", "owner", "org"} and not organization:
                organization = cleaned_value
            elif normalized_key in {"descr", "description", "comment"}:
                if cleaned_value not in description_values:
                    description_values.append(cleaned_value)
            elif normalized_key in {"country", "country-code"} and not country:
                country = cleaned_value
            elif normalized_key in {"remarks", "remark"}:
                if cleaned_value not in remarks:
                    remarks.append(cleaned_value)

    description = " | ".join(description_values)
    registry_server = sections[-1][0] if sections else ""
    return ASNLookupResult(
        query=normalized_asn,
        normalized_asn=asn,
        as_name=as_name,
        organization=organization,
        description=description,
        country=country,
        registry_server=registry_server,
        remarks=remarks,
        sections=list(sections),
    )


def _resolve_ip_scan_targets(
    target: str,
    *,
    max_hosts: int,
) -> list[ResolvedScanTarget]:
    cleaned = target.strip()
    if not cleaned:
        raise ValueError("Enter a hostname, IP address, or CIDR to scan.")

    if "/" in cleaned:
        try:
            network = ipaddress.ip_network(cleaned, strict=False)
        except ValueError as exc:
            raise ValueError("Enter a valid IP address, hostname, or CIDR.") from exc
        if network.num_addresses > max_hosts:
            raise ValueError(
                f"CIDR target expands to {network.num_addresses} hosts. Narrow the range to {max_hosts} hosts or fewer."
            )
        family = socket.AF_INET if network.version == 4 else socket.AF_INET6
        return [ResolvedScanTarget(str(address), family) for address in network.hosts()]

    try:
        address = ipaddress.ip_address(cleaned)
    except ValueError:
        return _resolve_hostname_scan_targets(cleaned, max_hosts=max_hosts)
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    return [ResolvedScanTarget(str(address), family)]


def _resolve_hostname_scan_targets(hostname: str, *, max_hosts: int) -> list[ResolvedScanTarget]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"Unable to resolve {hostname}: {exc}") from exc

    targets: list[ResolvedScanTarget] = []
    seen: set[tuple[int, str]] = set()
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = str(sockaddr[0]).strip()
        key = (family, address)
        if not address or key in seen:
            continue
        seen.add(key)
        targets.append(ResolvedScanTarget(address=address, family=family, display_name=hostname))
    if not targets:
        raise ValueError(f"Unable to resolve {hostname} to a TCP-capable IP address.")
    if len(targets) > max_hosts:
        raise ValueError(
            f"Hostname resolves to {len(targets)} addresses. Narrow the target to {max_hosts} addresses or fewer."
        )
    return targets


def _probe_ip_scan_port(target: ResolvedScanTarget, port: int, timeout_ms: int) -> bool:
    timeout_seconds = max(0.05, timeout_ms / 1000.0)
    address = (target.address, port, 0, 0) if target.family == socket.AF_INET6 else (target.address, port)
    sock = socket.socket(target.family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout_seconds)
        sock.connect(address)
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _resolve_ip_scan_name(
    target: ResolvedScanTarget,
    accumulator: _IPScanHostAccumulator,
    *,
    resolve_names: bool,
) -> str:
    if accumulator.source_name:
        return accumulator.source_name
    if not resolve_names or not accumulator.open_ports:
        return ""
    try:
        host_name, _aliases, _addresses = socket.gethostbyaddr(target.address)
    except OSError:
        return ""
    return host_name.strip()


def _service_name_for_port(port: int) -> str:
    try:
        return socket.getservbyport(port, "tcp")
    except OSError:
        return ""


def _validate_nameserver(value: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("Nameserver must be a valid IPv4 or IPv6 address.") from exc


def _rrsets_to_lines(rrsets) -> list[str]:
    try:
        import dns.rdataclass
        import dns.rdatatype
    except Exception:
        return []

    lines: list[str] = []
    for rrset in rrsets:
        rr_name = str(rrset.name)
        rr_ttl = rrset.ttl
        rr_class = dns.rdataclass.to_text(rrset.rdclass)
        rr_type = dns.rdatatype.to_text(rrset.rdtype)
        for record in rrset:
            lines.append(f"{rr_name}\t{rr_ttl}\t{rr_class}\t{rr_type}\t{record.to_text()}")
    return lines


def _perform_dns_lookup_with_nslookup(
    request: DNSLookupRequest,
    *,
    import_error: Exception | None = None,
) -> DNSLookupResult:
    query = request.query.strip()
    if not query:
        raise ValueError("Enter a hostname or domain name.")

    record_type = request.record_type.strip().upper() or "A"
    if record_type not in DNS_RECORD_TYPES:
        raise ValueError(f"Unsupported record type: {record_type}")

    nameserver = request.nameserver.strip()
    if nameserver:
        _validate_nameserver(nameserver)

    executable = shutil.which("nslookup")
    if not executable:
        base_message = "DNS lookup requires dnspython or the nslookup command."
        if import_error is None:
            raise RuntimeError(base_message)
        raise RuntimeError(f"{base_message} dnspython import failed: {import_error}") from import_error

    command = [executable, f"-type={record_type}", query]
    if nameserver:
        command.append(nameserver)

    timeout_seconds = max(1, math.ceil(request.timeout_ms / 1000.0))
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("DNS lookup timed out.") from exc

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    output = f"{completed.stdout}{completed.stderr}".replace("\r\n", "\n").replace("\r", "\n")
    output_lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    status = _nslookup_status(output, completed.returncode)

    if not output_lines:
        output_lines = ["(no response text)"]

    resolver_name = nameserver or "(system default)"
    return DNSLookupResult(
        query=query,
        record_type=record_type,
        resolver=resolver_name,
        status=status,
        elapsed_ms=elapsed_ms,
        answer_lines=output_lines,
        authority_lines=[],
        additional_lines=[],
    )


def _nslookup_status(output: str, return_code: int) -> str:
    lowered = output.lower()
    if "non-existent domain" in lowered or "nxdomain" in lowered:
        return "NXDOMAIN"
    if return_code == 0:
        return "NOERROR"
    if "timed out" in lowered:
        return "TIMEOUT"
    return "ERROR"


def _query_whois_server(server: str, query: str, timeout_seconds: float) -> str:
    try:
        with socket.create_connection((server, 43), timeout=timeout_seconds) as conn:
            conn.sendall((query + "\r\n").encode("utf-8", errors="ignore"))
            chunks: list[bytes] = []
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                chunks.append(data)
    except OSError as exc:
        raise ValueError(f"Unable to reach WHOIS server {server}: {exc}") from exc
    return b"".join(chunks).decode("utf-8", errors="replace")


def _extract_referral_server(payload: str) -> str | None:
    patterns = (
        re.compile(r"^refer:\s*(.+)$", flags=re.IGNORECASE),
        re.compile(r"^whois:\s*(.+)$", flags=re.IGNORECASE),
        re.compile(r"^ReferralServer:\s*(.+)$", flags=re.IGNORECASE),
    )
    for line in payload.splitlines():
        for pattern in patterns:
            match = pattern.match(line.strip())
            if not match:
                continue
            raw = match.group(1).strip().split()[0]
            normalized = _normalize_referral_host(raw)
            if normalized:
                return normalized
    return None


def _normalize_referral_host(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered.startswith("whois://"):
        cleaned = cleaned[8:]
    elif lowered.startswith("rwhois://"):
        cleaned = cleaned[9:]
    cleaned = cleaned.split("/")[0].strip()
    if cleaned.count(":") == 1 and cleaned.rsplit(":", 1)[1].isdigit():
        cleaned = cleaned.rsplit(":", 1)[0]
    return cleaned.strip()
