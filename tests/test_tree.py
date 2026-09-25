"""Key tree: derivation, sizing, and the engine's round mechanics."""

from decimal import Decimal

from flopstar.signer import did_from_private_key
from flopstar.tree import Tree, all_in_qty, derive_key, tree_dids

SEED = bytes(range(32))


def test_derivation_is_deterministic_and_distinct():
    dids = tree_dids(SEED, 8)
    assert dids == tree_dids(SEED, 8)
    assert len(set(dids)) == 8
    assert did_from_private_key(derive_key(SEED, 3)) == dids[3]
    assert tree_dids(bytes(32), 1) != dids[:1]


def test_all_in_qty_leaves_buffer_for_fee():
    q = all_in_qty(Decimal(10000), Decimal("225.00"))
    assert q == Decimal("43.12")
    assert q * Decimal("225.00") * Decimal("1.01") <= Decimal(10000)
    assert all_in_qty(Decimal(-5), Decimal(225)) == 0


def test_round_zero_pairs_then_split_flips_half_the_winners():
    dids = tree_dids(SEED, 8)
    tree = Tree(dids)
    cash = {k: Decimal(10000) for k in dids}
    intents = tree.step(1, Decimal(200), cash, {k: Decimal(0) for k in dids})
    assert [(i.maker, i.taker, i.side) for i in intents] == [
        (dids[j], dids[j + 1], "buy") for j in range(0, 8, 2)]
    # evens long, odds short; price up 3% -> evens win, odds become their mirrors
    position = {k: Decimal(48) if i % 2 == 0 else Decimal(-48) for i, k in enumerate(dids)}
    assert tree.step(2, Decimal(205), cash, position) == []      # +2.5%: no round
    closes = tree.step(3, Decimal(206), cash, position)
    assert tree.live == dids[0::2]
    assert [(i.maker, i.taker, i.side, i.purpose) for i in closes] == [
        (dids[2], dids[3], "sell", "close"), (dids[6], dids[7], "sell", "close")]
    reopens = tree.step(4, Decimal(206), cash, position)
    assert [(i.maker, i.taker, i.side, i.purpose) for i in reopens] == [
        (dids[2], dids[3], "sell", "reopen"), (dids[6], dids[7], "sell", "reopen")]
