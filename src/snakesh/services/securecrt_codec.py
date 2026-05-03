from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable
from xml.etree import ElementTree

from snakesh.core.models import Protocol, Session, is_auto_resolution, parse_resolution


_LINE_RE = re.compile(r'^(?P<kind>[SDB]):"(?P<key>[^"]+)"=(?P<value>.*)$')
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]+')
_SKIP_FILENAMES = {"__folderdata__.ini"}
_VALID_COLOR_DEPTHS = {8, 16, 24, 32}
_DEFAULT_PORTS = {
    Protocol.SSH: 22,
    Protocol.SFTP: 22,
    Protocol.RDP: 3389,
    Protocol.VNC: 5900,
}
_HIERARCHY_ROOT_NAMES = {
    "sessions",
    "session",
    "session manager",
    "sessionmanager",
    "connect",
    "connections",
}


@dataclass(slots=True)
class SecureCRTImportReport:
    scanned_files: int
    imported_sessions: list[Session]
    folders: list[str]
    skipped_files: list[str]
    warnings: list[str]

    @property
    def imported_count(self) -> int:
        return len(self.imported_sessions)


@dataclass(slots=True)
class SecureCRTExportReport:
    exported_count: int
    destination_path: Path
    warnings: list[str]


class SecureCRTCodecService:
    def import_from_path(self, source_path: Path) -> SecureCRTImportReport:
        source = source_path.expanduser()
        if not source.exists():
            raise FileNotFoundError(f"SecureCRT path does not exist: {source}")

        if source.is_file():
            if source.suffix.lower() == ".xml":
                return self._import_from_xml(source)
            files = [source]
            root_dir = source.parent
            folder_seed: set[str] = set()
        else:
            files = self._ini_files_in_tree(source)
            root_dir = source
            folder_seed = self._folders_in_tree(source)

        sessions: list[Session] = []
        skipped: list[str] = []
        warnings: list[str] = []
        folders: set[str] = set(folder_seed)

        for file_path in files:
            if file_path.name.lower() in _SKIP_FILENAMES:
                skipped.append(str(file_path))
                continue
            if file_path.suffix.lower() != ".ini":
                skipped.append(str(file_path))
                continue
            try:
                parsed = self._parse_file(file_path)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{file_path.name}: unable to parse ({exc})")
                continue

            folder = self._folder_for_file(file_path, root_dir)
            session = self._session_from_parsed(parsed, file_path.stem, folder)
            if session is None:
                skipped.append(str(file_path))
                warnings.append(f"{file_path.name}: skipped because host/protocol data is incomplete.")
                continue
            sessions.append(session)
            self._add_folder_with_parents(folders, session.folder)

        return SecureCRTImportReport(
            scanned_files=len(files),
            imported_sessions=sessions,
            folders=sorted(folders, key=self._folder_sort_key),
            skipped_files=skipped,
            warnings=warnings,
        )

    def _import_from_xml(self, xml_path: Path) -> SecureCRTImportReport:
        raw = xml_path.read_text(encoding="utf-8-sig", errors="ignore")
        decoded = html.unescape(raw).replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")

        sessions_by_identity: dict[tuple[str, str, str, int, str], Session] = {}
        folders: set[str] = set()
        warnings: list[str] = []
        skipped: list[str] = []
        scanned = 0

        for index, block in enumerate(self._extract_ini_blocks(decoded), start=1):
            parsed = self._parse_lines(block.splitlines())
            fallback_name = f"{xml_path.stem}-{index}"
            folder = self._normalize_folder(
                self._first_string(
                    parsed,
                    ["Folder", "Path", "Session Folder", "Session Path", "Folder Name", "FolderName"],
                )
                or "Default"
            )
            session = self._session_from_parsed(parsed, fallback_name, folder)
            scanned += 1
            if session is None:
                continue
            self._store_preferred_session(sessions_by_identity, session)
            self._add_folder_with_parents(folders, session.folder)

        try:
            root = ElementTree.fromstring(raw)
            for parsed, fallback_name, folder in self._extract_xml_session_maps(root):
                scanned += 1
                session = self._session_from_parsed(parsed, fallback_name, folder)
                if session is None:
                    continue
                self._store_preferred_session(sessions_by_identity, session)
                self._add_folder_with_parents(folders, session.folder)
        except ElementTree.ParseError as exc:
            warnings.append(f"{xml_path.name}: XML parse warning ({exc})")

        sessions = list(sessions_by_identity.values())
        if not sessions:
            skipped.append(str(xml_path))
            if not warnings:
                warnings.append(
                    f"{xml_path.name}: no recognizable SecureCRT session entries were found."
                )

        return SecureCRTImportReport(
            scanned_files=max(1, scanned),
            imported_sessions=sessions,
            folders=sorted(folders, key=self._folder_sort_key),
            skipped_files=skipped,
            warnings=warnings,
        )

    def export_sessions(
        self,
        sessions: Iterable[Session],
        destination_dir: Path,
    ) -> SecureCRTExportReport:
        destination = destination_dir.expanduser()
        destination.mkdir(parents=True, exist_ok=True)

        warnings: list[str] = []
        used_paths: set[Path] = set()
        exported = 0

        for session in sessions:
            folder = self._normalize_folder(session.folder)
            session_dir = destination if folder == "Default" else destination.joinpath(*folder.split("/"))
            session_dir.mkdir(parents=True, exist_ok=True)
            file_stem = self._safe_file_stem(session.name or session.host or "Session")
            session_path = self._resolve_export_path(session_dir, file_stem, used_paths)
            try:
                session_path.write_text(self._serialize_session(session), encoding="utf-8")
                exported += 1
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{session.name or session.host}: export failed ({exc})")

        return SecureCRTExportReport(
            exported_count=exported,
            destination_path=destination,
            warnings=warnings,
        )

    def export_xml(
        self,
        sessions: Iterable[Session],
        destination_path: Path,
    ) -> SecureCRTExportReport:
        path = destination_path.expanduser()
        if path.suffix.lower() != ".xml":
            path = path.with_suffix(".xml")
        path.parent.mkdir(parents=True, exist_ok=True)

        root = ElementTree.Element("SecureCRTSessionExport")
        root.set("source", "SnakeSh")
        root.set("version", "1")

        exported = 0
        warnings: list[str] = []
        for session in sessions:
            try:
                folder = self._normalize_folder(session.folder)
                entry = ElementTree.SubElement(
                    root,
                    "Session",
                    {
                        "name": session.name,
                        "folder": folder,
                        "protocol": self._protocol_name_for_export(session.protocol),
                    },
                )
                ElementTree.SubElement(
                    entry,
                    "Field",
                    {"name": "Session Folder", "value": folder},
                )
                ElementTree.SubElement(
                    entry,
                    "Field",
                    {"name": "Session Path", "value": f"{folder}/{session.name}.ini"},
                )
                ini_data = ElementTree.SubElement(entry, "SessionIni")
                ini_data.text = self._serialize_session(session)
                exported += 1
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{session.name or session.host}: export failed ({exc})")

        tree = ElementTree.ElementTree(root)
        try:
            ElementTree.indent(tree, space="  ")  # type: ignore[attr-defined]
        except Exception:
            pass
        tree.write(path, encoding="utf-8", xml_declaration=True)

        return SecureCRTExportReport(
            exported_count=exported,
            destination_path=path,
            warnings=warnings,
        )

    @staticmethod
    def _ini_files_in_tree(root_dir: Path) -> list[Path]:
        files = [
            file_path
            for file_path in root_dir.rglob("*")
            if file_path.is_file() and file_path.suffix.lower() == ".ini"
        ]
        return sorted(files)

    @staticmethod
    def _folder_for_file(file_path: Path, root_dir: Path) -> str:
        try:
            relative_parent = file_path.parent.relative_to(root_dir)
        except Exception:
            return "Default"
        if str(relative_parent) in ("", "."):
            return "Default"
        cleaned = str(relative_parent).replace("\\", "/").strip("/")
        return cleaned or "Default"

    @staticmethod
    def _folders_in_tree(root_dir: Path) -> set[str]:
        folders: set[str] = set()
        for dir_path in root_dir.rglob("*"):
            if not dir_path.is_dir():
                continue
            try:
                relative = dir_path.relative_to(root_dir)
            except Exception:
                continue
            if str(relative) in ("", "."):
                continue
            normalized = SecureCRTCodecService._normalize_folder(str(relative))
            if normalized != "Default":
                folders.add(normalized)
        return folders

    @staticmethod
    def _parse_file(file_path: Path) -> dict[str, object]:
        return SecureCRTCodecService._parse_lines(
            file_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
        )

    @staticmethod
    def _parse_lines(lines: list[str]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for line in lines:
            setting_line = SecureCRTCodecService._coerce_setting_line(line)
            if setting_line is None:
                continue
            match = _LINE_RE.match(setting_line)
            if not match:
                continue
            kind = match.group("kind")
            key = match.group("key")
            value = match.group("value").strip()
            if kind == "S":
                parsed[key] = value
                continue
            if kind == "D":
                number = SecureCRTCodecService._parse_numeric(value)
                if number is not None:
                    parsed[key] = number
                continue
            if kind == "B":
                number = SecureCRTCodecService._parse_numeric(value)
                if number is not None:
                    parsed[key] = bool(number)
                else:
                    parsed[key] = value.lower() in {"1", "true", "yes", "on"}
        return parsed

    @staticmethod
    def _extract_ini_blocks(text: str) -> list[str]:
        blocks: list[str] = []
        current: list[str] = []
        for line in text.splitlines():
            setting_line = SecureCRTCodecService._coerce_setting_line(line)
            if setting_line is not None and _LINE_RE.match(setting_line):
                current.append(setting_line)
                continue
            if current:
                blocks.append("\n".join(current))
                current = []
        if current:
            blocks.append("\n".join(current))
        return blocks

    @staticmethod
    def _coerce_setting_line(raw_line: str) -> str | None:
        stripped = raw_line.strip()
        if not stripped:
            return None
        if _LINE_RE.match(stripped):
            return stripped
        marker_positions = [index for index in (stripped.find('S:"'), stripped.find('D:"'), stripped.find('B:"')) if index >= 0]
        if not marker_positions:
            return None
        candidate = stripped[min(marker_positions) :]
        if _LINE_RE.match(candidate):
            return candidate
        return None

    def _extract_xml_session_maps(
        self,
        root: ElementTree.Element,
    ) -> list[tuple[dict[str, object], str, str]]:
        parent_map: dict[ElementTree.Element, ElementTree.Element] = {}
        for parent in root.iter():
            for child in list(parent):
                parent_map[child] = parent

        candidates: list[tuple[dict[str, object], str, str]] = []
        candidates.extend(self._extract_hierarchical_key_sessions(root, parent_map))
        for element in root.iter():
            parsed: dict[str, object] = {}
            for attr_key, attr_value in element.attrib.items():
                if attr_value:
                    parsed[attr_key] = attr_value

            text_value = (element.text or "").strip()
            if text_value and any(token in text_value for token in ('S:"', 'D:"', 'B:"')):
                parsed.update(self._parse_lines(text_value.splitlines()))

            ancestor = parent_map.get(element)
            ancestor_index = 0
            while ancestor is not None and ancestor_index < 12:
                for attr_key, attr_value in ancestor.attrib.items():
                    if not attr_value:
                        continue
                    key_name = f"ancestor_{ancestor_index}_{attr_key}"
                    parsed[key_name] = attr_value
                ancestor = parent_map.get(ancestor)
                ancestor_index += 1

            for child in list(element):
                key = self._xml_key_for_element(child)
                if not key:
                    continue
                value = self._xml_value_for_element(child)
                if value is None:
                    continue
                if any(token in value for token in ('S:"', 'D:"', 'B:"')):
                    parsed.update(self._parse_lines(value.splitlines()))
                    continue
                coerced: object = value
                numeric = self._parse_numeric(value)
                if numeric is not None and (
                    key.endswith("Port")
                    or "Width" in key
                    or "Height" in key
                    or "Color" in key
                    or key in {"Port", "Desktop Width", "Desktop Height", "Color Depth"}
                ):
                    coerced = numeric
                parsed[key] = coerced

            if not parsed:
                continue
            if not self._looks_like_session_data(parsed):
                continue

            fallback_name = (
                self._first_string(parsed, ["Session Name", "Name", "Session"])
                or (element.attrib.get("name", "") or "").strip()
                or element.tag
            )
            folder = self._resolve_folder_from_parsed(
                parsed,
                "Default",
                fallback_name=fallback_name,
            )
            candidates.append((parsed, fallback_name, folder))
        return candidates

    def _extract_hierarchical_key_sessions(
        self,
        root: ElementTree.Element,
        parent_map: dict[ElementTree.Element, ElementTree.Element],
    ) -> list[tuple[dict[str, object], str, str]]:
        candidates: list[tuple[dict[str, object], str, str]] = []
        for element in root.iter():
            if self._element_tag(element) != "key":
                continue
            session_name = (element.attrib.get("name", "") or "").strip()
            if not session_name:
                continue

            parsed = self._parse_key_node_settings(element)
            if not parsed:
                continue
            if not self._looks_like_session_data(parsed):
                continue

            key_chain = self._key_name_chain(element, parent_map)
            key_chain = self._trim_hierarchy_roots(key_chain)
            if key_chain:
                session_name = key_chain[-1]
            folder_parts = key_chain[:-1] if len(key_chain) >= 2 else []
            folder = self._normalize_folder("/".join(folder_parts)) if folder_parts else "Default"

            parsed.setdefault("Session Name", session_name)
            parsed.setdefault("Session Folder", folder)
            candidates.append((parsed, session_name, folder))
        return candidates

    def _parse_key_node_settings(self, key_node: ElementTree.Element) -> dict[str, object]:
        parsed: dict[str, object] = {}
        stack: list[ElementTree.Element] = list(key_node)
        while stack:
            node = stack.pop()
            if self._element_tag(node) == "key":
                # Nested keys represent child folders/sessions in SecureCRT hierarchy.
                continue

            key = self._xml_key_for_element(node)
            value = self._xml_value_for_element(node)
            if key and value is not None:
                if any(token in value for token in ('S:"', 'D:"', 'B:"')):
                    parsed.update(self._parse_lines(value.splitlines()))
                else:
                    coerced: object = value
                    numeric = self._parse_numeric(value)
                    if numeric is not None and (
                        key.endswith("Port")
                        or "Width" in key
                        or "Height" in key
                        or "Color" in key
                        or key in {"Port", "Desktop Width", "Desktop Height", "Color Depth"}
                    ):
                        coerced = numeric
                    parsed[key] = coerced

            stack.extend(list(node))
        return parsed

    def _key_name_chain(
        self,
        element: ElementTree.Element,
        parent_map: dict[ElementTree.Element, ElementTree.Element],
    ) -> list[str]:
        names: list[str] = []
        current: ElementTree.Element | None = element
        while current is not None:
            if self._element_tag(current) == "key":
                name = (current.attrib.get("name", "") or "").strip()
                if name:
                    names.append(name)
            current = parent_map.get(current)
        names.reverse()
        return names

    @staticmethod
    def _trim_hierarchy_roots(chain: list[str]) -> list[str]:
        trimmed = [entry.strip() for entry in chain if entry.strip()]
        while trimmed and trimmed[0].strip().lower() in _HIERARCHY_ROOT_NAMES:
            trimmed = trimmed[1:]
        return trimmed

    @staticmethod
    def _element_tag(element: ElementTree.Element) -> str:
        if "}" in element.tag:
            return element.tag.rsplit("}", 1)[-1].strip().lower()
        return element.tag.strip().lower()

    @staticmethod
    def _xml_key_for_element(element: ElementTree.Element) -> str | None:
        for attr_key in ("name", "key", "id", "field"):
            candidate = element.attrib.get(attr_key)
            if candidate:
                return candidate.strip()
        tag = element.tag.rsplit("}", 1)[-1] if "}" in element.tag else element.tag
        tag = tag.strip()
        if tag and tag.lower() not in {"item", "entry", "node", "value", "property", "option"}:
            return tag
        return None

    @staticmethod
    def _xml_value_for_element(element: ElementTree.Element) -> str | None:
        for attr_key in ("value", "val", "data"):
            candidate = element.attrib.get(attr_key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        text = (element.text or "").strip()
        if text:
            return text
        return None

    @staticmethod
    def _looks_like_session_data(parsed: dict[str, object]) -> bool:
        host_keys = {
            "Hostname",
            "[SSH2] Hostname",
            "[SFTP] Hostname",
            "[RDP] Hostname",
            "[VNC] Hostname",
        }
        if any(key in parsed for key in host_keys):
            return True
        for key in parsed:
            key_text = str(key).strip().lower()
            if "host" in key_text and "name" in key_text:
                return True
            if "address" in key_text and "ip" in key_text:
                return True
        return False

    @staticmethod
    def _session_identity(session: Session) -> tuple[str, str, str, int, str]:
        return (
            session.name,
            session.host,
            session.protocol.value,
            int(session.port),
            session.username,
        )

    def _store_preferred_session(
        self,
        target: dict[tuple[str, str, str, int, str], Session],
        candidate: Session,
    ) -> None:
        identity = self._session_identity(candidate)
        existing = target.get(identity)
        if existing is None:
            target[identity] = candidate
            return
        if existing.folder == "Default" and candidate.folder != "Default":
            target[identity] = candidate

    @staticmethod
    def _parse_numeric(value: str) -> int | None:
        cleaned = value.strip()
        if not cleaned:
            return None
        sign = -1 if cleaned.startswith("-") else 1
        if sign < 0:
            cleaned = cleaned[1:]
        if cleaned.lower().startswith("0x"):
            cleaned = cleaned[2:]
        if not cleaned:
            return None
        if re.fullmatch(r"[0-9a-fA-F]{8}", cleaned):
            # SecureCRT stores numeric D:/B: values as 8-char hex words.
            return sign * int(cleaned, 16)
        if cleaned.isdigit():
            return sign * int(cleaned, 10)
        if re.fullmatch(r"[0-9a-fA-F]+", cleaned):
            return sign * int(cleaned, 16)
        return None

    def _session_from_parsed(
        self,
        parsed: dict[str, object],
        fallback_name: str,
        folder: str,
    ) -> Session | None:
        protocol_name = self._first_string(parsed, ["Protocol Name"]) or "SSH2"
        protocol = self._protocol_from_name(protocol_name)
        host = self._first_string(
            parsed,
            [
                "Hostname",
                "[SSH2] Hostname",
                "[SFTP] Hostname",
                "[RDP] Hostname",
                "[VNC] Hostname",
            ],
        )
        if not host:
            return None

        if protocol in (Protocol.SSH, Protocol.SFTP):
            port_key_candidates = ["[SSH2] Port", "[SFTP] Port", "Port"]
        elif protocol == Protocol.RDP:
            port_key_candidates = ["[RDP] Port", "Port"]
        else:
            port_key_candidates = ["[VNC] Port", "Port"]
        port = self._first_int(parsed, port_key_candidates)
        if port is None or port <= 0:
            port = _DEFAULT_PORTS[protocol]

        width = self._first_int(parsed, ["Desktop Width", "[RDP] Desktop Width"])
        height = self._first_int(parsed, ["Desktop Height", "[RDP] Desktop Height"])
        resolution = ""
        if width and height and width > 0 and height > 0:
            resolution = f"{width}x{height}"
        else:
            raw_resolution = self._first_string(parsed, ["Resolution", "[RDP] Resolution"])
            if raw_resolution and is_auto_resolution(raw_resolution):
                resolution = "auto"
            else:
                parsed_resolution = parse_resolution(raw_resolution or "")
                if parsed_resolution:
                    resolution = f"{parsed_resolution[0]}x{parsed_resolution[1]}"

        color_depth = self._first_int(parsed, ["Color Depth", "[RDP] Color Depth"]) or 0
        if color_depth not in _VALID_COLOR_DEPTHS:
            color_depth = 0

        auth_method = (self._first_string(parsed, ["Authentication Method", "[SSH2] Authentication Method"]) or "").lower()
        use_key_auth = True
        if protocol in (Protocol.SSH, Protocol.SFTP) and auth_method:
            use_key_auth = "password" not in auth_method and "keyboard" not in auth_method

        save_password = bool(
            self._first_bool(parsed, ["Save Session Password", "[SSH2] Save Session Password", "Save Password"])
            or False
        )
        x11_forwarding = bool(self._first_bool(parsed, ["Forward X11", "[SSH2] Forward X11"]) or False)

        name = (self._first_string(parsed, ["Session Name"]) or fallback_name).strip()
        folder = self._resolve_folder_from_parsed(parsed, folder, fallback_name=name or fallback_name)
        name, folder = self._resolve_session_name_and_folder(name, fallback_name, folder)
        if not name:
            name = host

        return Session(
            name=name,
            host=host.strip(),
            protocol=protocol,
            port=port,
            username=(self._first_string(parsed, ["Username", "[SSH2] Username", "[RDP] Username"]) or "").strip(),
            domain=(self._first_string(parsed, ["Domain", "[RDP] Domain"]) or "").strip(),
            display_resolution=resolution,
            display_fullscreen=bool(self._first_bool(parsed, ["Full Screen", "[RDP] Full Screen"]) or False),
            display_color_depth=color_depth,
            notes=(self._first_string(parsed, ["Description", "Comments", "Comment"]) or "").strip(),
            use_key_auth=use_key_auth,
            save_password=save_password,
            x11_forwarding=x11_forwarding,
            folder=self._normalize_folder(folder),
        )

    @staticmethod
    def _resolve_session_name_and_folder(
        parsed_name: str,
        fallback_name: str,
        folder: str,
    ) -> tuple[str, str]:
        name = (parsed_name or fallback_name).strip()
        cleaned = name.replace("\\", "/").strip("/")
        if "/" not in cleaned:
            return name, folder

        parts = [part.strip() for part in cleaned.split("/") if part.strip()]
        if len(parts) < 2:
            return name, folder

        resolved_name = parts[-1]
        if SecureCRTCodecService._normalize_folder(folder) != "Default":
            return resolved_name, folder
        resolved_folder = "/".join(parts[:-1])
        return resolved_name, resolved_folder

    @staticmethod
    def _add_folder_with_parents(folders: set[str], folder_path: str) -> None:
        normalized = SecureCRTCodecService._normalize_folder(folder_path)
        if normalized == "Default":
            return
        current_parts: list[str] = []
        for part in normalized.split("/"):
            current_parts.append(part)
            folders.add("/".join(current_parts))

    def _resolve_folder_from_parsed(
        self,
        parsed: dict[str, object],
        fallback_folder: str,
        *,
        fallback_name: str = "",
    ) -> str:
        preferred = self._first_string(
            parsed,
            [
                "Folder",
                "Path",
                "Session Folder",
                "Session Path",
                "Folder Name",
                "FolderName",
                "folder",
                "path",
                "session_folder",
                "session_path",
            ],
        )
        if preferred:
            normalized = self._normalize_folder_candidate(preferred, fallback_name=fallback_name)
            if normalized:
                return normalized

        for key, value in parsed.items():
            if not isinstance(value, str):
                continue
            key_norm = str(key).strip().lower()
            if not any(token in key_norm for token in ("folder", "path", "file", "directory")):
                continue
            normalized = self._normalize_folder_candidate(value, fallback_name=fallback_name)
            if normalized:
                return normalized

        normalized_fallback = self._normalize_folder_candidate(
            fallback_folder,
            fallback_name=fallback_name,
        )
        if normalized_fallback:
            return normalized_fallback
        return "Default"

    def _normalize_folder_candidate(self, value: str, *, fallback_name: str = "") -> str | None:
        cleaned = value.strip()
        if not cleaned:
            return None

        lowered = cleaned.lower()
        if lowered in {"default", ".", "./", "/"}:
            return None
        if "://" in cleaned:
            return None

        normalized = cleaned.replace("\\", "/").strip()
        normalized = re.sub(r"^[A-Za-z]:", "", normalized).strip("/")
        if not normalized:
            return None

        parts = [part.strip() for part in normalized.split("/") if part.strip() and part.strip() not in {".", ".."}]
        if not parts:
            return None

        lower_parts = [part.lower() for part in parts]
        if "sessions" in lower_parts:
            idx = lower_parts.index("sessions")
            parts = parts[idx + 1 :]
        elif "session" in lower_parts:
            idx = lower_parts.index("session")
            parts = parts[idx + 1 :]
        if not parts:
            return None

        last = parts[-1]
        if last.lower().endswith(".ini") or last.lower().endswith(".xml"):
            parts = parts[:-1]
        elif fallback_name and last.lower() == fallback_name.strip().lower():
            parts = parts[:-1]
        if not parts:
            return None

        candidate = self._normalize_folder("/".join(parts))
        if candidate == "Default":
            return None
        return candidate

    @staticmethod
    def _first_string(parsed: dict[str, object], keys: list[str]) -> str | None:
        for key in keys:
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _first_int(parsed: dict[str, object], keys: list[str]) -> int | None:
        for key in keys:
            value = parsed.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
        return None

    @staticmethod
    def _first_bool(parsed: dict[str, object], keys: list[str]) -> bool | None:
        for key in keys:
            value = parsed.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, int):
                return bool(value)
            if isinstance(value, str) and value.strip():
                lowered = value.strip().lower()
                if lowered in {"1", "true", "yes", "on"}:
                    return True
                if lowered in {"0", "false", "no", "off"}:
                    return False
        return None

    @staticmethod
    def _protocol_from_name(protocol_name: str) -> Protocol:
        normalized = protocol_name.strip().lower()
        if "sftp" in normalized:
            return Protocol.SFTP
        if "rdp" in normalized:
            return Protocol.RDP
        if "vnc" in normalized:
            return Protocol.VNC
        return Protocol.SSH

    @staticmethod
    def _protocol_name_for_export(protocol: Protocol) -> str:
        if protocol == Protocol.SFTP:
            return "SFTP"
        if protocol == Protocol.RDP:
            return "RDP"
        if protocol == Protocol.VNC:
            return "VNC"
        return "SSH2"

    @staticmethod
    def _port_key_for_export(protocol: Protocol) -> str:
        if protocol in (Protocol.SSH, Protocol.SFTP):
            return "[SSH2] Port"
        if protocol == Protocol.RDP:
            return "[RDP] Port"
        if protocol == Protocol.VNC:
            return "[VNC] Port"
        return "[SSH2] Port"

    def _serialize_session(self, session: Session) -> str:
        lines = [
            f'S:"Session Name"={self._safe_value(session.name)}',
            f'S:"Protocol Name"={self._protocol_name_for_export(session.protocol)}',
            f'S:"Hostname"={self._safe_value(session.host)}',
        ]
        if session.username:
            lines.append(f'S:"Username"={self._safe_value(session.username)}')
        if session.domain:
            lines.append(f'S:"Domain"={self._safe_value(session.domain)}')
        if session.notes:
            lines.append(f'S:"Description"={self._safe_value(session.notes)}')

        lines.append(f'D:"{self._port_key_for_export(session.protocol)}"={self._format_number(session.port)}')

        if session.protocol in (Protocol.SSH, Protocol.SFTP):
            auth_method = "publickey" if session.use_key_auth else "password"
            lines.append(f'S:"Authentication Method"={auth_method}')
            lines.append(f'B:"Forward X11"={self._format_bool(session.x11_forwarding)}')
            lines.append(f'B:"Save Session Password"={self._format_bool(session.save_password)}')

        if session.protocol in (Protocol.RDP, Protocol.VNC) or session.display_fullscreen:
            lines.append(f'B:"Full Screen"={self._format_bool(session.display_fullscreen)}')

        if session.display_resolution:
            if is_auto_resolution(session.display_resolution):
                lines.append('S:"Resolution"=Auto')
            else:
                resolution = parse_resolution(session.display_resolution)
                if resolution:
                    width, height = resolution
                    lines.append(f'D:"Desktop Width"={self._format_number(width)}')
                    lines.append(f'D:"Desktop Height"={self._format_number(height)}')

        if session.display_color_depth in _VALID_COLOR_DEPTHS:
            lines.append(f'D:"Color Depth"={self._format_number(session.display_color_depth)}')

        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _normalize_folder(folder_path: str) -> str:
        cleaned = folder_path.replace("\\", "/").strip("/")
        parts = [part.strip() for part in cleaned.split("/") if part.strip()]
        if not parts:
            return "Default"
        return "/".join(parts)

    @staticmethod
    def _safe_value(value: str) -> str:
        return value.replace("\r", " ").replace("\n", " ").strip()

    @staticmethod
    def _safe_file_stem(candidate: str) -> str:
        cleaned = _INVALID_FILENAME_CHARS.sub("_", candidate).strip().strip(".")
        return cleaned or "Session"

    @staticmethod
    def _resolve_export_path(base_dir: Path, file_stem: str, used_paths: set[Path]) -> Path:
        candidate = base_dir / f"{file_stem}.ini"
        counter = 2
        while candidate.exists() or candidate in used_paths:
            candidate = base_dir / f"{file_stem} ({counter}).ini"
            counter += 1
        used_paths.add(candidate)
        return candidate

    @staticmethod
    def _format_number(value: int) -> str:
        return f"{max(0, int(value)):08x}"

    @staticmethod
    def _format_bool(value: bool) -> str:
        return "00000001" if value else "00000000"

    @staticmethod
    def _folder_sort_key(folder_path: str) -> tuple[int, str]:
        return folder_path.count("/"), folder_path.lower()
