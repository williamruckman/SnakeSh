from __future__ import annotations

import asyncssh
from pathlib import Path

from snakesh.core.hostkeys import known_hosts_path, trust_host_key
from snakesh.core.models import Session
from snakesh.protocols.base import ProtocolClient
from snakesh.services.ssh_key_service import is_openssh_public_key_line


class SSHClient(ProtocolClient):
    KEEPALIVE_INTERVAL_SECONDS = 30
    KEEPALIVE_COUNT_MAX = 3
    LEGACY_KEX_ALGS = (
        "curve25519-sha256",
        "curve25519-sha256@libssh.org",
        "ecdh-sha2-nistp256",
        "diffie-hellman-group14-sha256",
        "diffie-hellman-group14-sha1",
        "diffie-hellman-group1-sha1",
    )
    LEGACY_HOST_KEY_ALGS = (
        "rsa-sha2-512",
        "rsa-sha2-256",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ssh-ed25519",
    )
    LEGACY_ENCRYPTION_ALGS = (
        "chacha20-poly1305@openssh.com",
        "aes256-ctr",
        "aes192-ctr",
        "aes128-ctr",
        "aes256-cbc",
        "aes128-cbc",
        "3des-cbc",
    )
    LEGACY_MAC_ALGS = (
        "hmac-sha2-512",
        "hmac-sha2-256",
        "hmac-sha1",
        "hmac-sha1-96",
    )

    @staticmethod
    def _connect_kwargs(session: Session, password: str | None = None) -> dict[str, object]:
        connect_kwargs: dict[str, object] = {
            "host": session.host,
            "port": session.port,
            "username": session.username or None,
            "x11_forwarding": session.x11_forwarding,
            "known_hosts": str(known_hosts_path()),
            "connect_timeout": 12,
            "login_timeout": 12,
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
            connect_kwargs["keepalive_interval"] = SSHClient.KEEPALIVE_INTERVAL_SECONDS
            connect_kwargs["keepalive_count_max"] = SSHClient.KEEPALIVE_COUNT_MAX
        return connect_kwargs

    @staticmethod
    def is_legacy_negotiation_error(error: Exception | str) -> bool:
        lowered = str(error).lower()
        return any(
            marker in lowered
            for marker in (
                "no matching key exchange",
                "no matching host key",
                "no matching cipher",
                "no matching mac",
                "key exchange failed",
                "algorithm negotiation",
            )
        )

    @classmethod
    def apply_legacy_algorithm_overrides(cls, connect_kwargs: dict[str, object]) -> dict[str, object]:
        merged = dict(connect_kwargs)
        merged["kex_algs"] = list(cls.LEGACY_KEX_ALGS)
        merged["server_host_key_algs"] = list(cls.LEGACY_HOST_KEY_ALGS)
        merged["encryption_algs"] = list(cls.LEGACY_ENCRYPTION_ALGS)
        merged["mac_algs"] = list(cls.LEGACY_MAC_ALGS)
        return merged

    async def verify_connectivity(self, session: Session, password: str | None = None) -> str:
        # Connectivity/auth probe should not run remote commands; some older devices
        # do not support exec requests reliably and can hang.
        connect_kwargs = self._connect_kwargs(session, password=password)
        if session.ssh_legacy_compatibility:
            async with asyncssh.connect(**self.apply_legacy_algorithm_overrides(connect_kwargs)):
                return "connected (legacy algorithm compatibility mode)"
        try:
            async with asyncssh.connect(**connect_kwargs):
                return "connected"
        except Exception as exc:
            if not self.is_legacy_negotiation_error(exc):
                raise
        async with asyncssh.connect(**self.apply_legacy_algorithm_overrides(connect_kwargs)):
            return "connected (legacy algorithm compatibility mode)"

    async def trust_and_verify(self, session: Session, password: str | None = None) -> str:
        connect_kwargs = self._connect_kwargs(session, password=password)
        connect_kwargs["known_hosts"] = None
        if session.ssh_legacy_compatibility:
            async with asyncssh.connect(**self.apply_legacy_algorithm_overrides(connect_kwargs)) as conn:
                trust_host_key(session, conn.get_server_host_key())
                return "connected (legacy algorithm compatibility mode)"
        try:
            async with asyncssh.connect(**connect_kwargs) as conn:
                trust_host_key(session, conn.get_server_host_key())
                return "connected"
        except Exception as exc:
            if not self.is_legacy_negotiation_error(exc):
                raise
        legacy_kwargs = self.apply_legacy_algorithm_overrides(connect_kwargs)
        async with asyncssh.connect(**legacy_kwargs) as conn:
            trust_host_key(session, conn.get_server_host_key())
            return "connected (legacy algorithm compatibility mode)"

    async def run_command(self, session: Session, command: str, password: str | None = None) -> str:
        async with asyncssh.connect(**self._connect_kwargs(session, password=password)) as conn:
            result = await conn.run(command, check=False)
            text = result.stdout
            if result.stderr:
                text = f"{text}\n{result.stderr}" if text else result.stderr
            return text.strip()

    async def install_public_key(
        self,
        session: Session,
        *,
        public_key_path: str,
        password: str | None = None,
        trust_unknown: bool = False,
    ) -> None:
        pub_path = Path(public_key_path).expanduser().resolve()
        if not pub_path.exists():
            raise FileNotFoundError(f"Public key file not found: {pub_path}")
        key_line = pub_path.read_text(encoding="utf-8").strip()
        if not is_openssh_public_key_line(key_line):
            raise ValueError("Public key must be in OpenSSH public format.")

        connect_kwargs = self._connect_kwargs(session, password=password)
        if trust_unknown:
            connect_kwargs["known_hosts"] = None

        async with asyncssh.connect(**connect_kwargs) as conn:
            if trust_unknown:
                trust_host_key(session, conn.get_server_host_key())
            escaped = key_line.replace("'", "'\"'\"'")
            cmd = (
                "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
                f"grep -qxF '{escaped}' ~/.ssh/authorized_keys || echo '{escaped}' >> ~/.ssh/authorized_keys"
            )
            await conn.run(cmd, check=True)
