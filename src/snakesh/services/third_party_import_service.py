from __future__ import annotations

from dataclasses import dataclass
import glob
from pathlib import Path
import re
import shlex
from typing import Any
from urllib.parse import unquote

from snakesh.core.models import Protocol, Session


_WILDCARD_RE = re.compile(r"[*?]")


@dataclass(slots=True)
class ThirdPartyImportReport:
    source_name: str
    scanned_entries: int
    imported_sessions: list[Session]
    folders: list[str]
    warnings: list[str]

    @property
    def imported_count(self) -> int:
        return len(self.imported_sessions)


class ThirdPartyImportService:
    def import_openssh_config(self, config_path: Path) -> ThirdPartyImportReport:
        source = config_path.expanduser()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"OpenSSH config was not found: {source}")

        warnings: list[str] = []
        blocks = self._parse_openssh_file(source, visited=set(), warnings=warnings)
        sessions: list[Session] = []
        folders: set[str] = set()
        identities: set[tuple[str, str, int, str]] = set()
        scanned = 0

        for origin_path, aliases, options in blocks:
            scanned += 1
            for alias in aliases:
                if alias.startswith("!") or _WILDCARD_RE.search(alias):
                    continue

                host = self._strip_quotes(options.get("hostname", alias)).strip()
                if not host:
                    warnings.append(f"{origin_path.name}: host alias '{alias}' has no resolved hostname.")
                    continue

                username = self._strip_quotes(options.get("user", "")).strip()
                port = self._safe_int(options.get("port"), 22)
                identity = self._expand_identity_path(options.get("identityfile", "").strip())
                x11_forwarding = self._parse_bool(options.get("forwardx11", ""))
                folder = "Imported/OpenSSH"
                self._add_folder_with_parents(folders, folder)

                dedupe_key = (alias.lower(), host.lower(), port, username.lower())
                if dedupe_key in identities:
                    continue
                identities.add(dedupe_key)

                session = Session(
                    name=alias,
                    host=host,
                    protocol=Protocol.SSH,
                    port=port,
                    username=username,
                    use_key_auth=True,
                    private_key_path=identity,
                    x11_forwarding=x11_forwarding,
                    folder=folder,
                    notes=f"Imported from OpenSSH config: {origin_path}",
                    tags=["imported", "openssh"],
                )
                sessions.append(session)

        return ThirdPartyImportReport(
            source_name="OpenSSH",
            scanned_entries=scanned,
            imported_sessions=sessions,
            folders=sorted(folders, key=self._folder_sort_key),
            warnings=warnings,
        )

    def import_putty_registry(self) -> ThirdPartyImportReport:
        warnings: list[str] = []
        scanned = 0
        sessions: list[Session] = []
        folders: set[str] = set()
        dedupe: set[tuple[str, str, int, str]] = set()

        try:
            import winreg  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("PuTTY registry import is only available on Windows.") from exc

        base_path = r"Software\SimonTatham\PuTTY\Sessions"
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base_path)
        except FileNotFoundError:
            return ThirdPartyImportReport(
                source_name="PuTTY",
                scanned_entries=0,
                imported_sessions=[],
                folders=["Imported/PuTTY"],
                warnings=["No PuTTY sessions were found in the current user registry hive."],
            )

        try:
            index = 0
            while True:
                try:
                    encoded_name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                scanned += 1
                decoded_name = unquote(encoded_name)
                if decoded_name.strip().lower() == "default settings":
                    continue

                try:
                    session_key = winreg.OpenKey(root, encoded_name)
                except OSError as exc:
                    warnings.append(f"{decoded_name}: unable to open registry key ({exc})")
                    continue

                try:
                    protocol = str(self._query_registry_value(session_key, "Protocol", "ssh")).strip().lower()
                    if protocol and protocol != "ssh":
                        warnings.append(f"{decoded_name}: skipped unsupported protocol '{protocol}'.")
                        continue

                    host = str(self._query_registry_value(session_key, "HostName", "")).strip()
                    if not host:
                        warnings.append(f"{decoded_name}: skipped because HostName is empty.")
                        continue

                    username = str(self._query_registry_value(session_key, "UserName", "")).strip()
                    port = self._safe_int(self._query_registry_value(session_key, "PortNumber", 22), 22)
                    key_file = str(self._query_registry_value(session_key, "PublicKeyFile", "")).strip()
                    x11_forwarding = bool(self._safe_int(self._query_registry_value(session_key, "X11Forward", 0), 0))

                    folder, name = self._putty_folder_and_name(decoded_name)
                    self._add_folder_with_parents(folders, folder)
                    dedupe_key = (name.lower(), host.lower(), port, username.lower())
                    if dedupe_key in dedupe:
                        continue
                    dedupe.add(dedupe_key)

                    session = Session(
                        name=name,
                        host=host,
                        protocol=Protocol.SSH,
                        port=port,
                        username=username,
                        use_key_auth=bool(key_file),
                        private_key_path=key_file,
                        x11_forwarding=x11_forwarding,
                        folder=folder,
                        notes="Imported from PuTTY registry.",
                        tags=["imported", "putty"],
                    )
                    sessions.append(session)
                finally:
                    try:
                        winreg.CloseKey(session_key)
                    except Exception:
                        pass
        finally:
            try:
                winreg.CloseKey(root)
            except Exception:
                pass

        if not folders:
            folders.add("Imported/PuTTY")

        return ThirdPartyImportReport(
            source_name="PuTTY",
            scanned_entries=scanned,
            imported_sessions=sessions,
            folders=sorted(folders, key=self._folder_sort_key),
            warnings=warnings,
        )

    def _parse_openssh_file(
        self,
        config_path: Path,
        *,
        visited: set[Path],
        warnings: list[str],
    ) -> list[tuple[Path, list[str], dict[str, str]]]:
        resolved = config_path.expanduser().resolve()
        if resolved in visited:
            return []
        visited.add(resolved)

        try:
            content = resolved.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{resolved.name}: unable to read file ({exc})")
            return []

        blocks: list[tuple[Path, list[str], dict[str, str]]] = []
        current_aliases: list[str] = []
        current_options: dict[str, str] = {}

        def push_current() -> None:
            if current_aliases:
                blocks.append((resolved, list(current_aliases), dict(current_options)))

        for raw_line in content.splitlines():
            line = self._strip_openssh_comment(raw_line).strip()
            if not line:
                continue

            parts = line.split(None, 1)
            if not parts:
                continue
            key = parts[0].strip().lower()
            value = parts[1].strip() if len(parts) > 1 else ""

            if key == "include":
                for include_path in self._resolve_openssh_includes(base_file=resolved, value=value):
                    blocks.extend(self._parse_openssh_file(include_path, visited=visited, warnings=warnings))
                continue

            if key == "host":
                push_current()
                current_aliases = [entry.strip() for entry in value.split() if entry.strip()]
                current_options = {}
                continue

            if not current_aliases:
                continue

            if key not in current_options:
                current_options[key] = value

        push_current()
        return blocks

    @staticmethod
    def _resolve_openssh_includes(*, base_file: Path, value: str) -> list[Path]:
        entries: list[str] = []
        try:
            entries = shlex.split(value, posix=True)
        except ValueError:
            entries = [value.strip()]
        if not entries:
            return []

        resolved: list[Path] = []
        for entry in entries:
            expanded = Path(entry).expanduser()
            if not expanded.is_absolute():
                expanded = (base_file.parent / expanded).expanduser()
            for match_str in sorted(glob.glob(str(expanded), recursive=True)):
                match = Path(match_str)
                if match.is_file():
                    resolved.append(match)
        return resolved

    @staticmethod
    def _strip_openssh_comment(line: str) -> str:
        in_single = False
        in_double = False
        out_chars: list[str] = []
        for ch in line:
            if ch == "'" and not in_double:
                in_single = not in_single
                out_chars.append(ch)
                continue
            if ch == '"' and not in_single:
                in_double = not in_double
                out_chars.append(ch)
                continue
            if ch == "#" and not in_single and not in_double:
                break
            out_chars.append(ch)
        return "".join(out_chars)

    @staticmethod
    def _strip_quotes(value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) >= 2 and trimmed[0] == trimmed[-1] and trimmed[0] in ('"', "'"):
            return trimmed[1:-1]
        return trimmed

    @classmethod
    def _expand_identity_path(cls, value: str) -> str:
        cleaned = cls._strip_quotes(value).strip()
        if not cleaned:
            return ""
        return str(Path(cleaned).expanduser())

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _parse_bool(value: str) -> bool:
        lowered = value.strip().lower()
        return lowered in {"yes", "true", "1", "on"}

    @staticmethod
    def _query_registry_value(key, value_name: str, default: Any) -> Any:
        try:
            import winreg  # type: ignore[import-not-found]

            value, _kind = winreg.QueryValueEx(key, value_name)
            return value
        except Exception:
            return default

    @staticmethod
    def _putty_folder_and_name(session_name: str) -> tuple[str, str]:
        normalized = session_name.replace("\\", "/").strip("/")
        parts = [part.strip() for part in normalized.split("/") if part.strip()]
        if not parts:
            return "Imported/PuTTY", "Session"
        if len(parts) == 1:
            return "Imported/PuTTY", parts[0]
        folder = "Imported/PuTTY/" + "/".join(parts[:-1])
        return folder, parts[-1]

    @staticmethod
    def _add_folder_with_parents(folders: set[str], folder_path: str) -> None:
        parts = [part.strip() for part in folder_path.replace("\\", "/").strip("/").split("/") if part.strip()]
        if not parts:
            folders.add("Default")
            return
        current: list[str] = []
        for part in parts:
            current.append(part)
            folders.add("/".join(current))

    @staticmethod
    def _folder_sort_key(folder_path: str) -> tuple[int, str]:
        return folder_path.count("/"), folder_path.lower()
