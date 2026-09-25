"""did:key derivation and encrypted key loading, with a throwaway in-test key."""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flopstar.didkey import did_to_public_key
from flopstar.signer import did_from_private_key, expected_did, load_private_key


def test_did_round_trips_to_public_key():
    key = Ed25519PrivateKey.generate()
    did = did_from_private_key(key)
    assert did.startswith("did:key:z6Mk") and len(did) == 56
    sig = key.sign(b"x")
    did_to_public_key(did).verify(sig, b"x")


def test_encrypted_key_loads_with_passphrase_file(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"hunter2"),
    )
    (tmp_path / "k.pem").write_bytes(pem)
    (tmp_path / "pass").write_text("hunter2\n")
    monkeypatch.setenv("FLOPSTAR_PASSPHRASE_FILE", str(tmp_path / "pass"))
    loaded = load_private_key(tmp_path / "k.pem")
    assert did_from_private_key(loaded) == did_from_private_key(key)


def test_expected_did_is_flopstar():
    assert expected_did() == "did:key:z6MkjLpUAGLtNieLnCFQoUScwJxAKwo5PHZcHRsdyiCJG5Bv"


def test_signed_owner_message_verifies():
    from flopstar.didkey import verify_signature
    from flopstar.register import owner_message
    from flopstar.signer import sign_message

    key = Ed25519PrivateKey.generate()
    did = did_from_private_key(key)
    text = owner_message(did)
    assert text == '{"key":"' + did + '","season":"close-1","t":"owner"}'
    sig = sign_message(key, "close1", 1790347704958, text)
    assert len(sig) == 86
    assert verify_signature(did, "close1", "1790347704958", text, sig)


def _policy_signer(tmp_path):
    from flopstar.signer import PolicySigner

    return PolicySigner(Ed25519PrivateKey.generate(), log_path=tmp_path / "sig.log")


def test_policy_refuses_other_rooms_types_and_notes(tmp_path):
    import pytest

    from flopstar.signer import PolicyViolation

    signer = _policy_signer(tmp_path)
    with pytest.raises(PolicyViolation):
        signer.sign_message("lobby", 1, '{"t":"owner"}')
    with pytest.raises(PolicyViolation):
        signer.sign_message("close1", 1, '{"t":"chat"}')
    with pytest.raises(PolicyViolation):
        signer.sign_message("close1", 1, "not json")
    with pytest.raises(PolicyViolation):
        signer.sign_note("room-owners", "d-close1-price", 1, signer.did)
    with pytest.raises(PolicyViolation):
        signer.sign_note("topic", "d-flopstar-close1", 1, "x")
    assert not (tmp_path / "sig.log").exists()


def test_policy_note_signature_and_log(tmp_path):
    import base64
    import json

    from flopstar.didkey import did_to_public_key

    signer = _policy_signer(tmp_path)
    sig = signer.sign_note("room-owners", "d-flopstar-close1", 42, signer.did)
    payload = f"room-owners|d-flopstar-close1|42|{signer.did}".encode()
    did_to_public_key(signer.did).verify(base64.urlsafe_b64decode(sig + "=="), payload)
    signer.sign_message("d-flopstar-close1", 43, '{"t":"heartbeat"}')
    entries = [json.loads(line) for line in (tmp_path / "sig.log").read_text().splitlines()]
    assert [e["kind"] for e in entries] == ["note", "message"]
    assert all("PRIVATE" not in json.dumps(e) for e in entries)
