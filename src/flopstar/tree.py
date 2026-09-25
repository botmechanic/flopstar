"""The close-1 key tree: 64 keys that together hold no net position, so one of them ends up
riding every 3% leg of NVDA's path. docs/TREE.md explains the plan; this module is the pure
engine, driven the same way by the dry run and (later) the live agent.

Keys come from a master seed, never from the Flopstar key: key i is
Ed25519(HKDF-SHA256(seed, info="flopstar/close-1/tree/v1/<i>")).

Every trade is between two tree keys. So each live key's position is mirrored by a dead key's,
and a flip is two trades with that mirror: close (sweep t), then reopen the other way (sweep t+1,
after the close has settled in our local fold, so funds are never checked against an unsettled
close).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .signer import did_from_private_key

TREE_SIZE = 64
SPLITS = 5                      # 32 -> 16 -> 8 -> 4 -> 2 -> 1 live keys; with round 0, 6 rounds
ROUND_MOVE = Decimal("0.03")    # a new round when price moves this far from the round's open
BUFFER = Decimal("0.98")        # size at 98% of the most funds allow
FEE_RATE = Decimal("0.01")
UNTIL_SWEEPS = 2                # a trade may settle in the next sweep or the one after
CENT = Decimal("0.01")
INFO_PREFIX = b"flopstar/close-1/tree/v1/"


def get_seed_path() -> Path:
    return Path(os.environ.get(
        "FLOPSTAR_TREE_SEED_PATH", "~/.config/flopstar/close1-master.seed")).expanduser()


def derive_key(seed: bytes, index: int) -> Ed25519PrivateKey:
    if len(seed) != 32:
        raise ValueError("master seed must be 32 bytes")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=INFO_PREFIX + str(index).encode())
    return Ed25519PrivateKey.from_private_bytes(hkdf.derive(seed))


def load_seed(path: Path | None = None) -> bytes:
    return bytes.fromhex((path or get_seed_path()).read_text().strip())


def tree_dids(seed: bytes, size: int = TREE_SIZE) -> list[str]:
    return [did_from_private_key(derive_key(seed, i)) for i in range(size)]


def all_in_qty(cash: Decimal, px: Decimal) -> Decimal:
    """98% of the most contracts cash can open at px, paying the 1% fee."""
    if cash <= 0:
        return Decimal(0)
    return (BUFFER * cash / (px * (1 + FEE_RATE))).quantize(CENT, rounding=ROUND_DOWN)


@dataclass
class Intent:
    """One trade to sign: maker (on `side`) and taker are tree keys; the maker posts it."""
    maker: str
    taker: str
    side: str          # maker's side: "buy" or "sell"
    qty: Decimal
    purpose: str       # "open-0", "close", "reopen"


@dataclass
class Tree:
    dids: list[str]
    live: list[str] = field(default_factory=list)
    mirror: dict[str, str] = field(default_factory=dict)
    round_open: Decimal | None = None
    splits: int = 0
    rounds: int = 0
    pending: list[tuple[str, str]] = field(default_factory=list)   # (flipper, new side)
    log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"dids": self.dids, "live": self.live, "mirror": self.mirror,
                "round_open": None if self.round_open is None else str(self.round_open),
                "splits": self.splits, "rounds": self.rounds,
                "pending": [list(p) for p in self.pending], "log": self.log}

    @classmethod
    def from_dict(cls, d: dict) -> Tree:
        return cls(dids=d["dids"], live=d["live"], mirror=d["mirror"],
                   round_open=None if d["round_open"] is None else Decimal(d["round_open"]),
                   splits=d["splits"], rounds=d["rounds"],
                   pending=[tuple(p) for p in d["pending"]], log=d["log"])

    def step(self, n: int, px: Decimal, cash: dict, position: dict) -> list[Intent]:
        """Called once per sweep n with the reference price and our keys' settled state;
        returns the trades to post for sweep n+1."""
        if self.round_open is None:
            return self._round_zero(px, cash)
        if self.pending:
            return self._reopen(px, cash)
        if len(self.live) < 2 or abs(px / self.round_open - 1) < ROUND_MOVE:
            return []
        return self._new_round(n, px, cash, position)

    def _round_zero(self, px: Decimal, cash: dict) -> list[Intent]:
        intents = []
        for a, b in zip(self.dids[0::2], self.dids[1::2], strict=True):
            qty = min(all_in_qty(cash[a], px), all_in_qty(cash[b], px))
            intents.append(Intent(a, b, "buy", qty, "open-0"))     # a long, b short
        self.live = list(self.dids)
        self.round_open = px
        self.log.append(f"round 0 at {px}: {len(intents)} pairs")
        return intents

    def _new_round(self, n: int, px: Decimal, cash: dict, position: dict) -> list[Intent]:
        up = px > self.round_open
        winners = [k for k in self.live if (position[k] > 0) == up and position[k] != 0]
        if self.rounds == 0:
            # round-0 pairs mirror each other: the loser of each pair is the winner's mirror
            for a, b in zip(self.dids[0::2], self.dids[1::2], strict=True):
                win, lose = (a, b) if a in winners else (b, a)
                self.mirror[win] = lose
        self.rounds += 1
        self.log.append(f"sweep {n}: {'up' if up else 'down'} {px / self.round_open - 1:+.2%} "
                        f"from {self.round_open} -> {len(winners)} of {len(self.live)} live win")
        self.live = winners
        self.round_open = px
        if self.splits >= SPLITS or len(self.live) < 2:
            return []
        self.splits += 1
        intents = []
        for flipper in self.live[1::2]:     # pairs (stayer, flipper); stayers keep position
            held = position[flipper]
            side = "sell" if held > 0 else "buy"
            intents.append(Intent(flipper, self.mirror[flipper], side, abs(held), "close"))
            self.pending.append((flipper, side))
        return intents

    def _reopen(self, px: Decimal, cash: dict) -> list[Intent]:
        intents = []
        for flipper, side in self.pending:
            mirror = self.mirror[flipper]
            qty = min(all_in_qty(cash[flipper], px), all_in_qty(cash[mirror], px))
            intents.append(Intent(flipper, mirror, side, qty, "reopen"))
        self.pending = []
        return intents
