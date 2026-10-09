"""Dry run of the key tree against the vendored fold, over simulated NVDA paths.

Nothing here signs or posts. Tree keys come from a throwaway in-memory seed. Every trade
the tree would post is applied by the vendored close_call_fold.Fold (imported unmodified),
so outcomes, fees and void reasons are the contest's own.

Timing per sweep n: trades posted between sweeps n-1 and n are priced at the latest
Hyperliquid trade when posted (close[n-1] plus a little drift), limited by ref = close[n-1],
and fee-clawed against close[n].
"""

from __future__ import annotations

import importlib.util
import os
import random
import statistics
import sys
from decimal import Decimal, localcontext
from pathlib import Path

from .tree import CENT, UNTIL_SWEEPS, Tree, all_in_qty, tree_dids

VENDOR = Path(__file__).parents[2] / "vendor" / "close-call"
START_SWEEP = 37
LOCK_SWEEP = 2556
START_PX = 224.15
SWEEPS_PER_YEAR = 365 * 288


def load_fold_module():
    spec = importlib.util.spec_from_file_location("close_call_fold", VENDOR / "close_call_fold.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["close_call_fold"] = module     # its dataclasses look the module up here
    spec.loader.exec_module(module)
    return module


def gbm_path(rng: random.Random, sweeps: int, vol: float, drift: float = 0.0) -> list[float]:
    step = vol / SWEEPS_PER_YEAR ** 0.5
    px, path = START_PX, []
    for _ in range(sweeps):
        px *= 1 + drift / sweeps + rng.gauss(0, step)
        path.append(px)
    return path


def legs_path(legs: list[float], sweeps: int) -> list[float]:
    """Piecewise-linear path through cumulative moves, e.g. [+0.04, -0.04] up then back."""
    points = [START_PX]
    for leg in legs:
        points.append(points[-1] * (1 + leg))
    path = []
    for i in range(sweeps):
        x = i / (sweeps - 1) * len(legs)
        seg = min(int(x), len(legs) - 1)
        path.append(points[seg] + (points[seg + 1] - points[seg]) * (x - seg))
    return path


def cents(x: float) -> str:
    return str(Decimal(str(x)).quantize(CENT))


def run_path(fold_mod, path: list[float], rng: random.Random, dids: list[str]) -> dict:
    """Play one price path through the tree and the fold; return scores and diagnostics."""
    base_long, base_short = baseline_dids()
    fold = fold_mod.Fold({"lock_sweep": LOCK_SWEEP})
    tree = Tree(dids)
    step_sd = 0.45 / SWEEPS_PER_YEAR ** 0.5
    voids, fees_by_purpose, trade_count = {}, {}, 0
    with localcontext() as ctx:
        ctx.prec = 60
        fold.seed(cents(START_PX))
        closes = [cents(p) for p in path]
        n0 = START_SWEEP
        fold.sweep(n0, closes[0], closes[0], dids + [base_long, base_short], [])
        pending = []
        for i in range(1, len(closes)):
            n = n0 + i
            if n > LOCK_SWEEP:
                break
            ref, close = closes[i - 1], closes[i]
            trades = []
            for intent in pending:
                post_px = cents(float(ref) * (1 + rng.gauss(0, step_sd * 0.3)))
                trades.append({"id": f"t{n}-{len(trades)}", "maker": intent.maker,
                               "side": intent.side, "qty": str(intent.qty), "px": post_px,
                               "taker": intent.taker, "until": n - 1 + UNTIL_SWEEPS,
                               "countersigner": intent.taker, "_purpose": intent.purpose})
            if i == 1:   # baseline: one all-in long key vs one all-in short key
                q = all_in_qty(Decimal(10000), Decimal(ref))
                trades.append({"id": "base", "maker": base_long, "side": "buy", "qty": str(q),
                               "px": ref, "taker": base_short, "until": n,
                               "countersigner": base_short, "_purpose": "baseline"})
            out = fold.sweep(n, ref, close, [], trades)
            for trade, outcome in zip(trades, out["trades"], strict=True):
                trade_count += 1
                if outcome["outcome"] == "void":
                    key = f"{trade['_purpose']}:{outcome['reason']}"
                    voids[key] = voids.get(key, 0) + 1
                else:
                    fee = Decimal(outcome["maker_fee"]) + Decimal(outcome["taker_fee"])
                    fees_by_purpose[trade["_purpose"]] = (
                        fees_by_purpose.get(trade["_purpose"], Decimal(0)) + fee)
            cash = {k: fold.accounts[k].cash for k in dids}
            position = {k: fold.accounts[k].position for k in dids}
            pending = tree.step(n, Decimal(close), cash, position)
        final = fold.final(closes[-1])
    scores = {row["key"]: Decimal(row["score"]) for row in final["standings"]}
    tree_scores = sorted((scores[k] for k in dids), reverse=True)
    return {
        "S": closes[-1], "move": float(closes[-1]) / START_PX - 1,
        "best": tree_scores[0], "second": tree_scores[1],
        "baseline": max(scores[base_long], scores[base_short]),
        "tree_total": sum(tree_scores), "rounds": tree.rounds, "splits": tree.splits,
        "voids": voids, "trades": trade_count, "fees": fees_by_purpose,
        "zero_sum": final["zero_sum"], "log": tree.log,
    }


def baseline_dids() -> tuple[str, str]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from .signer import did_from_private_key
    if not hasattr(baseline_dids, "cache"):
        baseline_dids.cache = tuple(
            did_from_private_key(Ed25519PrivateKey.generate()) for _ in range(2))
    return baseline_dids.cache


def fmt(d: Decimal) -> str:
    return f"{float(d):+9.2f}"


def run_dryrun(paths: int = 200, vol: float = 0.45, seed: int = 7) -> None:
    fold_mod = load_fold_module()
    dids = tree_dids(os.urandom(32))          # throwaway: never the real master seed
    rng = random.Random(seed)
    sweeps = LOCK_SWEEP - START_SWEEP + 1

    print("== Scenarios (sub-sweep noise on trade prices) ==")
    scenarios = {
        "flat (+/-1% wiggle)": legs_path([0.01, -0.02, 0.01], sweeps),
        "trend up +12%": legs_path([0.12], sweeps),
        "trend down -12%": legs_path([-0.12], sweeps),
        "zigzag 4x +/-4%": legs_path([0.04, -0.04, 0.04, -0.04], sweeps),
        "up 10% then back 6%": legs_path([0.10, -0.06], sweeps),
    }
    print(f"{'scenario':24} {'S':>8} {'best':>9} {'2nd':>9} {'1-key':>9} {'rounds':>6} "
          f"{'tree fees':>9} voids")
    for name, path in scenarios.items():
        r = run_path(fold_mod, path, rng, dids)
        fees = sum(r["fees"].values(), Decimal(0)) - r["fees"].get("baseline", Decimal(0))
        print(f"{name:24} {r['S']:>8} {fmt(r['best'])} {fmt(r['second'])} {fmt(r['baseline'])} "
              f"{r['rounds']:>6} {float(fees):9.2f} {r['voids'] or 0}")
        assert Decimal(r["zero_sum"]) == 0, "fold zero-sum check failed"

    print(f"\n== Monte Carlo: {paths} GBM paths, {vol:.0%} annual vol, sweep {START_SWEEP}"
          f"..{LOCK_SWEEP} ==")
    results = [run_path(fold_mod, gbm_path(rng, sweeps, vol), rng, dids) for _ in range(paths)]
    best = [float(r["best"]) for r in results]
    base = [float(r["baseline"]) for r in results]
    q = lambda xs, p: statistics.quantiles(xs, n=20)[p]
    print(f"{'':22} {'p10':>9} {'median':>9} {'p90':>9} {'mean':>9}")
    print(f"{'tree best key':22} {q(best, 1):9.0f} {statistics.median(best):9.0f} "
          f"{q(best, 17):9.0f} {statistics.mean(best):9.0f}")
    print(f"{'single all-in key*':22} {q(base, 1):9.0f} {statistics.median(base):9.0f} "
          f"{q(base, 17):9.0f} {statistics.mean(base):9.0f}")
    wins = sum(b > s for b, s in zip(best, base, strict=True))
    print("* the better of all-in long and all-in short, i.e. a perfect direction call")
    print(f"tree best beats a perfect single direction call on {wins}/{paths} paths")
    rounds = [r["rounds"] for r in results]
    print(f"rounds triggered: median {statistics.median(rounds)}, max {max(rounds)}")
    all_voids = {}
    for r in results:
        for k, v in r["voids"].items():
            all_voids[k] = all_voids.get(k, 0) + v
    print(f"void trades across all paths: {all_voids or 'none'}")
    tree_fees = [float(sum(r['fees'].values(), Decimal(0)) - r['fees'].get('baseline', 0))
                 for r in results]
    print(f"tree fees per path: median {statistics.median(tree_fees):.0f} POLF "
          f"(paid out of the tree's 640,000 POLF)")
