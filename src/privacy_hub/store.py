"""Short-lived, encrypted storage of placeholder mappings.

The mapping links placeholders to real values, so it is the most sensitive
artefact of the system. It is

* encrypted with AES-256-GCM before it leaves the process (key from env),
* bound to its request id (used as associated data),
* stored with a short TTL (default 300 s) and
* deleted as soon as the response has been restored (unless configured).

It is never written to Postgres, logs or the LiteLLM request dict.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod

from .crypto import MappingCipher
from .masking.model import Mapping

KEY_PREFIX = "privacyhub:mapping:"


class MappingStore(ABC):
    def __init__(self, cipher: MappingCipher) -> None:
        self.cipher = cipher

    # ``binding`` (e.g. the organisation id) is part of the AEAD associated
    # data: a ciphertext stored for one tenant cannot be opened for another.
    @staticmethod
    def _ad(request_id: str, binding: str) -> bytes:
        return f"{request_id}|{binding}".encode()

    def _seal(self, request_id: str, mapping: Mapping, binding: str) -> bytes:
        plaintext = json.dumps(mapping.to_dict(), ensure_ascii=False).encode()
        return self.cipher.encrypt(plaintext, self._ad(request_id, binding))

    def _open(self, request_id: str, blob: bytes, binding: str) -> Mapping:
        return Mapping.from_dict(json.loads(self.cipher.decrypt(blob, self._ad(request_id, binding))))

    @abstractmethod
    async def put(self, request_id: str, mapping: Mapping, ttl_seconds: int, binding: str = "") -> None: ...

    @abstractmethod
    async def get(self, request_id: str, binding: str = "") -> Mapping | None: ...

    @abstractmethod
    async def delete(self, request_id: str) -> None: ...

    async def ping(self) -> bool:
        return True


class InMemoryMappingStore(MappingStore):
    """Process-local store (tests, single-process deployments). Values are
    still encrypted so a heap dump does not trivially reveal mappings."""

    def __init__(self, cipher: MappingCipher) -> None:
        super().__init__(cipher)
        self._data: dict[str, tuple[float, bytes]] = {}

    def _gc(self) -> None:
        now = time.monotonic()
        for k in [k for k, (exp, _) in self._data.items() if exp <= now]:
            del self._data[k]

    async def put(self, request_id, mapping, ttl_seconds, binding=""):
        self._gc()
        self._data[request_id] = (time.monotonic() + ttl_seconds, self._seal(request_id, mapping, binding))

    async def get(self, request_id, binding=""):
        self._gc()
        item = self._data.get(request_id)
        return None if item is None else self._open(request_id, item[1], binding)

    async def delete(self, request_id):
        self._data.pop(request_id, None)

    def raw_items(self) -> dict[str, bytes]:
        """For tests: the ciphertexts as stored."""
        return {k: v for k, (_, v) in self._data.items()}


class RedisMappingStore(MappingStore):
    def __init__(self, cipher: MappingCipher, url: str) -> None:
        super().__init__(cipher)
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, socket_timeout=2.0, socket_connect_timeout=2.0)

    async def put(self, request_id, mapping, ttl_seconds, binding=""):
        blob = self._seal(request_id, mapping, binding)
        await self._redis.set(KEY_PREFIX + request_id, blob, ex=max(1, int(ttl_seconds)))

    async def get(self, request_id, binding=""):
        blob = await self._redis.get(KEY_PREFIX + request_id)
        return None if blob is None else self._open(request_id, blob, binding)

    async def delete(self, request_id):
        await self._redis.delete(KEY_PREFIX + request_id)

    async def ping(self) -> bool:
        return bool(await self._redis.ping())
