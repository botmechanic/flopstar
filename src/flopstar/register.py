"""Register Flopstar as a close-1 owner: {"t":"owner","season":"close-1","key":<did>}."""

import time

from . import config
from .didkey import verify_signature
from .signer import PolicySigner, compact, expected_did, load_private_key
from .technocore import TechnocoreClient

REGISTER_ROOM = "close1"


def owner_message(did: str) -> str:
    return compact({"t": "owner", "season": config.SEASON_ID, "key": did})


async def run_register(post: bool = False) -> None:
    signer = PolicySigner(load_private_key())
    did = signer.did
    if did != expected_did():
        raise SystemExit(f"key DID {did} does not match flopstar.did; refusing to register")

    text = owner_message(did)
    nonce = time.time_ns() // 1_000_000   # millisecond clock: always above our last nonce
    sig = signer.sign_message(REGISTER_ROOM, nonce, text)
    if not verify_signature(did, REGISTER_ROOM, str(nonce), text, sig):
        raise SystemExit("signature failed local verification; not posting")

    print(f"room:  {REGISTER_ROOM}")
    print(f"did:   {did}")
    print(f"nonce: {nonce}")
    print(f"text:  {text}")
    print("signature verified locally")

    if not post:
        print("dry run: nothing sent. Re-run with --post to register.")
        return

    async with TechnocoreClient() as client:
        response = await client.post_signed(REGISTER_ROOM, did, sig, nonce, text)
    print(f"HTTP {response.status_code}: {response.text[:300]}")
    if response.status_code != 200:
        raise SystemExit(1)
    print("posted. The next sweep's d-close1-flow should mint 10,000 POLF to this key.")
