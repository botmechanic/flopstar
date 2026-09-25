"""DID:key handling and Ed25519 signature verification."""

import base64

import base58
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def did_to_public_key(did: str) -> Ed25519PublicKey:
    """
    Convert a did:key (Ed25519) to a public key.

    Format: did:key:z6Mk... where the multibase-encoded part after 'z' is:
    - 0xed (multicodec for Ed25519 public key)
    - 0x01 (varint length)
    - 32 bytes of public key
    """
    if not did.startswith("did:key:z6Mk"):
        raise ValueError(f"Invalid did:key format: {did}")

    # Remove the 'did:key:z' prefix
    multibase_str = did[len("did:key:z"):]

    # Decode base58btc (the 'z' indicates base58btc)
    try:
        decoded = base58.b58decode(multibase_str)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Failed to decode base58: {e}")

    # Check multicodec prefix (0xed, 0x01 for Ed25519)
    if len(decoded) < 2 or decoded[0] != 0xed or decoded[1] != 0x01:
        raise ValueError(f"Invalid multicodec prefix for Ed25519: {decoded[:2].hex()}")

    # Extract the 32-byte public key
    public_key_bytes = decoded[2:]
    if len(public_key_bytes) != 32:
        raise ValueError(f"Invalid public key length: {len(public_key_bytes)}")

    return Ed25519PublicKey.from_public_bytes(public_key_bytes)


def verify_signature(
    did: str,
    room: str,
    nonce: str,
    text: str,
    signature: str,
) -> bool:
    """
    Verify an Ed25519 signature over the exact message format.

    Message format: "<room>|<nonce>|<text>"
    Nonce is kept as exact digit string (can exceed 2^53).
    Signature is 86 characters of unpadded base64url (technocore.chat's did:key lane).

    Returns True if signature is valid, False otherwise.
    """
    try:
        public_key = did_to_public_key(did)
    except ValueError:
        return False

    # Construct the signed message
    message = f"{room}|{nonce}|{text}"
    message_bytes = message.encode('utf-8')

    # Decode signature from unpadded base64url
    if len(signature) != 86:
        return False
    try:
        signature_bytes = base64.urlsafe_b64decode(signature + "==")
    except ValueError:
        return False

    # Verify
    try:
        public_key.verify(signature_bytes, message_bytes)
        return True
    except InvalidSignature:
        return False
