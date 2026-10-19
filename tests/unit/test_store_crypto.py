import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from privacy_hub.crypto import MappingCipher, MappingKeyError, load_key
from privacy_hub.masking.model import Mapping, MappingEntry
from privacy_hub.store import InMemoryMappingStore


def _key() -> bytes:
    return os.urandom(32)


def _mapping() -> Mapping:
    return Mapping("bracket", "hu", [MappingEntry("PERSON_1", "PERSON", "Kovács Péter", "[PERSON_1]")])


def test_load_key_variants():
    raw = os.urandom(32)
    assert load_key(base64.b64encode(raw).decode()) == raw
    assert load_key(base64.urlsafe_b64encode(raw).decode().rstrip("=")) == raw
    for bad in (None, "", "not base64!!", base64.b64encode(b"short").decode()):
        with pytest.raises(MappingKeyError):
            load_key(bad)


async def test_store_keeps_only_ciphertext():
    store = InMemoryMappingStore(MappingCipher(_key()))
    await store.put("req1", _mapping(), ttl_seconds=60, binding="org-a")
    blob = store.raw_items()["req1"]
    assert "Kovács".encode() not in blob and b"PERSON_1" not in blob
    assert await store.get("req1", binding="org-a") == _mapping()


async def test_ciphertext_is_bound_to_request_and_org():
    store = InMemoryMappingStore(MappingCipher(_key()))
    await store.put("req1", _mapping(), ttl_seconds=60, binding="org-a")
    with pytest.raises(InvalidTag):
        await store.get("req1", binding="org-b")
    # moving the blob under another request id must fail too
    store._data["req2"] = store._data["req1"]
    with pytest.raises(InvalidTag):
        await store.get("req2", binding="org-a")


async def test_ttl_expiry_and_delete():
    store = InMemoryMappingStore(MappingCipher(_key()))
    await store.put("req1", _mapping(), ttl_seconds=60)
    await store.delete("req1")
    assert await store.get("req1") is None
    await store.put("req2", _mapping(), ttl_seconds=60)
    exp, blob = store._data["req2"]
    store._data["req2"] = (exp - 120, blob)  # simulate expiry
    assert await store.get("req2") is None


def test_wrong_key_cannot_decrypt():
    a, b = MappingCipher(_key()), MappingCipher(_key())
    blob = a.encrypt(b"secret", b"ad")
    with pytest.raises(InvalidTag):
        b.decrypt(blob, b"ad")
