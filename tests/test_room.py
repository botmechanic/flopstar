"""The post-lock statement naming the tree keys, with throwaway keys and no network."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flopstar import room
from flopstar.didkey import verify_signature
from flopstar.signer import PolicySigner, did_from_private_key


def tree_dids(n=64):
    return [did_from_private_key(Ed25519PrivateKey.generate()) for _ in range(n)]


def test_statement_fits_a_message_and_names_the_keys_in_order():
    dids = tree_dids()
    text = room.statement_text(dids)
    assert len(text) <= room.MESSAGE_CHARS
    body = json.loads(text)
    assert body["t"] == "statement" and body["season"] == "close-1"
    assert body["room"] == "d-flopstar-close1" and body["keys"] == dids
    assert body["sha256"] == hashlib.sha256(" ".join(dids).encode()).hexdigest()
    assert text == json.dumps(body, sort_keys=True, separators=(",", ":"))


def test_statement_signs_under_the_policy(tmp_path):
    signer = PolicySigner(Ed25519PrivateKey.generate(), log_path=tmp_path / "sig.log")
    text = room.statement_text(tree_dids())
    sig = signer.sign_message("d-flopstar-close1", 7, text)
    assert verify_signature(signer.did, "d-flopstar-close1", "7", text, sig)


class FakeClient:
    allow = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_note(self, namespace, key):
        assert (namespace, key) == ("room-allow", "d-flopstar-close1")
        return FakeClient.allow


@pytest.fixture
def statement(monkeypatch):
    dids = tree_dids()
    posted = []
    monkeypatch.setattr(room, "tree_allow_list", lambda: dids)
    monkeypatch.setattr(room, "TechnocoreClient", FakeClient)

    async def fake_post(room_, text, post, evidence):
        posted.append((room_, text, post))

    monkeypatch.setattr(room, "post_message", fake_post)
    FakeClient.allow = " ".join(dids)
    return dids, posted


def test_statement_post_refuses_before_the_lock(statement):
    _, posted = statement
    before = datetime(2026, 10, 4, 8, 59, 59, tzinfo=UTC)
    with pytest.raises(SystemExit, match="after the lock"):
        asyncio.run(room.post_statement(True, now=before))
    asyncio.run(room.post_statement(False, now=before))        # the dry run is fine any time
    asyncio.run(room.post_statement(True, now=room.LOCK))
    assert [p[2] for p in posted] == [False, True]


def test_statement_refuses_when_the_allow_list_differs(statement):
    dids, posted = statement
    FakeClient.allow = " ".join(dids[:-1])
    with pytest.raises(SystemExit, match="allow-list"):
        asyncio.run(room.post_statement(True, now=room.LOCK))
    assert posted == []
