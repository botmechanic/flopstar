"""Load the owner key and derive its did:key. Never logs or prints key material."""

import base64
import getpass
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import base58
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import config


def get_passphrase() -> bytes:
    """Read the key passphrase from FLOPSTAR_PASSPHRASE_FILE, else prompt without echo."""
    path = os.environ.get("FLOPSTAR_PASSPHRASE_FILE")
    if path:
        return Path(path).expanduser().read_bytes().rstrip(b"\r\n")
    return getpass.getpass("Flopstar key passphrase: ").encode()


def load_private_key(key_path: Path | None = None) -> Ed25519PrivateKey:
    """Load the Ed25519 owner key, asking for the passphrase if it is encrypted."""
    data = (key_path or config.get_key_path()).read_bytes()
    password = get_passphrase() if b"ENCRYPTED" in data else None
    key = serialization.load_pem_private_key(data, password=password)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("owner key is not Ed25519")
    return key


def did_from_private_key(key: Ed25519PrivateKey) -> str:
    """did:key for an Ed25519 key: multicodec 0xed 0x01 + public key, base58btc with 'z'."""
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return "did:key:z" + base58.b58encode(b"\xed\x01" + public).decode()


def expected_did() -> str:
    """Flopstar's DID as recorded in the repo's flopstar.did."""
    return (Path(__file__).parents[2] / "flopstar.did").read_text().strip()


def sign_message(key: Ed25519PrivateKey, room: str, nonce: int, text: str) -> str:
    """technocore.chat did:key lane: sign `<room>|<nonce>|<text>`, 86-char unpadded base64url."""
    sig = key.sign(f"{room}|{nonce}|{text}".encode())
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def compact(obj: dict) -> str:
    """Compact single-line JSON with sorted keys, as the contest expects."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


class PolicyViolation(Exception):
    """A signature the signer policy does not allow."""


class PolicySigner:
    """
    The only way code should sign with an owner key. Enforces the signer policy:
    rooms close1 and our own room; notes room-owners / room-allow for our own room;
    message types owner, room, trade, heartbeat, statement. Every signature is logged (texts and
    signatures are public once posted; key material never is).
    """

    NOTE_NAMESPACES = ("room-owners", "room-allow")
    MESSAGE_TYPES = ("owner", "room", "trade", "heartbeat", "statement")

    def __init__(self, key: Ed25519PrivateKey, log_path: Path | None = None):
        self._key = key
        self.did = did_from_private_key(key)
        self.rooms = ("close1", config.OWN_ROOM)
        self.log_path = log_path or config.get_data_dir() / "signatures.log"

    def sign_message(self, room: str, nonce: int, text: str) -> str:
        if room not in self.rooms:
            raise PolicyViolation(f"room {room!r} is not allowed")
        try:
            kind = json.loads(text).get("t")
        except (json.JSONDecodeError, AttributeError):
            kind = None
        if kind not in self.MESSAGE_TYPES:
            raise PolicyViolation(f"message type {kind!r} is not allowed")
        sig = sign_message(self._key, room, nonce, text)
        self._log({"kind": "message", "room": room, "nonce": str(nonce), "text": text, "sig": sig})
        return sig

    def sign_note(self, namespace: str, note_key: str, nonce: int, value: str) -> str:
        if namespace not in self.NOTE_NAMESPACES or note_key != config.OWN_ROOM:
            raise PolicyViolation(f"note {namespace}/{note_key} is not allowed")
        payload = f"{namespace}|{note_key}|{nonce}|{value}"
        sig = base64.urlsafe_b64encode(self._key.sign(payload.encode())).rstrip(b"=").decode()
        self._log({"kind": "note", "note": f"{namespace}/{note_key}", "nonce": str(nonce),
                   "value": value, "sig": sig})
        return sig

    def _log(self, entry: dict) -> None:
        entry = {"at": datetime.now(UTC).isoformat(), "did": self.did, **entry}
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
