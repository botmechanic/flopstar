"""Own room d-flopstar-close1: claim, register, heartbeat, reclaim, status.

Order matters: a d- room can be claimed only before its first message, so `claim` comes first.
Rooms and notes with no write for 7 days are deleted, and a room on a single message goes after
12 hours, so `heartbeat` runs at least every 6 hours and `reclaim` rewrites the owner note
before day 6. Every signing command is a dry run unless --post is given.
"""

import json
import sqlite3
import time
from datetime import UTC, datetime

from . import config
from .didkey import verify_signature
from .evidence import save_record
from .signer import PolicySigner, compact, expected_did, load_private_key
from .technocore import TechnocoreClient

ROOM = config.OWN_ROOM


def load_signer() -> PolicySigner:
    signer = PolicySigner(load_private_key())
    if signer.did != expected_did():
        raise SystemExit(f"key DID {signer.did} does not match flopstar.did; refusing to sign")
    return signer


async def note_nonce(client: TechnocoreClient) -> int:
    """Next nonce for ownership notes: above the room's shared counter and the clock."""
    current = await client.get_note("room-nonce", ROOM)
    floor = int(current.strip()) if current and current.strip().isdigit() else 0
    return max(floor + 1, time.time_ns() // 1_000_000)


async def write_owner_note(post: bool, if_absent: bool) -> None:
    signer = load_signer()
    async with TechnocoreClient() as client:
        owner = await client.get_note("room-owners", ROOM)
        if if_absent and owner is not None:
            raise SystemExit(f"{ROOM} already has an owner note: {owner.strip()}")
        if not if_absent and (owner or "").strip() != signer.did:
            raise SystemExit(f"{ROOM} owner is {owner!r}, not us; refusing to rewrite")
        nonce = await note_nonce(client)
        print(f"note:  room-owners/{ROOM}{'  (if_absent)' if if_absent else ''}")
        print(f"value: {signer.did}\nnonce: {nonce}")
        if not post:
            print("dry run: nothing signed or sent. Re-run with --post.")
            return
        sig = signer.sign_note("room-owners", ROOM, nonce, signer.did)
        response = await client.set_note_signed(
            "room-owners", ROOM, signer.did, sig, nonce, signer.did, if_absent=if_absent)
        print(f"HTTP {response.status_code}: {response.text[:200]}")
        readback = await client.get_note("room-owners", ROOM)
        ok = (readback or "").strip() == signer.did
        print(f"read back: {readback!r} -> {'OWNED BY FLOPSTAR' if ok else 'NOT OURS'}")
        if not ok:
            raise SystemExit(1)


async def post_message(room: str, text: str, post: bool, evidence: str | None) -> None:
    signer = load_signer()
    nonce = time.time_ns() // 1_000_000
    print(f"room:  {room}\nnonce: {nonce}\ntext:  {text}")
    if not post:
        print("dry run: nothing signed or sent. Re-run with --post.")
        return
    sig = signer.sign_message(room, nonce, text)
    if not verify_signature(signer.did, room, str(nonce), text, sig):
        raise SystemExit("signature failed local verification; not posting")
    async with TechnocoreClient() as client:
        response = await client.post_signed(room, signer.did, sig, nonce, text)
        print(f"HTTP {response.status_code}: {response.text.splitlines()[0][:200]}")
        if response.status_code != 200:
            raise SystemExit(1)
        if evidence:
            record = await save_record(client, room, signer.did, nonce, evidence)
            print(f"evidence: seq {record['seq']} saved to {evidence}" if record
                  else "WARNING: posted, but the record was not found in the export")


def heartbeat_text() -> str:
    at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return compact({"t": "heartbeat", "season": config.SEASON_ID, "at": at})


async def status() -> None:
    async with TechnocoreClient() as client:
        owner = await client.get_note("room-owners", ROOM)
        allow = await client.get_note("room-allow", ROOM)
        rnonce = await client.get_note("room-nonce", ROOM)
        msgs = await client.get_room(ROOM, limit=1)
    print(f"owner note:  {owner.strip() if owner else None}"
          f"{'  (Flopstar)' if owner and owner.strip() == expected_did() else ''}")
    print(f"allow list:  {len(allow.split()) if allow else 0} keys")
    print(f"room-nonce:  {rnonce.strip() if rnonce else None}")
    print(f"last message: {msgs[-1]['seq']} at {msgs[-1]['ts']}" if msgs else "no messages")
    db = sqlite3.connect(config.get_data_dir() / "flopstar.db")
    listed = [
        json.loads(json.loads(raw)["text"])["n"]
        for (raw,) in db.execute(
            "SELECT raw_json FROM messages WHERE room='d-close1-flow' ORDER BY seq")
        if ROOM in json.loads(json.loads(raw)["text"]).get("rooms", [])
    ]
    print(f"listed by referee: sweep {listed[0]}" if listed else "listed by referee: not yet")


async def run_room(args: list[str]) -> None:
    post = "--post" in args
    command = args[0] if args else "status"
    data = config.get_data_dir()
    if command == "claim":
        await write_owner_note(post, if_absent=True)
    elif command == "reclaim":
        await write_owner_note(post, if_absent=False)
    elif command == "register":
        text = compact({"t": "room", "season": config.SEASON_ID, "room": ROOM})
        await post_message("close1", text, post, str(data / "registration-room.jsonl"))
    elif command == "heartbeat":
        await post_message(ROOM, heartbeat_text(), post, None)
    elif command == "status":
        await status()
    elif command == "verify":
        print(f"key loads and matches flopstar.did: {load_signer().did}")
    else:
        raise SystemExit("usage: flopstar room [status|verify|claim|register|heartbeat|reclaim] [--post]")
