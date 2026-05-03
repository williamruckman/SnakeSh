from __future__ import annotations

import asyncio
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest

# Allow tests to run in lightweight environments where asyncssh isn't installed.
if "asyncssh" not in sys.modules:
    try:
        __import__("asyncssh")
    except ModuleNotFoundError:
        asyncssh_stub = types.ModuleType("asyncssh")
        asyncssh_stub.SFTPClient = type("SFTPClient", (), {})
        asyncssh_stub.SFTPAttrs = type("SFTPAttrs", (), {})
        asyncssh_stub.Error = type("Error", (Exception,), {})
        sys.modules["asyncssh"] = asyncssh_stub

from snakesh.core.models import Protocol, Session
from snakesh.protocols.sftp_client import SFTPClient


def _build_session() -> Session:
    return Session(
        id="sess-sftp-client",
        name="SFTP Client Test",
        host="192.0.2.30",
        protocol=Protocol.SFTP,
        port=22,
        username="tester",
    )


class SFTPClientTests(unittest.TestCase):
    def test_connect_kwargs_do_not_set_keepalive_by_default(self) -> None:
        session = _build_session()
        kwargs = SFTPClient._connect_kwargs(session, password="secret")
        self.assertNotIn("keepalive_interval", kwargs)
        self.assertNotIn("keepalive_count_max", kwargs)

    def test_connect_kwargs_set_keepalive_when_enabled(self) -> None:
        session = _build_session()
        session.ssh_keepalive = True
        kwargs = SFTPClient._connect_kwargs(session, password="secret")
        self.assertEqual(kwargs.get("keepalive_interval"), SFTPClient.KEEPALIVE_INTERVAL_SECONDS)
        self.assertEqual(kwargs.get("keepalive_count_max"), SFTPClient.KEEPALIVE_COUNT_MAX)

    def test_list_entries_preserves_remote_modified_time(self) -> None:
        client = SFTPClient()

        class _Attrs:
            def __init__(self, permissions: int, size: int, mtime: int | None) -> None:
                self.permissions = permissions
                self.size = size
                self.mtime = mtime

        class _Name:
            def __init__(self, filename: str, attrs: _Attrs) -> None:
                self.filename = filename
                self.attrs = attrs

        class _FakeSFTP:
            async def realpath(self, path: str) -> str:
                return "/remote"

            async def exists(self, _path: str) -> bool:
                return True

            async def isdir(self, _path: str) -> bool:
                return True

            async def scandir(self, _path: str):
                yield _Name("new.log", _Attrs(stat.S_IFREG, 512, 2_000))
                yield _Name("old.log", _Attrs(stat.S_IFREG, 256, 1_000))

        current_path, entries = asyncio.run(client._scan_directory_entries(_FakeSFTP(), "."))
        self.assertEqual(current_path, "/remote")
        self.assertEqual([entry.name for entry in entries], ["new.log", "old.log"])
        self.assertEqual(entries[0].modified_time, 2_000)
        self.assertEqual(entries[1].modified_time, 1_000)

    def test_scan_directory_marks_symlinked_directory_as_navigable(self) -> None:
        client = SFTPClient()

        class _Attrs:
            def __init__(self, permissions: int, size: int = 0, mtime: int | None = None) -> None:
                self.permissions = permissions
                self.size = size
                self.mtime = mtime

        class _Name:
            def __init__(self, filename: str, attrs: _Attrs) -> None:
                self.filename = filename
                self.attrs = attrs

        class _FakeSFTP:
            def __init__(self) -> None:
                self.isdir_calls: list[str] = []

            async def realpath(self, _path: str) -> str:
                return "/remote"

            async def exists(self, _path: str) -> bool:
                return True

            async def isdir(self, path: str) -> bool:
                self.isdir_calls.append(path)
                return path in {"/remote", "/remote/linkdir"}

            async def scandir(self, _path: str):
                yield _Name("linkdir", _Attrs(stat.S_IFLNK, 16, 2_000))

        sftp = _FakeSFTP()
        current_path, entries = asyncio.run(client._scan_directory_entries(sftp, "."))
        self.assertEqual(current_path, "/remote")
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].is_dir)
        self.assertTrue(entries[0].is_symlink)
        self.assertEqual(sftp.isdir_calls, ["/remote", "/remote/linkdir"])

    def test_scan_directory_avoids_followup_dir_checks_for_regular_entries(self) -> None:
        client = SFTPClient()

        class _Attrs:
            def __init__(self, permissions: int, size: int = 0, mtime: int | None = None) -> None:
                self.permissions = permissions
                self.size = size
                self.mtime = mtime

        class _Name:
            def __init__(self, filename: str, attrs: _Attrs) -> None:
                self.filename = filename
                self.attrs = attrs

        class _FakeSFTP:
            def __init__(self) -> None:
                self.isdir_calls: list[str] = []

            async def realpath(self, _path: str) -> str:
                return "/remote"

            async def exists(self, _path: str) -> bool:
                return True

            async def isdir(self, path: str) -> bool:
                self.isdir_calls.append(path)
                return path == "/remote"

            async def scandir(self, _path: str):
                yield _Name("folder", _Attrs(stat.S_IFDIR, 0, 2_000))
                yield _Name("file.txt", _Attrs(stat.S_IFREG, 32, 1_000))

        sftp = _FakeSFTP()
        current_path, entries = asyncio.run(client._scan_directory_entries(sftp, "."))
        self.assertEqual(current_path, "/remote")
        self.assertEqual([entry.name for entry in entries], ["folder", "file.txt"])
        self.assertEqual(sftp.isdir_calls, ["/remote"])

    def test_collect_upload_overwrite_conflicts_finds_nested_files_only(self) -> None:
        client = SFTPClient()

        class _FakeSFTP:
            def __init__(self) -> None:
                self._files = {
                    "/remote/folder/app.log",
                    "/remote/folder/sub/nested.txt",
                }
                self._dirs = {
                    "/remote",
                    "/remote/folder",
                    "/remote/folder/sub",
                }

            async def exists(self, path: str) -> bool:
                return path in self._files or path in self._dirs

            async def isdir(self, path: str) -> bool:
                return path in self._dirs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "folder"
            root.mkdir()
            (root / "app.log").write_text("first", encoding="utf-8")
            (root / "sub").mkdir()
            (root / "sub" / "nested.txt").write_text("second", encoding="utf-8")
            (root / "sub" / "new.txt").write_text("third", encoding="utf-8")

            conflicts = asyncio.run(
                client._collect_upload_overwrite_conflicts(_FakeSFTP(), [str(root)], "/remote")
            )

        self.assertEqual(
            [conflict.destination_path for conflict in conflicts],
            [
                "/remote/folder/app.log",
                "/remote/folder/sub/nested.txt",
            ],
        )

    def test_remove_remote_path_deletes_nested_directory_without_recursion(self) -> None:
        client = SFTPClient()

        class _Attrs:
            def __init__(self, permissions: int) -> None:
                self.permissions = permissions

        class _FakeSFTP:
            def __init__(self) -> None:
                self.removed: list[str] = []
                self.removed_dirs: list[str] = []

            async def islink(self, _path: str) -> bool:
                return False

            async def lstat(self, path: str) -> _Attrs:
                if path in {"/remote/dir", "/remote/dir/sub"}:
                    return _Attrs(stat.S_IFDIR)
                return _Attrs(stat.S_IFREG)

            async def realpath(self, path: str) -> str:
                return path

            async def listdir(self, path: str) -> list[str]:
                if path == "/remote/dir":
                    return ["a.txt", "sub"]
                if path == "/remote/dir/sub":
                    return ["b.txt"]
                return []

            async def remove(self, path: str) -> None:
                self.removed.append(path)

            async def rmdir(self, path: str) -> None:
                self.removed_dirs.append(path)

        sftp = _FakeSFTP()
        asyncio.run(client._remove_remote_path(sftp, "/remote/dir"))

        self.assertEqual(sftp.removed, ["/remote/dir/a.txt", "/remote/dir/sub/b.txt"])
        self.assertEqual(sftp.removed_dirs, ["/remote/dir/sub", "/remote/dir"])

    def test_remove_remote_path_deletes_symlink_without_descending(self) -> None:
        client = SFTPClient()

        class _FakeSFTP:
            def __init__(self) -> None:
                self.removed: list[str] = []
                self.listdir_calls = 0
                self.lstat_calls = 0
                self.rmdir_calls = 0

            async def islink(self, path: str) -> bool:
                return path == "/remote/linkdir"

            async def lstat(self, _path: str):
                self.lstat_calls += 1
                raise AssertionError("lstat should not be called for symlinks")

            async def realpath(self, _path: str) -> str:
                raise AssertionError("realpath should not be called for symlinks")

            async def listdir(self, _path: str) -> list[str]:
                self.listdir_calls += 1
                raise AssertionError("listdir should not be called for symlinks")

            async def remove(self, path: str) -> None:
                self.removed.append(path)

            async def rmdir(self, _path: str) -> None:
                self.rmdir_calls += 1
                raise AssertionError("rmdir should not be called for symlinks")

        sftp = _FakeSFTP()
        asyncio.run(client._remove_remote_path(sftp, "/remote/linkdir"))

        self.assertEqual(sftp.removed, ["/remote/linkdir"])
        self.assertEqual(sftp.listdir_calls, 0)
        self.assertEqual(sftp.lstat_calls, 0)
        self.assertEqual(sftp.rmdir_calls, 0)

    def test_remove_remote_path_rejects_cyclic_directory_alias(self) -> None:
        client = SFTPClient()

        class _Attrs:
            def __init__(self, permissions: int) -> None:
                self.permissions = permissions

        class _FakeSFTP:
            async def islink(self, _path: str) -> bool:
                return False

            async def lstat(self, _path: str) -> _Attrs:
                return _Attrs(stat.S_IFDIR)

            async def realpath(self, _path: str) -> str:
                return "/canonical/dir"

            async def listdir(self, path: str) -> list[str]:
                if path == "/remote/dir":
                    return ["loop"]
                return []

            async def remove(self, _path: str) -> None:
                raise AssertionError("remove should not be called for cyclic directory aliases")

            async def rmdir(self, _path: str) -> None:
                raise AssertionError("rmdir should not be called for cyclic directory aliases")

        with self.assertRaisesRegex(RuntimeError, "cyclic path reference"):
            asyncio.run(client._remove_remote_path(_FakeSFTP(), "/remote/dir"))

    def test_collect_download_overwrite_conflicts_handles_symlink_entries(self) -> None:
        client = SFTPClient()

        class _Attrs:
            def __init__(self, permissions: int) -> None:
                self.permissions = permissions

        class _FakeSFTP:
            def __init__(self) -> None:
                self.lstat_calls: list[str] = []

            async def realpath(self, path: str) -> str:
                return f"/canonical{path}"

            async def islink(self, path: str) -> bool:
                return path == "/remote/dir/link"

            async def lstat(self, path: str) -> _Attrs:
                self.lstat_calls.append(path)
                if path == "/remote/dir":
                    return _Attrs(stat.S_IFDIR)
                return _Attrs(stat.S_IFREG)

            async def listdir(self, path: str) -> list[str]:
                if path == "/remote/dir":
                    return ["file.txt", "link"]
                return []

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            (destination / "dir").mkdir()
            (destination / "dir" / "file.txt").write_text("existing", encoding="utf-8")
            (destination / "dir" / "link").write_text("existing-link", encoding="utf-8")
            sftp = _FakeSFTP()

            conflicts = asyncio.run(
                client._collect_download_overwrite_conflicts(
                    sftp,
                    ["/remote/dir"],
                    destination,
                )
            )

        self.assertCountEqual(
            [conflict.source_path for conflict in conflicts],
            ["/remote/dir/file.txt", "/remote/dir/link"],
        )
        self.assertNotIn("/remote/dir/link", sftp.lstat_calls)

    def test_collect_download_overwrite_conflicts_rejects_cyclic_directory_alias(self) -> None:
        client = SFTPClient()

        class _Attrs:
            def __init__(self, permissions: int) -> None:
                self.permissions = permissions

        class _FakeSFTP:
            async def realpath(self, _path: str) -> str:
                return "/canonical/dir"

            async def islink(self, _path: str) -> bool:
                return False

            async def lstat(self, _path: str) -> _Attrs:
                return _Attrs(stat.S_IFDIR)

            async def listdir(self, path: str) -> list[str]:
                if path == "/remote/dir":
                    return ["loop"]
                return []

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "cyclic path reference"):
                asyncio.run(
                    client._collect_download_overwrite_conflicts(
                        _FakeSFTP(),
                        ["/remote/dir"],
                        Path(tmp),
                    )
                )

    def test_rename_remote_path_uses_plain_rename_when_target_is_new(self) -> None:
        client = SFTPClient()

        class _FakeSFTP:
            def __init__(self) -> None:
                self.renamed: list[tuple[str, str]] = []
                self.posix_renamed: list[tuple[str, str]] = []

            async def exists(self, _path: str) -> bool:
                return False

            async def isdir(self, _path: str) -> bool:
                return False

            async def rename(self, old_path: str, new_path: str) -> None:
                self.renamed.append((old_path, new_path))

            async def posix_rename(self, old_path: str, new_path: str) -> None:
                self.posix_renamed.append((old_path, new_path))

        sftp = _FakeSFTP()
        renamed_path = asyncio.run(
            client._rename_remote_path(
                sftp,
                "/remote/old.txt",
                "/remote/new.txt",
                replace=False,
            )
        )
        self.assertEqual(renamed_path, "/remote/new.txt")
        self.assertEqual(sftp.renamed, [("/remote/old.txt", "/remote/new.txt")])
        self.assertEqual(sftp.posix_renamed, [])

    def test_create_remote_directory_resolves_parent_and_calls_mkdir(self) -> None:
        client = SFTPClient()

        class _FakeSFTP:
            def __init__(self) -> None:
                self.mkdir_calls: list[str] = []

            async def realpath(self, path: str) -> str:
                return "/remote/current" if path == "." else path

            async def exists(self, path: str) -> bool:
                return path == "/remote/current"

            async def isdir(self, path: str) -> bool:
                return path == "/remote/current"

            async def mkdir(self, path: str) -> None:
                self.mkdir_calls.append(path)

        sftp = _FakeSFTP()
        created_path = asyncio.run(client._create_remote_directory(sftp, ".", "logs"))

        self.assertEqual(created_path, "/remote/current/logs")
        self.assertEqual(sftp.mkdir_calls, ["/remote/current/logs"])

    def test_create_remote_directory_propagates_mkdir_failures(self) -> None:
        client = SFTPClient()

        class _FakeSFTP:
            async def realpath(self, _path: str) -> str:
                return "/remote/current"

            async def exists(self, path: str) -> bool:
                return path == "/remote/current"

            async def isdir(self, path: str) -> bool:
                return path == "/remote/current"

            async def mkdir(self, _path: str) -> None:
                raise FileExistsError("already exists")

        with self.assertRaises(FileExistsError):
            asyncio.run(client._create_remote_directory(_FakeSFTP(), ".", "logs"))

    def test_rename_remote_path_uses_posix_replace_for_existing_target(self) -> None:
        client = SFTPClient()

        class _FakeSFTP:
            def __init__(self) -> None:
                self.listdir_calls: list[str] = []
                self.posix_renamed: list[tuple[str, str]] = []

            async def exists(self, _path: str) -> bool:
                return True

            async def isdir(self, _path: str) -> bool:
                return False

            async def listdir(self, path: str) -> list[str]:
                self.listdir_calls.append(path)
                return []

            async def rename(self, _old_path: str, _new_path: str) -> None:
                raise AssertionError("rename should not be used when replacing an existing target")

            async def posix_rename(self, old_path: str, new_path: str) -> None:
                self.posix_renamed.append((old_path, new_path))

        sftp = _FakeSFTP()
        renamed_path = asyncio.run(
            client._rename_remote_path(
                sftp,
                "/remote/old.txt",
                "/remote/new.txt",
                replace=True,
            )
        )
        self.assertEqual(renamed_path, "/remote/new.txt")
        self.assertEqual(sftp.listdir_calls, [])
        self.assertEqual(sftp.posix_renamed, [("/remote/old.txt", "/remote/new.txt")])

    def test_rename_remote_path_refuses_non_empty_directory_replace(self) -> None:
        client = SFTPClient()

        class _FakeSFTP:
            async def exists(self, _path: str) -> bool:
                return True

            async def isdir(self, _path: str) -> bool:
                return True

            async def listdir(self, _path: str) -> list[str]:
                return ["child.txt"]

            async def rename(self, _old_path: str, _new_path: str) -> None:
                raise AssertionError("rename should not be called when replace is refused")

            async def posix_rename(self, _old_path: str, _new_path: str) -> None:
                raise AssertionError("posix_rename should not be called when replace is refused")

        with self.assertRaisesRegex(RuntimeError, "non-empty remote directory"):
            asyncio.run(
                client._rename_remote_path(
                    _FakeSFTP(),
                    "/remote/old-dir",
                    "/remote/new-dir",
                    replace=True,
                )
            )


if __name__ == "__main__":
    unittest.main()
