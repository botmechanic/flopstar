"""Tree signer policy, the sweep clock, and the paper trader against a fake referee store."""

import asyncio
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from flopstar import config, trader
from flopstar.didkey import did_to_public_key
from flopstar.hyperliquid import LastTrade, parse_last_trade
from flopstar.signer import PolicyViolation, compact
from flopstar.store import MessageStore
from flopstar.tree import Tree, tree_dids
from flopstar.treesigner import TreeSigner, accept_payload, b64, terms_payload

SEED = bytes(range(32))


def verify(did, payload, sig):
    import base64
    did_to_public_key(did).verify(base64.urlsafe_b64decode(sig + "=="), payload.encode())


def test_sweep_clock_matches_the_referee():
    # price post n=116 was stamped 21:40:13; sweep n cuts at 12:00 + 5n minutes
    assert trader.sweep_time(116).isoformat() == "2026-09-25T21:40:00+00:00"
    assert trader.sweep_at(trader.parse_ts("2026-09-25T21:38:17.712000Z")) == 116
    assert trader.sweep_at(trader.parse_ts("2026-09-25T21:40:00.000001Z")) == 117


def test_parse_last_trade_takes_the_newest():
    trades = [{"coin": "xyz:NVDA", "px": "225.10", "time": 2, "tid": 1},
              {"coin": "xyz:NVDA", "px": "225.30", "time": 3, "tid": 5},
              {"coin": "xyz:NVDA", "px": "225.20", "time": 3, "tid": 4}]
    assert parse_last_trade(trades).px == Decimal("225.30")
    assert parse_last_trade([]) is None


def test_tree_signer_signs_verifiable_tree_trades_only(tmp_path):
    signer = TreeSigner(SEED, size=4, log_path=tmp_path / "log")
    a, b, c, _ = signer.dids
    terms = {"id": "ab12", "maker": a, "px": "225.00", "qty": "43.12", "side": "buy",
             "taker": b, "until": 120}
    msg = json.loads(signer.trade_text(terms))
    assert msg["t"] == "trade" and msg["terms"] == terms and msg["taker"] == b
    verify(a, terms_payload(terms), msg["maker_sig"])
    verify(b, accept_payload(terms, b), msg["taker_sig"])
    assert terms_payload(terms).startswith('close-1|terms|{"id":"ab12","maker":')

    text = compact(msg)
    signer.sign_message(a, config.OWN_ROOM, 1, text)
    with pytest.raises(PolicyViolation):
        signer.sign_message(a, "close1", 2, text)                       # wrong room
    with pytest.raises(PolicyViolation):
        signer.sign_message(c, config.OWN_ROOM, 3, text)                # not a party
    with pytest.raises(PolicyViolation):
        signer.trade_text({**terms, "taker": "any"})                    # open offer
    outsider = tree_dids(bytes(32), 1)[0]
    with pytest.raises(PolicyViolation):
        signer.trade_text({**terms, "taker": outsider})                 # not our key
    with pytest.raises(PolicyViolation):
        signer.sign_message(a, config.OWN_ROOM, 4, compact(
            {"t": "owner", "season": "close-1", "key": b}))             # someone else's owner msg
    with pytest.raises(PolicyViolation):
        signer.sign_message(a, config.OWN_ROOM, 5, compact(
            {"t": "room", "season": "close-1", "room": "x"}))           # type not allowed
    assert len((tmp_path / "log").read_text().splitlines()) == 3        # terms, accept, message
    assert b64(b"\0" * 64).endswith("AA") and len(b64(b"\0" * 64)) == 86


def test_tree_state_round_trips_through_json():
    dids = tree_dids(SEED, 8)
    tree = Tree(dids)
    cash = {k: Decimal(10000) for k in dids}
    tree.step(1, Decimal(200), cash, {k: Decimal(0) for k in dids})
    position = {k: Decimal(48) if i % 2 == 0 else Decimal(-48) for i, k in enumerate(dids)}
    tree.step(2, Decimal(207), cash, position)
    again = Tree.from_dict(json.loads(json.dumps(tree.to_dict())))
    assert again == tree


def price_post(n, applied, close):
    return {"t": "price", "n": n, "applied": applied, "for": n + 1,
            "ref": {"px": close, "tid": 1, "time": "x"}, "limits": ["0", "0"]}


