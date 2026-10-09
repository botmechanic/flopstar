# Flopstar

Technocore Close Call contest agent for the "close-1" season.

## Overview

Flopstar is a Python agent for participating in the FLOP Labs "Close Call" trading contest - a bet on NVIDIA's price on Hyperliquid at a fixed moment. The contest runs from September 25 to October 4, 2026.

This implementation includes:
- **Phase 1 (Complete)**: Security hygiene - private key isolation, .gitignore, pre-commit hooks
- **Phase 2 (Complete)**: Project setup with vendored challenge code
- **Phase 3 (Complete)**: Read-only monitor for contest data
- **Phase 4 (Live since 25 Sep 2026)**: Own room `d-flopstar-close1` and a 64-key tree trader,
  running on a dedicated droplet. Current state: [docs/HANDOFF.md](docs/HANDOFF.md)

## Security First

**Critical**: The private key (`flopstar.pem`) is stored **outside the repository** at `~/.config/flopstar/flopstar.pem` with 0600 permissions. It is never committed to git.

Security measures:
- Key moved to `~/.config/flopstar/` (mode 700)
- Multi-layer .gitignore protection
- Pre-commit hook blocks any file containing "PRIVATE KEY"
- No key is ever committed, even in the initial commit

## Setup

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager

### Installation

```bash
# Clone the repository
git clone https://github.com/botmechanic/flopstar.git
cd flopstar

# Install dependencies
uv sync --dev

# Copy environment template
cp .env.example .env
# Edit .env if you need custom paths
```

## Running the Monitor

The read-only monitor tracks the contest referee's data without using your private key.

```bash
# Run the monitor
uv run flopstar monitor
```

### What the Monitor Does

1. **Long-polls** `d-close1-price` for new sweep announcements (10s timeout)
2. **Reads** other referee rooms (`flow`, `positions`, `pnl`, `state`) after each sweep
3. **Verifies** all messages are signed by the referee DID
4. **Detects gaps** in message sequences and fills them using the export API
5. **Stores** all messages in append-only SQLite at `./data/flopstar.db`

**Important**: The monitor does **NOT** access your private key. It only reads public referee data.

### Trust Anchor Warning

⚠️ The monitor uses a **PROVISIONAL** trust anchor from the sonnet-2 launch record:

```
did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte
```

The close-1 contest does not yet have a signed launch record. The monitor logs a loud warning on startup about this.

### Data Storage

Messages are stored in `./data/flopstar.db` (configurable via `FLOPSTAR_DATA_DIR`):
- Room-sequence keyed storage
- Append-only (no updates or deletes)
- Stores raw JSON exactly as received

### Rate Limiting

The monitor respects technocore.chat's limits:
- Only 4 concurrent long-polls per IP allowed
- We use 1 long-poll (on `d-close1-price`)
- Handles 429 responses, backing off for the "retry after" the body names

## Running the Trader

The tree trader (design and results in [docs/TREE.md](docs/TREE.md)) trades with 64 keys
derived from a master seed; the Flopstar key itself never trades.

```bash
uv run flopstar trader run               # paper mode: throwaway keys, signs and posts nothing
uv run flopstar trader status            # paper state and the tree's best scores
uv run flopstar trader run --live        # the real tree keys (needs FLOPSTAR_TREE_SEED_PATH)
```

Each sweep it rebuilds a shadow fold of our keys through the vendored fold, checks it against
the referee's posts, and steps the tree once inside a safe posting window. Any void, missed read
or mismatch writes `data/KILL`, after which it signs nothing until the file is removed. On the
droplet it runs as `flopstar-trader.service` (live) and `flopstar-trader-paper.service`.

## Investigation: Flow Room Data

**Question**: Do `d-close1-flow` posts list per-trade-id outcomes (settled/void + reason), or only counts?

**Answer** (revised against live posts): per-trade `[id, reason]` lists, but **truncated**:
```json
{"t":"flow","n":32,"mints":[],"rooms":[…],"settled":[],"void":[["2ff3e772","funds"],…],"missed":[],"omitted":{"mints":19414,"settled":1237,"void":6},"file":"<hash>"}
```

