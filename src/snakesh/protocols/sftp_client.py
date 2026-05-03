from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import posixpath
import stat

import asyncssh

from snakesh.core.hostkeys import known_hosts_path, trust_host_key
from snakesh.core.models import Session

_ASYNCSSH_SFTP_MODULE = getattr(asyncssh, "sftp", None)
_FILEXFER_TYPE_DIRECTORY = getattr(_ASYNCSSH_SFTP_MODULE, "FILEXFER_TYPE_DIRECTORY", 2)
_FILEXFER_TYPE_SYMLINK = getattr(_ASYNCSSH_SFTP_MODULE, "FILEXFER_TYPE_SYMLINK", 3)


@dataclass(slots=True)
class SFTPEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified_time: int | None = None
    is_symlink: bool = False


@dataclass(slots=True)
class TransferProgress:
    source_path: str
    destination_path: str
    item_index: int
    item_count: int
    item_bytes_transferred: int
    item_bytes_total: int
    overall_bytes_transferred: int
    overall_bytes_total: int


@dataclass(slots=True)
class OverwriteConflict:
    source_path: str
    destination_path: str


class TransferCancelledError(RuntimeError):
    """Raised when a user cancels an in-progress file transfer."""


class SFTPClient:
    KEEPALIVE_INTERVAL_SECONDS = 30
    KEEPALIVE_COUNT_MAX = 3

    @staticmethod
    def _connect_kwargs(session: Session, password: str | None = None) -> dict[str, object]:
        connect_kwargs: dict[str, object] = {
            "host": session.host,
            "port": session.port,
            "username": session.username or None,
            "known_hosts": str(known_hosts_path()),
        }
        if session.use_key_auth:
            if session.private_key_path:
                connect_kwargs["client_keys"] = [session.private_key_path]
        else:
            connect_kwargs["client_keys"] = []
            connect_kwargs["preferred_auth"] = "password,keyboard-interactive"
        if password:
            connect_kwargs["password"] = password
            connect_kwargs["preferred_auth"] = "password,keyboard-interactive,publickey"
        if session.ssh_keepalive:
            connect_kwargs["keepalive_interval"] = SFTPClient.KEEPALIVE_INTERVAL_SECONDS
            connect_kwargs["keepalive_count_max"] = SFTPClient.KEEPALIVE_COUNT_MAX
        return connect_kwargs

    @staticmethod
    def _home_path(session: Session) -> str:
        # Let the server resolve the user's home instead of assuming /home/<username>.
        return "."

    async def _resolve_directory(self, sftp: asyncssh.SFTPClient, path: str) -> str:
        current_path = await sftp.realpath(path)
        if not await sftp.exists(current_path):
            raise FileNotFoundError(f"Remote path not found: {path}")
        if not await sftp.isdir(current_path):
            raise NotADirectoryError(f"Remote path is not a directory: {current_path}")
        return current_path

    async def _scan_directory_entries(
        self,
        sftp: asyncssh.SFTPClient,
        path: str,
        *,
        batch_size: int = 250,
        batch_callback: Callable[[str, list[SFTPEntry]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[str, list[SFTPEntry]]:
        current_path = await self._resolve_directory(sftp, path)
        entries: list[SFTPEntry] = []
        batch: list[SFTPEntry] = []
        async for remote_name in sftp.scandir(current_path):
            if cancel_requested and cancel_requested():
                raise TransferCancelledError("Directory load cancelled by user.")
            entry = await self._sftp_name_to_entry(sftp, current_path, remote_name)
            if entry is None:
                continue
            entries.append(entry)
            if batch_callback is None:
                continue
            batch.append(entry)
            if len(batch) >= max(1, int(batch_size)):
                batch_callback(current_path, list(batch))
                batch.clear()
        if batch_callback and batch:
            batch_callback(current_path, list(batch))
        return current_path, self._sort_entries(entries)

    async def _sftp_name_to_entry(
        self,
        sftp: asyncssh.SFTPClient,
        current_path: str,
        remote_name,
    ) -> SFTPEntry | None:
        name = self._normalize_remote_name(getattr(remote_name, "filename", ""))
        if not name or name in {".", ".."}:
            return None
        full_path = posixpath.join(current_path, name)
        attrs = getattr(remote_name, "attrs", None)
        is_symlink = bool(attrs and self._attrs_is_symlink(attrs))
        is_dir = bool(attrs and self._attrs_is_dir(attrs))
        if is_symlink:
            try:
                is_dir = await sftp.isdir(full_path)
            except asyncssh.Error:
                is_dir = False
        return SFTPEntry(
            name=name,
            path=full_path,
            is_dir=is_dir,
            size=self._attrs_size(attrs, is_dir=is_dir),
            modified_time=self._attrs_modified_time(attrs),
            is_symlink=is_symlink,
        )

    async def resolve_directory(
        self,
        session: Session,
        path: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
    ) -> str:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None
        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                return await self._resolve_directory(sftp, path)

    async def list_directory(
        self,
        session: Session,
        path: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
    ) -> tuple[str, list[SFTPEntry]]:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None
        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                return await self._scan_directory_entries(sftp, path)

    async def scan_directory(
        self,
        session: Session,
        path: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
        batch_size: int = 250,
        batch_callback: Callable[[str, list[SFTPEntry]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[str, list[SFTPEntry]]:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None
        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                return await self._scan_directory_entries(
                    sftp,
                    path,
                    batch_size=batch_size,
                    batch_callback=batch_callback,
                    cancel_requested=cancel_requested,
                )

    async def find_upload_overwrite_conflicts(
        self,
        session: Session,
        local_paths: list[str],
        remote_dir: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
    ) -> list[OverwriteConflict]:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None

        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                target_dir = await sftp.realpath(remote_dir)
                conflicts = await self._collect_upload_overwrite_conflicts(sftp, local_paths, target_dir)
                return self._sorted_overwrite_conflicts(conflicts)

    async def find_download_overwrite_conflicts(
        self,
        session: Session,
        remote_paths: list[str],
        local_dir: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
    ) -> list[OverwriteConflict]:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None

        destination = Path(local_dir).expanduser().resolve()

        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                conflicts = await self._collect_download_overwrite_conflicts(sftp, remote_paths, destination)
                return self._sorted_overwrite_conflicts(conflicts)

    async def upload_paths(
        self,
        session: Session,
        local_paths: list[str],
        remote_dir: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> int:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None

        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                target_dir = await sftp.realpath(remote_dir)
                total_bytes = sum(self._local_size_bytes(raw_path) for raw_path in local_paths)
                overall_bytes_transferred = 0
                transferred = 0
                for item_index, raw_path in enumerate(local_paths, start=1):
                    if cancel_requested and cancel_requested():
                        raise TransferCancelledError("Upload cancelled by user.")
                    local_path = Path(raw_path).expanduser()
                    if not local_path.exists():
                        raise FileNotFoundError(f"Local path not found: {local_path}")
                    remote_target = posixpath.join(target_dir, local_path.name)
                    item_total = self._local_size_bytes(str(local_path))
                    item_progress = 0
                    reported_completion = False

                    def _on_progress(*args) -> None:
                        nonlocal item_progress, overall_bytes_transferred, reported_completion
                        if cancel_requested and cancel_requested():
                            raise TransferCancelledError("Upload cancelled by user.")
                        transferred_bytes, total_for_item = self._extract_progress_bytes(args, item_total)
                        delta = max(0, transferred_bytes - item_progress)
                        item_progress = transferred_bytes
                        overall_bytes_transferred = min(total_bytes, overall_bytes_transferred + delta)
                        if transferred_bytes >= max(1, total_for_item):
                            reported_completion = True
                        if progress_callback:
                            progress_callback(
                                TransferProgress(
                                    source_path=str(local_path),
                                    destination_path=remote_target,
                                    item_index=item_index,
                                    item_count=len(local_paths),
                                    item_bytes_transferred=min(item_progress, max(item_total, total_for_item)),
                                    item_bytes_total=max(item_total, total_for_item),
                                    overall_bytes_transferred=overall_bytes_transferred,
                                    overall_bytes_total=total_bytes,
                                )
                            )

                    await sftp.put(
                        str(local_path),
                        remote_target,
                        recurse=local_path.is_dir(),
                        preserve=True,
                        progress_handler=_on_progress,
                    )

                    if not reported_completion:
                        remaining = max(0, item_total - item_progress)
                        if remaining:
                            overall_bytes_transferred = min(total_bytes, overall_bytes_transferred + remaining)
                        if progress_callback:
                            progress_callback(
                                TransferProgress(
                                    source_path=str(local_path),
                                    destination_path=remote_target,
                                    item_index=item_index,
                                    item_count=len(local_paths),
                                    item_bytes_transferred=item_total,
                                    item_bytes_total=item_total,
                                    overall_bytes_transferred=overall_bytes_transferred,
                                    overall_bytes_total=total_bytes,
                                )
                            )
                    transferred += 1
                return transferred

    async def download_paths(
        self,
        session: Session,
        remote_paths: list[str],
        local_dir: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> int:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None

        destination = Path(local_dir).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)

        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                remote_items: list[tuple[str, asyncssh.SFTPAttrs, int]] = []
                for remote_path in remote_paths:
                    attrs = await sftp.lstat(remote_path)
                    item_total = await self._remote_size_bytes(sftp, remote_path, attrs)
                    remote_items.append((remote_path, attrs, item_total))

                total_bytes = sum(item_total for _remote_path, _attrs, item_total in remote_items)

                overall_bytes_transferred = 0
                transferred = 0
                for item_index, (remote_path, attrs, item_total) in enumerate(remote_items, start=1):
                    if cancel_requested and cancel_requested():
                        raise TransferCancelledError("Download cancelled by user.")
                    name = posixpath.basename(remote_path.rstrip("/")) or "downloaded"
                    local_target = destination / name
                    item_progress = 0
                    reported_completion = False

                    def _on_progress(*args) -> None:
                        nonlocal item_progress, overall_bytes_transferred, reported_completion
                        if cancel_requested and cancel_requested():
                            raise TransferCancelledError("Download cancelled by user.")
                        transferred_bytes, total_for_item = self._extract_progress_bytes(args, item_total)
                        delta = max(0, transferred_bytes - item_progress)
                        item_progress = transferred_bytes
                        overall_bytes_transferred = min(total_bytes, overall_bytes_transferred + delta)
                        if transferred_bytes >= max(1, total_for_item):
                            reported_completion = True
                        if progress_callback:
                            progress_callback(
                                TransferProgress(
                                    source_path=remote_path,
                                    destination_path=str(local_target),
                                    item_index=item_index,
                                    item_count=len(remote_paths),
                                    item_bytes_transferred=min(item_progress, max(item_total, total_for_item)),
                                    item_bytes_total=max(item_total, total_for_item),
                                    overall_bytes_transferred=overall_bytes_transferred,
                                    overall_bytes_total=total_bytes,
                                )
                            )

                    await sftp.get(
                        remote_path,
                        str(local_target),
                        recurse=self._attrs_is_dir(attrs),
                        preserve=True,
                        progress_handler=_on_progress,
                    )

                    if not reported_completion:
                        remaining = max(0, item_total - item_progress)
                        if remaining:
                            overall_bytes_transferred = min(total_bytes, overall_bytes_transferred + remaining)
                        if progress_callback:
                            progress_callback(
                                TransferProgress(
                                    source_path=remote_path,
                                    destination_path=str(local_target),
                                    item_index=item_index,
                                    item_count=len(remote_paths),
                                    item_bytes_transferred=item_total,
                                    item_bytes_total=item_total,
                                    overall_bytes_transferred=overall_bytes_transferred,
                                    overall_bytes_total=total_bytes,
                                )
                            )
                    transferred += 1
                return transferred

    async def delete_paths(
        self,
        session: Session,
        remote_paths: list[str],
        *,
        password: str | None = None,
        trust_unknown: bool = False,
    ) -> int:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None

        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                deleted = 0
                for remote_path in remote_paths:
                    await self._remove_remote_path(sftp, remote_path)
                    deleted += 1
                return deleted

    async def create_directory(
        self,
        session: Session,
        remote_dir: str,
        name: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
    ) -> str:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None

        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                return await self._create_remote_directory(sftp, remote_dir, name)

    async def rename_path(
        self,
        session: Session,
        remote_path: str,
        new_path: str,
        *,
        replace: bool = False,
        password: str | None = None,
        trust_unknown: bool = False,
    ) -> str:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None

        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                return await self._rename_remote_path(
                    sftp,
                    remote_path,
                    new_path,
                    replace=replace,
                )

    async def remote_path_exists(
        self,
        session: Session,
        remote_path: str,
        *,
        password: str | None = None,
        trust_unknown: bool = False,
    ) -> bool:
        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None

        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            async with conn.start_sftp_client() as sftp:
                return await sftp.exists(remote_path)

    async def _collect_upload_overwrite_conflicts(
        self,
        sftp: asyncssh.SFTPClient,
        local_paths: list[str],
        remote_dir: str,
    ) -> list[OverwriteConflict]:
        conflicts: list[OverwriteConflict] = []
        for raw_path in local_paths:
            local_path = Path(raw_path).expanduser()
            if not local_path.exists():
                raise FileNotFoundError(f"Local path not found: {local_path}")
            conflicts.extend(await self._collect_upload_conflicts_for_path(sftp, local_path, remote_dir))
        return conflicts

    async def _collect_upload_conflicts_for_path(
        self,
        sftp: asyncssh.SFTPClient,
        local_path: Path,
        remote_dir: str,
    ) -> list[OverwriteConflict]:
        remote_target = posixpath.join(remote_dir, local_path.name)
        if local_path.is_dir():
            return await self._collect_upload_dir_conflicts(sftp, local_path, remote_target)

        final_target = remote_target
        if await sftp.isdir(remote_target):
            final_target = posixpath.join(remote_target, local_path.name)

        if await self._is_remote_non_directory(sftp, final_target):
            return [
                OverwriteConflict(
                    source_path=str(local_path),
                    destination_path=final_target,
                )
            ]
        return []

    async def _collect_upload_dir_conflicts(
        self,
        sftp: asyncssh.SFTPClient,
        local_dir: Path,
        remote_dir: str,
    ) -> list[OverwriteConflict]:
        if await self._is_remote_non_directory(sftp, remote_dir):
            return []

        conflicts: list[OverwriteConflict] = []
        stack: list[tuple[Path, str]] = [(local_dir, remote_dir)]

        while stack:
            current_local_dir, current_remote_dir = stack.pop()
            with os.scandir(current_local_dir) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name.lower())

            for entry in reversed(entries):
                source_path = Path(entry.path)
                remote_target = posixpath.join(current_remote_dir, entry.name)
                if entry.is_dir(follow_symlinks=False):
                    if not await self._is_remote_non_directory(sftp, remote_target):
                        stack.append((source_path, remote_target))
                    continue
                if await self._is_remote_non_directory(sftp, remote_target):
                    conflicts.append(
                        OverwriteConflict(
                            source_path=str(source_path),
                            destination_path=remote_target,
                        )
                    )
        return conflicts

    async def _collect_download_overwrite_conflicts(
        self,
        sftp: asyncssh.SFTPClient,
        remote_paths: list[str],
        local_dir: Path,
    ) -> list[OverwriteConflict]:
        conflicts: list[OverwriteConflict] = []
        for remote_path in remote_paths:
            attrs = await sftp.lstat(remote_path)
            conflicts.extend(await self._collect_download_conflicts_for_path(sftp, remote_path, attrs, local_dir))
        return conflicts

    async def _collect_download_conflicts_for_path(
        self,
        sftp: asyncssh.SFTPClient,
        remote_path: str,
        attrs: asyncssh.SFTPAttrs,
        local_dir: Path,
    ) -> list[OverwriteConflict]:
        name = posixpath.basename(remote_path.rstrip("/")) or "downloaded"
        local_target = local_dir / name
        if self._attrs_is_dir(attrs):
            return await self._collect_download_dir_conflicts(sftp, remote_path, local_target)

        final_target = local_target
        if local_target.exists() and local_target.is_dir():
            final_target = local_target / name

        if final_target.exists() and not final_target.is_dir():
            return [
                OverwriteConflict(
                    source_path=remote_path,
                    destination_path=str(final_target),
                )
            ]
        return []

    async def _collect_download_dir_conflicts(
        self,
        sftp: asyncssh.SFTPClient,
        remote_dir: str,
        local_dir: Path,
    ) -> list[OverwriteConflict]:
        if local_dir.exists() and not local_dir.is_dir():
            return []

        conflicts: list[OverwriteConflict] = []
        stack: list[tuple[str, Path]] = [(remote_dir, local_dir)]
        visited_directories: set[str] = set()

        while stack:
            current_remote_dir, current_local_dir = stack.pop()
            resolved_path = await sftp.realpath(current_remote_dir)
            if resolved_path in visited_directories:
                raise RuntimeError(
                    "Refusing to scan remote directory for overwrite conflicts due to cyclic path reference: "
                    f"{current_remote_dir}"
                )
            visited_directories.add(resolved_path)

            names = await sftp.listdir(current_remote_dir)
            for name in reversed(sorted(names, key=lambda value: str(value).lower())):
                child_name = str(name)
                if child_name in (".", ".."):
                    continue
                remote_target = posixpath.join(current_remote_dir, child_name)
                local_target = current_local_dir / child_name
                if await sftp.islink(remote_target):
                    if local_target.exists() and not local_target.is_dir():
                        conflicts.append(
                            OverwriteConflict(
                                source_path=remote_target,
                                destination_path=str(local_target),
                            )
                        )
                    continue

                attrs = await sftp.lstat(remote_target)
                if self._attrs_is_dir(attrs):
                    if not (local_target.exists() and not local_target.is_dir()):
                        stack.append((remote_target, local_target))
                    continue
                if local_target.exists() and not local_target.is_dir():
                    conflicts.append(
                        OverwriteConflict(
                            source_path=remote_target,
                            destination_path=str(local_target),
                        )
                    )
        return conflicts

    async def list_home(self, session: Session, password: str | None = None) -> list[str]:
        _, entries = await self.list_directory(session, self._home_path(session), password=password)
        return [entry.name for entry in entries]

    async def trust_and_list_home(self, session: Session, password: str | None = None) -> list[str]:
        _, entries = await self.list_directory(
            session,
            self._home_path(session),
            password=password,
            trust_unknown=True,
        )
        return [entry.name for entry in entries]

    @staticmethod
    def _local_size_bytes(path: str) -> int:
        root = Path(path).expanduser()
        if not root.exists():
            return 0
        if root.is_file():
            try:
                return int(root.stat().st_size)
            except OSError:
                return 0
        if root.is_dir():
            total = 0
            for dirpath, _dirs, files in os.walk(root):
                for filename in files:
                    file_path = Path(dirpath) / filename
                    try:
                        total += int(file_path.stat().st_size)
                    except OSError:
                        continue
            return total
        return 0

    async def _remote_size_bytes(
        self,
        sftp: asyncssh.SFTPClient,
        remote_path: str,
        attrs: asyncssh.SFTPAttrs | None = None,
    ) -> int:
        remote_attrs = attrs or await sftp.lstat(remote_path)
        if not self._attrs_is_dir(remote_attrs):
            try:
                return int(remote_attrs.size or 0)
            except (TypeError, ValueError, OverflowError):
                return 0

        total = 0
        stack: list[str] = [remote_path]
        visited_directories: set[str] = set()

        while stack:
            current_dir = stack.pop()
            resolved_path = await sftp.realpath(current_dir)
            if resolved_path in visited_directories:
                raise RuntimeError(
                    "Refusing to scan remote directory size due to cyclic path reference: "
                    f"{current_dir}"
                )
            visited_directories.add(resolved_path)

            names = await sftp.listdir(current_dir)
            for name in reversed(sorted(names, key=lambda value: str(value).lower())):
                child_name = str(name)
                if child_name in (".", ".."):
                    continue
                child_path = posixpath.join(current_dir, child_name)
                if await sftp.islink(child_path):
                    link_attrs = await sftp.lstat(child_path)
                    try:
                        total += int(link_attrs.size or 0)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    continue
                child_attrs = await sftp.lstat(child_path)
                if self._attrs_is_dir(child_attrs):
                    stack.append(child_path)
                    continue
                try:
                    total += int(child_attrs.size or 0)
                except (TypeError, ValueError, OverflowError):
                    continue

        return total

    @staticmethod
    def _extract_progress_bytes(args: tuple[object, ...], fallback_total: int) -> tuple[int, int]:
        int_args = [int(value) for value in args if isinstance(value, int)]
        if len(int_args) >= 2:
            transferred = int_args[-2]
            total = int_args[-1]
            if total <= 0:
                total = fallback_total
            return max(0, transferred), max(0, total)
        if len(int_args) == 1:
            transferred = int_args[0]
            total = fallback_total
            return max(0, transferred), max(0, total)
        return 0, max(0, fallback_total)

    @staticmethod
    def _attrs_is_dir(attrs: asyncssh.SFTPAttrs) -> bool:
        file_type = getattr(attrs, "type", None)
        if file_type == _FILEXFER_TYPE_DIRECTORY:
            return True
        permissions = getattr(attrs, "permissions", None)
        return permissions is not None and stat.S_ISDIR(permissions)

    @staticmethod
    def _attrs_is_symlink(attrs: asyncssh.SFTPAttrs) -> bool:
        file_type = getattr(attrs, "type", None)
        if file_type == _FILEXFER_TYPE_SYMLINK:
            return True
        permissions = getattr(attrs, "permissions", None)
        return permissions is not None and stat.S_ISLNK(permissions)

    @staticmethod
    def _attrs_size(attrs: asyncssh.SFTPAttrs | None, *, is_dir: bool) -> int:
        if attrs is None or is_dir:
            return 0
        try:
            return int(getattr(attrs, "size", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return 0

    @staticmethod
    def _attrs_modified_time(attrs: asyncssh.SFTPAttrs | None) -> int | None:
        if attrs is None:
            return None
        value = getattr(attrs, "mtime", None)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _normalize_remote_name(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", "backslashreplace")
        return str(value or "")

    @staticmethod
    def _sort_entries(entries: list[SFTPEntry]) -> list[SFTPEntry]:
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.name.lower()))

    @staticmethod
    async def _is_remote_non_directory(sftp: asyncssh.SFTPClient, path: str) -> bool:
        return await sftp.exists(path) and not await sftp.isdir(path)

    @staticmethod
    def _sorted_overwrite_conflicts(conflicts: list[OverwriteConflict]) -> list[OverwriteConflict]:
        deduped: dict[tuple[str, str], OverwriteConflict] = {}
        for conflict in conflicts:
            deduped[(conflict.source_path, conflict.destination_path)] = conflict
        return sorted(
            deduped.values(),
            key=lambda conflict: (conflict.destination_path.lower(), conflict.source_path.lower()),
        )

    async def _rename_remote_path(
        self,
        sftp: asyncssh.SFTPClient,
        remote_path: str,
        new_path: str,
        *,
        replace: bool,
    ) -> str:
        if remote_path == new_path:
            return new_path
        if await sftp.exists(new_path):
            if not replace:
                raise FileExistsError(f"Remote path already exists: {new_path}")
            if await sftp.isdir(new_path):
                names = await sftp.listdir(new_path)
                if any(str(name) not in {".", ".."} for name in names):
                    raise RuntimeError(f"Cannot replace non-empty remote directory: {new_path}")
            await sftp.posix_rename(remote_path, new_path)
            return new_path
        await sftp.rename(remote_path, new_path)
        return new_path

    async def _create_remote_directory(
        self,
        sftp: asyncssh.SFTPClient,
        remote_dir: str,
        name: str,
    ) -> str:
        parent_dir = await self._resolve_directory(sftp, remote_dir)
        target_path = posixpath.join(parent_dir, name)
        await sftp.mkdir(target_path)
        return target_path

    async def _remove_remote_path(self, sftp: asyncssh.SFTPClient, remote_path: str) -> None:
        stack: list[tuple[str, bool]] = [(remote_path, False)]
        visited_directories: set[str] = set()

        while stack:
            current_path, children_queued = stack.pop()
            if not children_queued:
                if await sftp.islink(current_path):
                    await sftp.remove(current_path)
                    continue

                attrs = await sftp.lstat(current_path)
                if self._attrs_is_dir(attrs):
                    resolved_path = await sftp.realpath(current_path)
                    if resolved_path in visited_directories:
                        raise RuntimeError(
                            "Refusing to recursively delete remote directory with cyclic path reference: "
                            f"{current_path}"
                        )
                    visited_directories.add(resolved_path)
                    stack.append((current_path, True))
                    names = await sftp.listdir(current_path)
                    for name in reversed(names):
                        child_name = str(name)
                        if child_name in (".", ".."):
                            continue
                        stack.append((posixpath.join(current_path, child_name), False))
                    continue

                await sftp.remove(current_path)
                continue

            await sftp.rmdir(current_path)
