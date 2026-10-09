"""Own room d-flopstar-close1: claim, register, heartbeat, reclaim, statement, status.

Order matters: a d- room can be claimed only before its first message, so `claim` comes first.
Rooms and notes with no write for 7 days are deleted, and a room on a single message goes after
12 hours, so `heartbeat` runs at least every 6 hours and `reclaim` rewrites the owner note
before day 6. `statement` names the 64 tree keys as Flopstar's, after the lock. Every signing
command is a dry run unless --post is given.
"""

import hashlib
import json
import re
import sqlite3
import time
from datetime import UTC, datetime

from . import config
from .didkey import verify_signature
from .evidence import save_record
from .signer import PolicySigner, compact, expected_did, load_private_key
from .technocore import TechnocoreClient
from .tree import TREE_SIZE

ROOM = config.OWN_ROOM
NOTE_CHARS = 8192   # technocore.chat's note limit
MESSAGE_CHARS = 4096
LOCK = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)   # close-1's last sweep (2556)
DID = re.compile(r"did:key:z6Mk[1-9A-HJ-NP-Za-km-z]{44}")   # as the fold checks it


def load_signer() -> PolicySigner:
    signer = PolicySigner(load_private_key())
    if signer.did != expected_did():
        raise SystemExit(f"key DID {signer.did} does not match flopstar.did; refusing to sign")
    return signer


async def note_nonce(client: TechnocoreClient) -> int:
    """Next nonce for ownership notes: above the room's shared counter and the clock."""
    current = await client.get_note("room-nonce", ROOM)
    if current is not None and not current.strip().isdigit():
        raise SystemExit(f"unexpected room-nonce value {current!r}; not signing")
    floor = int(current) if current is not None else 0
    return max(floor + 1, time.time_ns() // 1_000_000)


def tree_allow_list() -> list[str]:
    """The tree's public DIDs, in index order, from data/tree-dids.txt (`flopstar tree dids`)."""
    lines = (config.get_data_dir() / "tree-dids.txt").read_text().splitlines()
    dids = [line.split()[1] for line in lines if line.strip()]
    if len(dids) != TREE_SIZE or len(set(dids)) != TREE_SIZE or not all(
            DID.fullmatch(d) for d in dids):
        raise SystemExit(f"tree-dids.txt must hold {TREE_SIZE} distinct did:keys")
    return dids


async def write_allow_note(post: bool) -> None:
    """Allow-list the tree keys in our room: one space-separated note, owner-signed.
    The dry run needs no key; only --post loads it."""
    dids = tree_allow_list()
    value = " ".join(dids)
    if len(value) > NOTE_CHARS:
        raise SystemExit(f"allow-list is {len(value)} characters, over {NOTE_CHARS}")
    async with TechnocoreClient() as client:
        owner = (await client.get_note("room-owners", ROOM) or "").strip()
        if owner != expected_did():
            raise SystemExit(f"{ROOM} owner is {owner!r}, not Flopstar; not writing the allow-list")
        current = await client.get_note("room-allow", ROOM)
        if current is not None and current.split() == dids:
            print(f"allow-list already holds these {len(dids)} keys; nothing to do")
            return
        print(f"note:    room-allow/{ROOM}")
        print(f"current: {len(current.split()) if current else 0} keys")
        print(f"new:     {len(dids)} tree keys, {len(value)} of {NOTE_CHARS} characters")
        print(f"first:   {dids[0]}\nlast:    {dids[-1]}")
        if not post:
            print("dry run: nothing signed or sent. Re-run with --post.")
            return
        signer = load_signer()
        nonce = await note_nonce(client)
        sig = signer.sign_note("room-allow", ROOM, nonce, value)
        response = await client.set_note_signed("room-allow", ROOM, signer.did, sig, nonce, value)
        print(f"nonce: {nonce}\nHTTP {response.status_code}: {response.text[:200]}")
        readback = await client.get_note("room-allow", ROOM)
        ok = readback is not None and readback.split() == dids
        print(f"read back: {len(readback.split()) if readback else 0} keys -> "
              f"{'MATCH' if ok else 'MISMATCH'}")
        if not ok:
            raise SystemExit(1)


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


def statement_text(dids: list[str]) -> str:
    """Flopstar's claim to the tree keys; the record's `from` names the signer."""
    text = compact({
        "t": "statement", "season": config.SEASON_ID, "room": ROOM, "keys": dids,
        "sha256": hashlib.sha256(" ".join(dids).encode()).hexdigest(),
        "says": f"Flopstar operated these {len(dids)} keys as one key tree in close-1. "
                "Flopstar's own key never traded.",
    })
    if len(text) > MESSAGE_CHARS:
        raise SystemExit(f"statement is {len(text)} characters, over {MESSAGE_CHARS}")
    return text


async def post_statement(post: bool, now: datetime | None = None) -> None:
    """The statement, checked against the live allow-list; --post refuses before the lock."""
    dids = tree_allow_list()
    async with TechnocoreClient() as client:
        allow = await client.get_note("room-allow", ROOM)
    if allow is None or allow.split() != dids:
        raise SystemExit("tree-dids.txt does not match the room's allow-list; not signing")
    if post and (now or datetime.now(UTC)) < LOCK:
        raise SystemExit(f"the statement is posted after the lock ({LOCK:%Y-%m-%d %H:%M} UTC)")
    await post_message(ROOM, statement_text(dids), post,
                       str(config.get_data_dir() / "statement-close1.jsonl"))


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
    elif command == "allow":
        await write_allow_note(post)
    elif command == "heartbeat":
        await post_message(ROOM, heartbeat_text(), post, None)
    elif command == "statement":
        await post_statement(post)
    elif command == "status":
        await status()
    elif command == "verify":
        print(f"key loads and matches flopstar.did: {load_signer().did}")
    else:
        raise SystemExit("usage: flopstar room [status|verify|claim|register|allow|heartbeat|reclaim|statement] [--post]")
