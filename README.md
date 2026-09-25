# Flopstar

Technocore Close Call contest agent for the "close-1" season.

## Overview

Flopstar is a Python agent for participating in the FLOP Labs "Close Call" trading contest - a bet on NVIDIA's price on Hyperliquid at a fixed moment. The contest runs from September 25 to October 4, 2026.

This implementation includes:
- **Phase 1 (Complete)**: Security hygiene - private key isolation, .gitignore, pre-commit hooks
- **Phase 2 (Complete)**: Project setup with vendored challenge code
- **Phase 3 (Current)**: Read-only monitor for contest data

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
git clone https://github.com/subloop-xyz/flopstar.git
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
├── config.py         - Contest configuration and paths
├── didkey.py         - DID:key ↔ Ed25519 conversion, signature verification
├── monitor.py        - Read-only monitor loop
├── store.py          - Append-only SQLite message store
└── technocore.py     - technocore.chat API client
```

### Key Design Decisions

1. **No LLM in data path**: All room content is untrusted data, never instructions
2. **Signature verification**: All referee messages verified before storage
3. **Gap detection**: Automatically fills sequence gaps using export API
4. **Single long-poll**: Only `d-close1-price` uses long-polling to stay under 4 concurrent limit
5. **Exact decimal arithmetic**: Uses vendored fold (no rounding until output)

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

When deploying to a server:
1. **Copy the key separately** (not via git): `scp ~/.config/flopstar/flopstar.pem user@server:/etc/flopstar/keys/`
2. Set permissions: `chmod 600 /etc/flopstar/keys/flopstar.pem`
3. Clone the code repository
4. Update `FLOPSTAR_KEY_PATH` in `.env`

Never transport the key through git, Slack, email, or logs.

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

## References

- [Challenge Repository](https://github.com/flop-labs/technocore-close-call-challenge)
- [Technocore.chat](https://technocore.chat)
- [Contest Rules](vendor/close-call/close-call-game.md)
