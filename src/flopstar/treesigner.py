"""The only way code should sign with a tree key. Enforces the tree signer policy (docs/TREE.md):

- room: d-flopstar-close1 only;
- messages: `owner` naming the signing key itself, and `trade` whose maker and taker are two
  different tree keys, with the taker named (never "any");
- the inner `terms` and `accept` signatures only for such tree-internal terms.

Every signature is logged to data/tree-signatures.log. Key material is never printed or logged.
"""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

from . import config
from .signer import PolicyViolation, compact
from .tree import TREE_SIZE, derive_key, tree_dids

TERMS_KEYS = {"id", "maker", "px", "qty", "side", "taker", "until"}


def b64(sig: bytes) -> str:
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def terms_payload(terms: dict) -> str:
    return f"{config.SEASON_ID}|terms|{compact(terms)}"


def accept_payload(terms: dict, taker: str) -> str:
    return f"{config.SEASON_ID}|accept|{compact(terms)}|{taker}"


class TreeSigner:
    def __init__(self, seed: bytes, size: int = TREE_SIZE, log_path: Path | None = None):
        self.dids = tree_dids(seed, size)
        self._keys = {did: derive_key(seed, i) for i, did in enumerate(self.dids)}
        self.room = config.OWN_ROOM
        self.log_path = log_path or config.get_data_dir() / "tree-signatures.log"

    def _check_terms(self, terms: dict) -> None:
        if set(terms) != TERMS_KEYS:
            raise PolicyViolation(f"terms keys {sorted(terms)}")
        maker, taker = terms["maker"], terms["taker"]
        if maker not in self._keys or taker not in self._keys or maker == taker:
            raise PolicyViolation("terms must be between two different tree keys")

    def trade_text(self, terms: dict) -> str:
        """The signed `trade` message text: maker signs the terms, the named taker accepts."""
        self._check_terms(terms)
        maker, taker = terms["maker"], terms["taker"]
        maker_sig = b64(self._keys[maker].sign(terms_payload(terms).encode()))
        taker_sig = b64(self._keys[taker].sign(accept_payload(terms, taker).encode()))
        self._log({"kind": "terms", "did": maker, "terms": terms, "sig": maker_sig})
        self._log({"kind": "accept", "did": taker, "terms": terms, "sig": taker_sig})
        return compact({"t": "trade", "season": config.SEASON_ID, "terms": terms,
                        "taker": taker, "maker_sig": maker_sig, "taker_sig": taker_sig})

    def sign_message(self, did: str, room: str, nonce: int, text: str) -> str:
        if did not in self._keys:
            raise PolicyViolation("not a tree key")
        if room != self.room:
            raise PolicyViolation(f"room {room!r} is not allowed")
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            raise PolicyViolation("text is not JSON") from None
        if not isinstance(msg, dict) or msg.get("season") != config.SEASON_ID:
            raise PolicyViolation("not a close-1 message")
        if msg.get("t") == "owner":
            if msg.get("key") != did:
                raise PolicyViolation("an owner message must name its own key")
        elif msg.get("t") == "trade":
            self._check_terms(msg.get("terms") or {})
            if did not in (msg["terms"]["maker"], msg["terms"]["taker"]):
                raise PolicyViolation("only a party to a trade posts it")
        else:
            raise PolicyViolation(f"message type {msg.get('t')!r} is not allowed")
        sig = b64(self._keys[did].sign(f"{room}|{nonce}|{text}".encode()))
        self._log({"kind": "message", "did": did, "room": room, "nonce": str(nonce),
                   "text": text, "sig": sig})
        return sig

    def _log(self, entry: dict) -> None:
        entry = {"at": datetime.now(UTC).isoformat(), **entry}
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
