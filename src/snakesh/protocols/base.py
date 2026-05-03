from __future__ import annotations

from abc import ABC, abstractmethod

from snakesh.core.models import Session


class ProtocolError(RuntimeError):
    pass


class ProtocolClient(ABC):
    @abstractmethod
    async def verify_connectivity(self, session: Session) -> str:
        raise NotImplementedError

