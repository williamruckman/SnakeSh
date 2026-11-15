from __future__ import annotations

import asyncio
import posixpath
import stat
from unittest.mock import patch

import asyncssh
import pytest

from snakesh.core.models import Protocol, Session
from snakesh.protocols.sftp_client import SFTPClient, TransferCancelledError


class RemoteFS:
    def __init__(self):
        self.nodes = {
            "/remote": stat.S_IFDIR, "/remote/dest": stat.S_IFDIR,
            "/remote/a.txt": stat.S_IFREG, "/remote/b.txt": stat.S_IFREG,
            "/remote/folder": stat.S_IFDIR, "/remote/folder/sub": stat.S_IFDIR,
            "/remote/link": stat.S_IFLNK,
        }
        self.aliases = {}
        self.moves = []

    async def realpath(self, path):
        return self.aliases.get(path, posixpath.normpath(path))

    async def exists(self, path):
        return path in self.nodes

    async def lexists(self, path):
        return path in self.nodes

    async def isdir(self, path):
        return self.nodes.get(path) == stat.S_IFDIR

    async def lstat(self, path):
        if path not in self.nodes:
            raise FileNotFoundError(path)
        return asyncssh.SFTPAttrs(permissions=self.nodes[path] | 0o755)

    async def rename(self, source, target):
        assert target not in self.nodes
        self.moves.append((source, target))
        self.nodes[target] = self.nodes.pop(source)


def test_remote_move_renames_files_directories_and_links_on_server():
    fs = RemoteFS()
    count = asyncio.run(SFTPClient()._move_remote_paths(
        fs, ["/remote/a.txt", "/remote/folder", "/remote/link"], "/remote/dest"
    ))
    assert count == 3
    assert fs.moves == [("/remote/" + name, "/remote/dest/" + name)
                        for name in ("a.txt", "folder", "link")]
    assert fs.nodes["/remote/dest/link"] == stat.S_IFLNK


@pytest.mark.parametrize("conflict_type", [stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK])
def test_later_collision_stops_whole_selection_before_first_move(conflict_type):
    fs = RemoteFS()
    fs.nodes["/remote/dest/b.txt"] = conflict_type
    with pytest.raises(FileExistsError):
        asyncio.run(SFTPClient()._move_remote_paths(
            fs, ["/remote/a.txt", "/remote/b.txt"], "/remote/dest"
        ))
    assert fs.moves == []
    assert "/remote/a.txt" in fs.nodes


@pytest.mark.parametrize("destination", ["/remote/folder", "/remote/folder/sub", "/remote/alias"])
def test_directory_cannot_move_into_itself_even_through_target_alias(destination):
    fs = RemoteFS()
    fs.aliases["/remote/alias"] = "/remote/folder/sub"
    with pytest.raises(ValueError, match="into itself"):
        asyncio.run(SFTPClient()._move_remote_paths(fs, ["/remote/folder"], destination))
    assert fs.moves == []


def test_same_directory_drop_is_rejected_and_duplicate_sources_move_once():
    fs = RemoteFS()
    with pytest.raises(ValueError, match="already in"):
        asyncio.run(SFTPClient()._move_remote_paths(fs, ["/remote/a.txt"], "/remote"))
    assert not fs.moves
    assert asyncio.run(SFTPClient()._move_remote_paths(
        fs, ["/remote/a.txt", "/remote/a.txt"], "/remote/dest"
    )) == 1


def test_overlapping_selection_and_duplicate_destination_names_are_rejected():
    fs = RemoteFS()
    fs.nodes["/remote/folder/a.txt"] = stat.S_IFREG
    with pytest.raises(ValueError, match="separately"):
        asyncio.run(SFTPClient()._move_remote_paths(
            fs, ["/remote/folder", "/remote/folder/sub"], "/remote/dest"
        ))
    assert not fs.moves
    with pytest.raises(FileExistsError):
        asyncio.run(SFTPClient()._move_remote_paths(
            fs, ["/remote/a.txt", "/remote/folder/a.txt"], "/remote/dest"
        ))
    assert not fs.moves


def test_cancelled_move_preserves_completed_changes_and_remaining_sources():
    fs = RemoteFS()
    with pytest.raises(TransferCancelledError):
        asyncio.run(SFTPClient()._move_remote_paths(
            fs, ["/remote/a.txt", "/remote/b.txt"], "/remote/dest",
            cancel_requested=lambda: bool(fs.moves),
        ))
    assert fs.moves == [("/remote/a.txt", "/remote/dest/a.txt")]
    assert "/remote/b.txt" in fs.nodes


def test_public_move_over_loopback_sftp_preserves_file_contents_and_directory_tree(tmp_path):
    async def exercise():
        class Server(asyncssh.SSHServer):
            def begin_auth(self, _username):
                return False

        (tmp_path / "dest").mkdir()
        (tmp_path / "folder").mkdir()
        (tmp_path / "folder" / "nested.bin").write_bytes(bytes(range(256)))
        (tmp_path / "a.txt").write_bytes(b"original content\x00\xff")
        listener = await asyncssh.create_server(
            Server, "127.0.0.1", 0, server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
            sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(tmp_path)),
        )
        session = Session(id="move-test", name="Move Test", host="127.0.0.1",
                          protocol=Protocol.SFTP, port=listener.get_port(), username="tester")
        client = SFTPClient()
        try:
            with patch.object(client, "_connect_kwargs", return_value={
                "host": "127.0.0.1", "port": listener.get_port(), "username": "tester", "known_hosts": None,
            }):
                count = await client.move_paths(session, ["/a.txt", "/folder"], "/dest")
            assert count == 2
            assert not (tmp_path / "a.txt").exists()
            assert not (tmp_path / "folder").exists()
            assert (tmp_path / "dest/a.txt").read_bytes() == b"original content\x00\xff"
            assert (tmp_path / "dest/folder/nested.bin").read_bytes() == bytes(range(256))
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())