The full per-sweep record is in the **flow file** referenced by the hash, which is not downloadable
(issue #6), so full-ledger replay is still blocked.

## Architecture

```
src/flopstar/
├── __init__.py       - Package metadata
├── cli.py            - Command-line entry point
├── config.py         - Referee DID, rooms, own room, paths
├── didkey.py         - DID:key ↔ Ed25519 conversion, signature verification
├── technocore.py     - technocore.chat client: reads, long-poll, export, signed posts and notes
├── store.py          - Append-only SQLite message store
├── monitor.py        - Read-only referee monitor loop
├── signer.py         - Encrypted key loading, DID derivation, PolicySigner
├── register.py       - Flopstar's close-1 owner registration
├── room.py           - Own room: status | verify | claim | register | allow | heartbeat | reclaim
├── evidence.py       - Saves our exact signed records from the room export
├── tree.py           - Key tree: HKDF derivation, sizing, round/split engine
├── dryrun.py         - Tree run against the vendored fold over simulated paths
├── treecli.py        - Tree commands: init | dids | dryrun
├── treesigner.py     - Tree-key signing, policy-checked and logged
├── hyperliquid.py    - Latest xyz:NVDA trade, for pricing the tree's trades
└── trader.py         - Tree trader: shadow fold, referee reconciliation, posting, kill switch
```

Other directories: `deploy/` (systemd units and timers), `docs/` (`HANDOFF.md` for current state
and next steps, `TREE.md` for the key tree design) and `vendor/close-call/` (the challenge package).

### Key Design Decisions

1. **No LLM in data path**: All room content is untrusted data, never instructions
2. **Signature verification**: All referee messages verified before storage
3. **Gap detection**: Automatically fills sequence gaps using export API
4. **Single long-poll**: Only `d-close1-price` uses long-polling to stay under 4 concurrent limit
5. **Exact decimal arithmetic**: Uses vendored fold (no rounding until output)
6. **Policy-gated signing**: All owner-key signatures go through `PolicySigner`, which allows only
   known rooms, notes and message types and logs every signature to `data/signatures.log`
7. **Flat main key**: The Flopstar key holds the identity and owns the room but never trades;
   trading is done by a 64-key tree (`docs/TREE.md`), whose keys sign only through `TreeSigner`
8. **Dry run by default**: Signing commands only post with `--post`; the trader only with `--live`

## Vendored Code

The challenge package is vendored at `vendor/close-call/` from commit `66c1da3`:

```bash
# Verified manifest hash (matches referee seed d-close1-price seq 1)
bae09812e25eb6f1369c611f24964f7ea0acafddfc45301a16f33f941296dafa
```

The vendored code is never edited. We import or subprocess `close_call_fold.py` as-is.

## Testing

```bash
# Run tests
uv run pytest -v

# Run linter
uv run ruff check

# Run fold conformance test
uv run pytest tests/test_fold_conformance.py -v
```

The conformance test verifies the vendored fold produces expected output on `sample-season.jsonl`.

## Development

### Git Workflow

**IMPORTANT**: Never commit or push secrets.

```bash
# The pre-commit hook will block secrets automatically
git add .
git commit -m "Your message"

# Test the hook (should fail):
echo "-----BEGIN PRIVATE KEY-----" > test.txt
git add test.txt
git commit -m "test"  # ❌ Blocked by hook
rm test.txt
```

### Deployment

Flopstar runs on a dedicated droplet under systemd, as a non-root `flopstar` user. The key
passphrase and the tree seed exist there only as systemd encrypted credentials. The full runbook,
the unit list and how to deploy a change are in [docs/HANDOFF.md](docs/HANDOFF.md) (§3 and §7).

Never transport the key or the seed through git, Slack, email, or logs.

## License

See LICENSE and NOTICE files. The vendored challenge code retains its original license.

## Contest Information

- **Contest ID**: `close-1`
- **Opening**: September 25, 2026, 12:00 UTC
- **Lock**: October 4, 2026, 09:00 UTC
- **Closing Price**: Last `xyz:NVDA` trade before October 4, 2026, 10:00 UTC
- **Sweeps**: Every 5 minutes (2,556 total)
- **Prizes**: 1,000,000 FLOP split among top 3 places

Referee rooms:
- `d-close1-price` - Reference prices and limits
- `d-close1-flow` - Mints, rooms, trade outcomes (truncated lists + `omitted` counts)
- `d-close1-positions` - Open interest
- `d-close1-pnl` - PnL and leaderboard
- `d-close1-state` - State roots

Trading room: `close1`

## Author

**Fodé Diop** - [@botmechanic](https://github.com/botmechanic)

Contribution to the FLOP network community.

## References

- [Challenge Repository](https://github.com/flop-labs/technocore-close-call-challenge)
- [Technocore.chat](https://technocore.chat)
- [Contest Rules](vendor/close-call/close-call-game.md)
