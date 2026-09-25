"""The close-1 tree trader: runs the plan in docs/TREE.md against the live referee.

Each pass of the loop:
1. Read the referee's verified posts from the monitor's store (the monitor must be running).
2. For each new sweep, rebuild the shadow fold (our 64 keys' mints and trades through the
   vendored fold, our ground truth for cash and positions), then check it against the referee:
   any of our trades void, a missed read of our room, or a pnl/positions top-list entry for one
   of our keys that disagrees with the shadow fold trips the kill switch.
3. Once every trade we posted has been applied, the referee is on time and we are in the safe
   middle of a sweep window, ask the tree for the next trades, check them against a copy of the
   shadow fold, and post them. They land in the next sweep.

Paper mode (the default) signs nothing: it uses throwaway keys and records each trade it would
post as stamped now, so the tree can be watched on the real price path. `--live` posts with the
real tree keys. The kill switch is a file (data/KILL, or data/KILL-paper): while it exists
nothing is signed. Delete it to resume, after finding out why it tripped.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import ClassVar

import httpx

from . import config
from .didkey import verify_signature
from .dryrun import load_fold_module
from .hyperliquid import last_trade
from .signer import compact
from .technocore import TechnocoreClient
from .tree import CENT, TREE_SIZE, UNTIL_SWEEPS, Tree, get_seed_path, load_seed

logger = logging.getLogger(__name__)

FIRST_CUT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)   # sweep n closes at FIRST_CUT + 5n min
SWEEP = timedelta(seconds=300)
LOCK_SWEEP = 2556
MINT = Decimal(10000)
POST_AFTER = timedelta(seconds=45)    # post no earlier than this after a sweep's cut...
POST_BEFORE = timedelta(seconds=75)   # ...and no later than this before the next cut
LIMIT_MARGIN = Decimal("0.045")       # the referee's window is 5%; keep inside 4.5%
LOOP_SECONDS = 5


def sweep_time(n: int) -> datetime:
    return FIRST_CUT + n * SWEEP


def sweep_at(ts: datetime) -> int:
    """The sweep that applies a message stamped at ts: the first cut after it."""
    return int((ts - FIRST_CUT) // SWEEP) + 1


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class Referee:
    """The referee's verified posts, read incrementally from the monitor's store."""

    ROOMS: ClassVar[dict[str, str]] = {"price": "d-close1-price", "flow": "d-close1-flow",
                                       "pnl": "d-close1-pnl", "positions": "d-close1-positions"}

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.posts: dict[str, dict[int, dict]] = {k: {} for k in self.ROOMS}
        self.last_seq: dict[str, int] = {k: 0 for k in self.ROOMS}

    def refresh(self) -> None:
        db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)
        try:
            for kind, room in self.ROOMS.items():
                rows = db.execute("SELECT seq, raw_json FROM messages WHERE room=? AND seq>? "
                                  "ORDER BY seq", (room, self.last_seq[kind])).fetchall()
                for seq, raw in rows:
                    self.last_seq[kind] = seq
                    text = json.loads(json.loads(raw)["text"])
                    if isinstance(text, dict) and text.get("t") == kind and "n" in text:
                        self.posts[kind][int(text["n"])] = text
        finally:
            db.close()

    def latest(self) -> int | None:
        """The newest sweep with both its price and flow posts."""
        both = self.posts["price"].keys() & self.posts["flow"].keys()
        return max(both) if both else None

    def prices(self, n: int) -> tuple[str, str] | None:
        """(applied, close) for sweep n: the limit reference (the previous sweep's reference)
        and the close that prices the fee (this sweep's reference)."""
        post = self.posts["price"].get(n)
        if not post:
            return None
        applied = post.get("applied") or (self.posts["price"].get(n - 1) or {}).get("ref", {}).get("px")
        return (applied, post["ref"]["px"]) if applied else None

    def room_listed(self, room: str) -> int | None:
        for n in sorted(self.posts["flow"]):
            if room in self.posts["flow"][n].get("rooms", []):
                return n
        return None


class Trader:
    def __init__(self, live: bool, data_dir: Path | None = None, signer=None,
                 need_signer: bool = True):
        self.live = live
        self.data = data_dir or config.get_data_dir()
        suffix = "" if live else "-paper"
        self.state_path = self.data / f"trader{suffix}.json"
        self.kill_path = self.data / f"KILL{suffix}"
        self.referee = Referee(self.data / "flopstar.db")
        self.fold_mod = load_fold_module()
        self.state = self._load_state()
        self.signer = signer
        self.clock = lambda: datetime.now(UTC)
        if live and need_signer and signer is None:
            from .treesigner import TreeSigner
            self.signer = TreeSigner(load_seed(get_seed_path()))
        if self.signer is not None and self.signer.dids != self.state["tree"]["dids"]:
            raise SystemExit("tree keys do not match the saved state; refusing to run")

    # ---- state ----

    def _load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text())
        if self.live:
            from .tree import tree_dids
            dids = tree_dids(load_seed(get_seed_path()))
            paper_seed = None
        else:
            paper_seed = os.urandom(32).hex()   # throwaway keys: never the real master seed
            from .tree import tree_dids
            dids = tree_dids(bytes.fromhex(paper_seed))
        return {"mode": "live" if self.live else "paper", "paper_seed": paper_seed,
                "sweep": None, "stepped": None, "mints": {}, "trades": [], "nonces": {},
                "tree": Tree(dids).to_dict()}

    def save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True))
        os.replace(tmp, self.state_path)

    @property
    def dids(self) -> list[str]:
        return self.state["tree"]["dids"]

    def killed(self) -> str | None:
        if not self.kill_path.exists():
            return None
        return self.kill_path.read_text().strip() or "killed"

    def kill(self, reason: str) -> None:
        logger.error(f"KILL SWITCH: {reason}")
        if not self.kill_path.exists():
            self.kill_path.write_text(f"{datetime.now(UTC).isoformat()} {reason}\n")

    # ---- shadow fold ----

    def shadow(self, through: int | None) -> tuple[object, dict[str, dict]]:
        """Our keys' fold over every mint and applied trade up to sweep `through`."""
        fold = self.fold_mod.Fold({"lock_sweep": LOCK_SWEEP})
        outcomes: dict[str, dict] = {}
        with localcontext() as ctx:
            ctx.prec = 60
            fold.seed("1")
            if through is None:
                return fold, outcomes
            events: dict[int, tuple[list, list]] = {}
            for did, n in self.state["mints"].items():
                events.setdefault(n, ([], []))[0].append(did)
            for t in self.state["trades"]:
                if t["sweep"] is not None and t["status"] != "prepared":
                    events.setdefault(t["sweep"], ([], []))[1].append(t)
            for n in sorted(k for k in events if k <= through):
                owners, trades = events[n]
                trades.sort(key=lambda t: t["seq"])
                applied, close = self.referee.prices(n)
                out = fold.sweep(n, applied, close, owners,
                                 [{**t["terms"], "countersigner": t["terms"]["taker"]}
                                  for t in trades])
                for o in out["trades"]:
                    outcomes[o["id"]] = o
        return fold, outcomes

    def book(self, fold) -> tuple[dict, dict]:
        cash = {k: fold.accounts[k].cash if k in fold.accounts else Decimal(0) for k in self.dids}
        pos = {k: fold.accounts[k].position if k in fold.accounts else Decimal(0)
               for k in self.dids}
        return cash, pos

    # ---- reconciling each sweep ----

    def process(self, n: int) -> None:
        """Apply sweep n to the shadow fold and check it against the referee's posts."""
        if self.referee.prices(n) is None:
            if any(t["sweep"] == n for t in self.state["trades"]) or n in self.state["mints"].values():
                self.kill(f"no price post for sweep {n}, which holds our trades or mints")
            self.state["sweep"] = n
            return
        fold, outcomes = self.shadow(n)
        ours = {t["terms"]["id"]: t for t in self.state["trades"] if t["sweep"] == n}
        flow = self.referee.posts["flow"][n]
        void_ids = {v[0] if isinstance(v, list) else v: v for v in flow.get("void", [])}
        settled_ids = set(flow.get("settled", []))
        for tid, t in ours.items():
            local = outcomes.get(tid, {})
            t["status"] = local.get("outcome", "void")
            if t["status"] != "settled":
                self.kill(f"sweep {n}: shadow fold voids our trade {tid}: {local.get('reason')}")
            if tid in void_ids:
                t["status"] = "void"
                self.kill(f"sweep {n}: referee voided our trade {tid}: {void_ids[tid]}")
            elif tid in settled_ids:
                t["confirmed"] = True
        if config.OWN_ROOM in json.dumps(flow.get("missed", [])):
            self.kill(f"sweep {n}: referee missed messages in {config.OWN_ROOM}")
        self.check_tops(n, fold)
        self.state["sweep"] = n
        if ours:
            confirmed = sum(1 for t in ours.values() if t.get("confirmed"))
            logger.info(f"sweep {n}: {len(ours)} of our trades applied, "
                        f"{confirmed} listed as settled by the referee, none void")

    def check_tops(self, n: int, fold) -> None:
        """Any of our keys on the referee's pnl or positions top lists must match the fold."""
        mine = set(self.dids)
        pnl = self.referee.posts["pnl"].get(n)
        if pnl:
            mark = Decimal(pnl["mark"])
            for did, value in pnl.get("top", []):
                if did in mine and did in fold.accounts:
                    ours = fold.accounts[did].value_at(mark) - MINT
                    if abs(ours - Decimal(value)) > Decimal("0.011"):
                        self.kill(f"sweep {n}: pnl for {did} is {value}, shadow says {ours:.2f}")
        positions = self.referee.posts["positions"].get(n)
        if positions:
            for did, value in positions.get("top", []):
                if (did in mine and did in fold.accounts
                        and fold.accounts[did].position != Decimal(value)):
                    self.kill(f"sweep {n}: position for {did} is {value}, shadow says "
                              f"{fold.accounts[did].position}")

    # ---- trading ----

    def ready_to_step(self, now: datetime) -> int | None:
        """The sweep to step on, if the tree may act now."""
        latest = self.state["sweep"]
        if latest is None or latest != self.referee.latest() or latest + 1 > LOCK_SWEEP:
            return None
        if self.state["stepped"] == latest:
            return None
        if not sweep_time(latest) + POST_AFTER <= now <= sweep_time(latest + 1) - POST_BEFORE:
            return None
        if any(t["sweep"] is None or t["sweep"] > latest for t in self.state["trades"]):
            return None
        if len(self.state["mints"]) < TREE_SIZE or max(self.state["mints"].values()) > latest:
            return None
        return latest

    async def step(self, n: int, http: httpx.AsyncClient, client: TechnocoreClient | None):
        _, close = self.referee.prices(n)
        ref = Decimal(close)
        try:
            hl = await last_trade(http)
        except httpx.HTTPError as e:
            logger.warning(f"Hyperliquid read failed ({e!r}); not stepping yet")
            return
        if hl is None:
            logger.warning("no Hyperliquid trade; not stepping this sweep")
            return
        if abs(hl.px / ref - 1) > LIMIT_MARGIN:
            logger.warning(f"Hyperliquid {hl.px} is outside {LIMIT_MARGIN:.1%} of the reference "
                           f"{ref}; not stepping this sweep")
            return
        fold, _ = self.shadow(n)
        cash, pos = self.book(fold)
        tree = Tree.from_dict(self.state["tree"])
        intents = tree.step(n, ref, cash, pos)
        self.state["tree"] = tree.to_dict()
        self.state["stepped"] = n
        if not intents:
            self.save()
            return
        px = hl.px.quantize(CENT)
        trades = [{"terms": {"id": secrets.token_hex(8), "maker": i.maker, "px": str(px),
                             "qty": str(i.qty), "side": i.side, "taker": i.taker,
                             "until": n + UNTIL_SWEEPS},
                   "purpose": i.purpose, "status": "prepared", "seq": None, "ts": None,
                   "sweep": None, "confirmed": False} for i in intents]
        problem = self.precheck(fold, n + 1, str(ref), str(px), trades)
        if problem:
            self.save()
            self.kill(f"sweep {n}: planned trades would not settle: {problem}")
            return
        self.state["trades"].extend(trades)
        self.save()                      # the tree has moved: record it before posting anything
        logger.info(f"sweep {n}: posting {len(trades)} trades at {px} "
                    f"({', '.join(sorted({t['purpose'] for t in trades}))})")
        for t in trades:
            if self.killed():
                return
            await self.post_trade(t, client)
            self.save()

    def precheck(self, fold, n: int, ref: str, px: str, trades: list[dict]) -> str | None:
        """Run the planned trades through a copy of the shadow fold, with the close at px."""
        trial = copy.deepcopy(fold)
        with localcontext() as ctx:
            ctx.prec = 60
            out = trial.sweep(n, ref, px, [], [{**t["terms"], "countersigner": t["terms"]["taker"]}
                                               for t in trades])
        bad = [o for o in out["trades"] if o["outcome"] != "settled"]
        return f"{len(bad)} void, e.g. {bad[0]}" if bad else None

    def next_nonce(self, did: str) -> int:
        nonce = max(self.state["nonces"].get(did, 0) + 1, time.time_ns() // 1_000_000)
        self.state["nonces"][did] = nonce
        return nonce

    async def post(self, did: str, text: str, client: TechnocoreClient | None) -> dict | None:
        """Post text from a tree key in our room; return the stored record (paper: a stand-in)."""
        room = config.OWN_ROOM
        if not self.live:
            return {"seq": time.time_ns(), "ts": self.clock().isoformat()}
        nonce = self.next_nonce(did)
        sig = self.signer.sign_message(did, room, nonce, text)
        if not verify_signature(did, room, str(nonce), text, sig):
            self.kill("a tree signature failed local verification")
            return None
        try:
            for _ in range(2):
                response = await client.post_signed(room, did, sig, nonce, text)
                if response.status_code != 429:
                    break
                await client._wait_if_rate_limited()
        except httpx.RequestError as e:
            # the post may or may not have landed: look before doing anything else
            record = await find_in_room(client, did, nonce)
            if record is None:
                self.kill(f"post from {did} failed ({e!r}) and is not in the room")
            return record
        if response.status_code != 200:
            self.kill(f"post from {did} refused: HTTP {response.status_code} "
                      f"{response.text.splitlines()[0][:200] if response.text else ''}")
            return None
        record = find_record(response, did, nonce) or await find_in_room(client, did, nonce)
        if record is None:
            self.kill(f"posted from {did} (nonce {nonce}) but could not find the record")
            return None
        with (self.data / "tree-posts.jsonl").open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    async def post_trade(self, t: dict, client: TechnocoreClient | None) -> None:
        text = self.signer.trade_text(t["terms"]) if self.live else ""
        record = await self.post(t["terms"]["maker"], text, client)
        if record is None:
            return
        t.update(status="posted", seq=record["seq"], ts=record["ts"],
                 sweep=sweep_at(parse_ts(record["ts"])))

    async def recover(self, client: TechnocoreClient | None) -> None:
        """Trades saved as prepared but not known to be posted: a restart mid-post."""
        for t in self.state["trades"]:
            if t["status"] != "prepared":
                continue
            record = await find_in_room(client, t["terms"]["maker"], None, t["terms"]["id"]) \
                if self.live else None
            if record:
                t.update(status="posted", seq=record["seq"], ts=record["ts"],
                         sweep=sweep_at(parse_ts(record["ts"])))
                if self.state["sweep"] is not None and t["sweep"] <= self.state["sweep"]:
                    self.kill(f"trade {t['terms']['id']} was found posted in sweep {t['sweep']}, "
                              "which was already processed without it; review before resuming")
            elif self.state["stepped"] == self.referee.latest() and self.ready_window():
                await self.post_trade(t, client)
            else:
                self.kill(f"trade {t['terms']['id']} was prepared but never posted, and its "
                          "window has passed; review the tree state before resuming")
                return
        self.save()

    def ready_window(self) -> bool:
        n, now = self.state["stepped"], self.clock()
        return sweep_time(n) + POST_AFTER <= now <= sweep_time(n + 1) - POST_BEFORE

    # ---- registration (live only) ----

    async def register(self, post: bool) -> None:
        self.referee.refresh()
        listed = self.referee.room_listed(config.OWN_ROOM)
        todo = [d for d in self.dids if d not in self.state["mints"]]
        print(f"{config.OWN_ROOM} listed by the referee: {f'sweep {listed}' if listed else 'no'}")
        print(f"tree keys to register: {len(todo)} of {len(self.dids)}")
        async with TechnocoreClient() as client:
            allow = (await client.get_note("room-allow", config.OWN_ROOM) or "").split()
            missing = [d for d in todo if d not in allow]
            print(f"allow-list has {len(allow)} keys; {len(missing)} tree keys missing from it")
            if not post:
                print("dry run: nothing signed or sent. Re-run with --post.")
                return
            if listed is None or missing:
                raise SystemExit("the room must be listed and every tree key allow-listed first")
            for did in todo:
                text = compact({"t": "owner", "season": config.SEASON_ID, "key": did})
                record = await self.post(did, text, client)
                if record is None:
                    raise SystemExit(f"registration stopped: {self.killed()}")
                self.state["mints"][did] = sweep_at(parse_ts(record["ts"]))
                self.save()
        print(f"registered {len(todo)} tree keys; mints at sweep "
              f"{sorted(set(self.state['mints'].values()))}")

    # ---- the loop ----

    async def run(self) -> None:
        mode = "LIVE" if self.live else "paper"
        logger.info(f"trader starting in {mode} mode; state {self.state_path}")
        async with httpx.AsyncClient() as http, TechnocoreClient() as client:
            self.referee.refresh()
            if not self.live and not self.state["mints"]:
                start = self.referee.latest()
                if start is None:
                    raise SystemExit("no referee sweeps in the store yet; is the monitor running?")
                self.state["mints"] = {d: start for d in self.dids}
                self.state["sweep"] = start - 1
            await self.recover(client)
            while True:
                try:
                    try:
                        self.referee.refresh()
                    except sqlite3.OperationalError as e:
                        logger.warning(f"store not readable yet ({e}); retrying")
                        await asyncio.sleep(LOOP_SECONDS)
                        continue
                    latest = self.referee.latest()
                    start = self.state["sweep"]
                    if latest is not None:
                        first = start + 1 if start is not None else min(
                            self.referee.posts["price"])
                        for n in range(first, latest + 1):
                            self.process(n)
                        if latest != start:
                            self.save()
                    n = self.ready_to_step(self.clock())
                    if n is not None and not self.killed():
                        await self.step(n, http, client)
                except Exception:
                    logger.exception("trader loop error")
                    self.kill("unexpected error in the trader loop; see the log")
                await asyncio.sleep(LOOP_SECONDS)

    # ---- status ----

    def status(self) -> None:
        self.referee.refresh()
        s = self.state
        tree = Tree.from_dict(s["tree"])
        print(f"mode {s['mode']}; processed through sweep {s['sweep']}; "
              f"referee latest {self.referee.latest()}; kill switch: {self.killed() or 'off'}")
        print(f"minted keys: {len(s['mints'])} of {len(self.dids)}; trades: {len(s['trades'])} "
              f"({sum(t['status'] == 'settled' for t in s['trades'])} settled, "
              f"{sum(t.get('confirmed', False) for t in s['trades'])} listed by the referee)")
        print(f"tree: round {tree.rounds}, splits {tree.splits}, live {len(tree.live)}, "
              f"round open {tree.round_open}, pending reopens {len(tree.pending)}")
        for line in tree.log[-8:]:
            print(f"  {line}")
        if s["sweep"] is None or not s["mints"]:
            return
        fold, _ = self.shadow(s["sweep"])
        _, close = self.referee.prices(s["sweep"]) or (None, None)
        if close is None:
            return
        px = Decimal(close)
        rows = sorted(((fold.accounts[k].value_at(px) - MINT, k) for k in self.dids
                       if k in fold.accounts), reverse=True)
        print(f"scores at the latest reference {px}:")
        for score, k in rows[:5]:
            print(f"  {score:+10.2f}  pos {fold.accounts[k].position:>7}  {k}")
        print(f"  tree total {sum(r[0] for r in rows):+.2f} (fees {fold.fees:.2f})")


def find_record(response: httpx.Response, did: str, nonce: int) -> dict | None:
    try:
        messages = response.json().get("messages", [])
    except (ValueError, AttributeError):
        return None
    for m in messages:
        if m.get("from") == did and str(m.get("nonce")) == str(nonce):
            return m
    return None


async def find_in_room(client: TechnocoreClient, did: str, nonce: int | None,
                       trade_id: str | None = None) -> dict | None:
    def match(messages: list[dict]) -> dict | None:
        for m in reversed(messages):
            if m.get("from") != did:
                continue
            if nonce is not None and str(m.get("nonce")) == str(nonce):
                return m
            if trade_id is not None and f'"id":"{trade_id}"' in m.get("text", ""):
                return m
        return None

    # the newest messages first (one cheap read); the whole ring only if that misses
    return (match(await client.get_room(config.OWN_ROOM, limit=100))
            or match(await client.export_room(config.OWN_ROOM)))


async def run_trader(args: list[str]) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    command = args[0] if args else "status"
    live = "--live" in args
    if command == "run":
        await Trader(live=live).run()
    elif command == "register":
        await Trader(live=True).register(post="--post" in args)
    elif command == "status":
        Trader(live=live, need_signer=False).status()
    else:
        raise SystemExit("usage: flopstar trader [run|status] [--live] | register [--post]")