def flow_post(n, void=()):
    return {"t": "flow", "n": n, "mints": [], "rooms": [], "settled": [],
            "void": [list(v) for v in void], "missed": [], "omitted": {}}


def put(store, room, seq, text):
    store.store_message(room, seq, {"seq": seq, "ts": "x", "from": config.REFEREE_DID,
                                    "nonce": seq, "sig": "x", "text": compact(text)})


@pytest.fixture
def paper(tmp_path, monkeypatch):
    store = MessageStore(tmp_path / "flopstar.db")
    put(store, "d-close1-price", 100, price_post(100, "225.00", "225.00"))
    put(store, "d-close1-flow", 100, flow_post(100))
    t = trader.Trader(live=False, data_dir=tmp_path)
    t.referee.refresh()
    t.state["mints"] = {d: 100 for d in t.dids}
    t.state["sweep"] = 99
    t.process(100)

    async def fake_last_trade(http):
        return LastTrade(Decimal("225.10"), trader.sweep_time(100), 7)

    monkeypatch.setattr(trader, "last_trade", fake_last_trade)
    t.clock = lambda: trader.sweep_time(100) + timedelta(seconds=60)
    return t, store


def test_paper_round_zero_then_referee_sweep_settles_it(paper):
    t, store = paper
    assert t.ready_to_step(t.clock()) == 100
    assert t.ready_to_step(trader.sweep_time(100) + timedelta(seconds=10)) is None   # too early
    assert t.ready_to_step(trader.sweep_time(101) - timedelta(seconds=30)) is None   # too late
    asyncio.run(t.step(100, None, None))
    trades = t.state["trades"]
    assert len(trades) == 32 and {x["sweep"] for x in trades} == {101}
    assert {x["terms"]["px"] for x in trades} == {"225.10"}
    assert {x["terms"]["until"] for x in trades} == {102}
    assert t.ready_to_step(t.clock()) is None                  # stepped once per sweep

    put(store, "d-close1-price", 101, price_post(101, "225.00", "225.20"))
    put(store, "d-close1-flow", 101, flow_post(101))
    t.referee.refresh()
    t.process(101)
    assert t.killed() is None
    assert {x["status"] for x in trades} == {"settled"}
    fold, _ = t.shadow(101)
    cash, pos = t.book(fold)
    assert sum(pos.values()) == 0 and pos[t.dids[0]] == Decimal("43.12")
    assert pos[t.dids[1]] == Decimal("-43.12")
    assert cash[t.dids[0]] == Decimal(10000) - Decimal("43.12") * Decimal("225.10") * Decimal(
        "1.01")


def test_referee_void_of_our_trade_trips_the_kill_switch(paper):
    t, store = paper
    asyncio.run(t.step(100, None, None))
    victim = t.state["trades"][5]["terms"]["id"]
    put(store, "d-close1-price", 101, price_post(101, "225.00", "225.20"))
    put(store, "d-close1-flow", 101, flow_post(101, void=[(victim, "funds")]))
    t.referee.refresh()
    t.process(101)
    assert victim in t.killed() and "funds" in t.killed()


def test_limits_breach_on_hyperliquid_skips_the_step(paper, monkeypatch):
    t, _ = paper

    async def far(http):
        return LastTrade(Decimal("240.00"), trader.sweep_time(100), 8)

    monkeypatch.setattr(trader, "last_trade", far)
    asyncio.run(t.step(100, None, None))
    assert t.state["trades"] == [] and t.state["stepped"] is None and t.killed() is None


def test_tree_allow_list_reads_and_checks_tree_dids(tmp_path, monkeypatch):
    from flopstar.room import NOTE_CHARS, tree_allow_list
    monkeypatch.setenv("FLOPSTAR_DATA_DIR", str(tmp_path))
    dids = tree_dids(SEED)
    (tmp_path / "tree-dids.txt").write_text("".join(f"{i} {d}\n" for i, d in enumerate(dids)))
    assert tree_allow_list() == dids
    assert len(" ".join(dids)) < NOTE_CHARS
    (tmp_path / "tree-dids.txt").write_text(f"0 {dids[0]}\n1 {dids[0]}\n")
    with pytest.raises(SystemExit):
        tree_allow_list()
