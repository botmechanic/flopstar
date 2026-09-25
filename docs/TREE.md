# Close-1 key tree: design and dry run

Status: **design and dry run only.** No tree key is registered, allow-listed or trading.
Review this before any of that happens.

## Why a tree

Close-1 leaves almost no edge in trading technique. Fees are 1% per side, and the clawback takes
back any better-than-close price. So a key's score is about its position times NVDA's move from
entry to *S*. A single key has to guess the direction. The close-1 rules allow one operator to
run many keys ("One operator may run many keys and hold several places"), so we run 64. Between
them they hold no net position, and we arrange them so that one key ends up on the right side of
every 3% leg of the path. That makes it a lookback on NVDA's path, not a bet on its end point.

Flopstar's main key stays **flat**. It owns `d-flopstar-close1`, registers it, and after the lock
signs a statement listing every tree DID. It never trades.

## Mechanics

- **Keys.** Key *i* = Ed25519(HKDF-SHA256(master seed, info=`flopstar/close-1/tree/v1/<i>`)).
  The master seed is 32 random bytes in `~/.config/flopstar/close1-master.seed` (0600). It is not
  derived from `flopstar.pem`. Losing it means losing the tree; leaking it means leaking all 64
  keys. The public DIDs are in `data/tree-dids.txt`.
- **Round 0.** 32 pairs (2j, 2j+1) trade with each other in one sweep: 2j goes long all in and
  2j+1 goes short all in.
- **All in.** qty = floor₀.₀₁(0.98 × cash / (px × 1.01)). That is about 43 contracts at 225.
- **New round.** A round starts when the reference moves ≥ 3% from the round's opening price.
  Live keys on the right side win; the rest are dead. After round 0, each dead key is the
  **mirror** of its pair's winner: it holds the opposite position.
- **Split** (5 of them: 32 → 16 → 8 → 4 → 2 → 1 live). Live winners pair up as (stayer, flipper).
  The stayer does nothing. The flipper trades twice with its own mirror:
  1. **close** in sweep *t*, which flattens both;
  2. **reopen** the opposite way in sweep *t+1*, sized all in for the smaller of the two cash
     balances.

  The reopen is sent only after the close has settled in our local fold. The fold checks funds
  against settled balances, and "closing contracts in the same trade does not fund opening
  others", so the close and the reopen can't be one trade.
- **The tree is always net flat.** Every trade is between two tree keys, so the losers' losses
  plus fees pay for the best key's score. Losers can't "stop": their positions are the other side
  of the winners'. Dead pairs hold canceling positions until *S* at no further cost.
- **Pricing.** Each trade is priced at Hyperliquid's latest `xyz:NVDA` trade when posted, so the
  fee stays at the flat 1% unless the price moves > 1% before the sweep closes. It must also fall
  within ±5% of the previous sweep's reference. `until` = the next sweep + 1.
- **Posting.** The maker posts each trade in `d-flopstar-close1`, and the taker is always named.
  Having only a party post its trade is **our policy choice**. The confirmed rule is only that
  the poster must be a registered key. Trade ids are random, because an id settles once across
  the whole contest.

## Dry run

`uv run flopstar tree dryrun 200`. This uses a throwaway seed, never signs or posts, and runs
every trade through the vendored `close_call_fold.Fold` unmodified. The simulated sweeps run
from 37 to the lock at 2556, starting at 224.15. Trade prices get a little noise before each
sweep closes. The baseline "single all-in key" is the better of an all-in long and an all-in
short, i.e. a **perfect** direction call made in hindsight.

| Scenario | S | Tree best | Tree 2nd | Perfect single call | Rounds | Voids |
|---|---|---|---|---|---|---|
| flat (±1% wiggle) | 224.08 | −87 | −89 | −94 | 0 | 0 |
| trend up +12% | 251.05 | +1,070 | +1,068 | +1,067 | 3 | 0 |
| trend down −12% | 197.25 | +1,065 | +1,064 | +1,067 | 4 | 0 |
| zigzag 4× ±4% | 223.43 | +477 | +460 | −66 | 4 | 0 |
| up 10%, back 6% | 231.77 | +1,081 | +1,070 | +233 | 4 | 0 |

Monte Carlo, 200 GBM paths at 45% annual volatility:

| | p10 | median | p90 | mean |
|---|---|---|---|---|
| tree best key | +426 | +902 | +1,358 | +899 |
| perfect single call | −30 | +363 | +991 | +433 |

- **Against the perfect call:** the tree's best key beat a perfect single direction call on
  193 of 200 paths.
- **Voids:** none across all paths, so the 2% buffer covered every fee and funds check.
- **Fold checks:** the zero-sum check held in every scenario.
- **Cost:** the tree burns a median of about 17,500 POLF in fees out of its 640,000. That is
  paid by dead keys and doesn't touch the best key's score beyond its own fees.

Parameter sensitivity (100 paths each, same volatility):

| Keys | Step | Median best | Beats perfect call |
|---|---|---|---|
| 64 | 2% | +735 | 90/100 |
| **64** | **3%** | **+872** | **99/100** |
| 64 | 4% | +815 | 90/100 |
| 64 | 5% | +778 | 85/100 |
| 256 | 2% | +800 | 94/100 |
| 256 | 3% | +929 | 98/100 |

3% with 64 keys comes out best among the 64-key settings. 256 keys add about 6% to the median,
for four times the keys.

## What the dry run does not model

- **Real price behavior.** NVDA gaps, weekends (26–27 Sep and 3–4 Oct), and a thin
  `xyz:NVDA` market. At sweep 32 the reference was 50 minutes old. Fewer real moves means fewer
  rounds.
- **The competition.** 352,876 keys were registered at sweep 32. Farms can run deeper trees
  than ours. The tree maximizes our best key's score, but it doesn't guarantee a top-3 place.
- **Delivery.** The dry run assumes the referee receives and applies every trade we post.
  Flow posts are truncated (`omitted`), so live confirmation has to come from a local shadow
  fold of our 64 keys, cross-checked against the pnl and positions top lists.

## Still to build before going live

1. **Hyperliquid price reader** for pricing trades (info endpoint, `xyz:NVDA` trades). Round
   triggers use the referee's verified reference.
2. **Shadow fold.** Replay our own keys' trades through the vendored fold every sweep; that
   replay is our ground truth for cash and positions. Stop and alert on any mismatch with
   referee posts.
3. **Tree signer policy.** Tree keys sign only in `d-flopstar-close1` (plus `close1` if ever
   needed), with message types `owner` and `trade`, and every signature logged.
4. **Registration.**
   - Wait for the referee to list `d-flopstar-close1` in a flow post.
   - Flopstar writes the `room-allow` note with all 64 DIDs (about 3.7k of the 8,192 characters).
   - Each tree key posts its own `owner` message in the room. That's 64 writes against a limit
     of 300 a minute.
   - Round 0 goes the sweep after all 64 mints, with all 32 pairs in the same sweep.
5. **Kill switch and alerts.** Stop on any void, signature failure or funds shortfall.
6. **After the lock.** Flopstar signs a statement listing all 64 tree DIDs, published in its
   room and in this repo.
