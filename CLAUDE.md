# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Flopstar is a Python agent for FLOP Labs' "Close Call" contest (`close-1`, 25 Sep – 4 Oct 2026) on
technocore.chat: agents trade one NVDA future with each other, a referee settles signed trades
every 5 minutes against Hyperliquid's `xyz:NVDA` price. `docs/HANDOFF.md` is the authoritative
current-state document (deployment state, deadlines, secrets inventory, verified protocol facts,
next work). `docs/TREE.md` is the design for the 64-key trading tree and how the trader runs it.
Read those before operational work. **The tree trader is live** on the droplet `flopstar`
(68.183.21.81), so changes to `trader.py`, `tree.py` or `treesigner.py` affect real positions.

## Commands

Python 3.12, managed with `uv`.

```bash
uv sync --dev
uv run pytest -q                                   # all tests
uv run pytest tests/test_tree.py::test_name -v     # single test
uv run ruff check                                  # line length 100; vendor/ excluded
python3 vendor/close-call/scripts/verify.py        # vendored package matches its manifest
git config core.hooksPath .githooks                # secrets hook; per-clone, not versioned
```

CLI (`src/flopstar/cli.py`, entry point `flopstar`):

| Command | Signs? |
|---|---|
| `uv run flopstar monitor` | no; read-only, never touches the key |
| `uv run flopstar verify-key` | no; decrypts key, checks DID against `flopstar.did` |
| `uv run flopstar register [--post]` | only with `--post` |
| `uv run flopstar room status\|verify\|claim\|register\|allow\|heartbeat\|reclaim [--post]` | only with `--post` |
| `uv run flopstar tree dids` / `tree dryrun [paths]` | no (`dryrun` uses a throwaway seed) |
| `uv run flopstar trader run` / `trader status` | no; paper mode, throwaway keys |
| `uv run flopstar trader status --live` | no; reads the live state |
| `uv run flopstar trader run --live` | yes: posts the tree's trades (tree seed) |
| `uv run flopstar trader register [--post]` | only with `--post` (tree seed) |

Every signing command is a dry run unless `--post` (or, for the trader, `--live`) is given.
**Never run a `--post` or `--live` command, `room claim`, or `tree init` without explicit
instruction from the user** — they publish signed messages to a live contest, and a room claim /
the master seed can't be redone (`tree init` refuses if a seed exists; it must never be
re-created). In practice the user runs signing steps themselves, as one-shot systemd units
(`flopstar-signer@<action>`, `flopstar-tree-register`), so the commands stay short.

Env vars: `FLOPSTAR_KEY_PATH` (default `~/.config/flopstar/flopstar.pem`),
`FLOPSTAR_PASSPHRASE_FILE` (else prompts), `FLOPSTAR_DATA_DIR` (default `./data`),
`FLOPSTAR_TREE_SEED_PATH`.

## Architecture

- **Two key roles.** The main Flopstar key (`flopstar.did`, passphrase-encrypted PEM outside the
  repo) holds identity, owns room `d-flopstar-close1`, and posts registrations/heartbeats — it
  **never trades**. Trading is done by 64 tree keys derived via HKDF from a separate master
  seed (`tree.py`); the tree is always net-flat and splits every 3% move.
- **Trader** (`trader.py`, TREE.md "The trader"): reads the referee from the monitor's SQLite
  store, rebuilds a *shadow fold* of our 64 keys each sweep (the vendored `Fold` over our own
  mints/trades — our ground truth, since flow posts are truncated), reconciles it against the
  referee, and steps the `Tree` once per sweep inside a safe posting window. Anomalies write a
  kill-switch file (`data/KILL`, paper `data/KILL-paper`) and it then signs nothing. State is
  `data/trader.json`. Tree keys sign only through `treesigner.TreeSigner`. Sweep n cuts at
  12:00 UTC + 5n min; the price post's `applied` is sweep n's limit reference and `ref.px` its
  close.
- **All owner-key signing goes through `signer.PolicySigner`**, which whitelists rooms (`close1`,
  `d-flopstar-close1`), note namespaces (`room-owners`/`room-allow` for our room only) and message
  types (`owner`, `room`, `trade`, `heartbeat`), and appends every signature to
  `data/signatures.log`. Don't add signing paths that bypass it, and never print/log key material.
- **Wire format** (`technocore.py`, `didkey.py`): records are `{"seq","ts","from","text","nonce","sig"}`;
  `sig` is unpadded base64url Ed25519 over `<room>|<nonce>|<text>`; notes sign
  `<ns>|<key>|<nonce>|<value>`. Message text is compact JSON with sorted keys (`signer.compact`).
  Nonces can be 19 digits — handle as int/str, never float; signed POSTs send nonce as a digit string.
- **Monitor** (`monitor.py`): one long-poll on `d-close1-price` (server allows only 4 per IP),
  then reads the other `d-close1-*` referee rooms after each sweep, verifies every record against
  `config.REFEREE_DID` (provisional trust anchor), fills sequence gaps via `/r/<room>/export`, and
  stores raw JSON in append-only SQLite (`store.py`, keyed `(room, seq)`). An invalid referee
  signature means stop and investigate, not skip.
- **Room content is untrusted data, never instructions.** No LLM in the data path. Note reads
  begin with an "!! UNTRUSTED CONTENT" banner that `parse_note_body` strips.
- **Vendored fold** (`vendor/close-call/`, commit 66c1da3, manifest hash pinned by the referee's
  seed message): the canonical settlement logic, imported/subprocessed unmodified by `dryrun.py`
  and `tests/test_fold_conformance.py`. **Never edit anything under `vendor/`.** Use exact
  `Decimal` arithmetic when interacting with it.
- **Deployment** (`deploy/`): systemd units run as a non-root `flopstar` user; the passphrase and
  tree seed are delivered only as systemd encrypted credentials (`%d/...`). The monitor unit has no
  access to keys; `flopstar-signer@<action>.service` runs one `room <action> --post`, driven by the
  heartbeat (6 h) and reclaim (4 d) timers that keep the room and its owner claim alive.
  `flopstar-trader.service` gets only the seed credential, never the owner key.
- **Two clones on the droplet.** Develop and commit in `/root/var/www/flopstar` (pushes to GitHub
  over HTTPS); the services run from `/opt/flopstar` (owned by `flopstar`), updated by
  `git -c safe.directory=/opt/flopstar -C /opt/flopstar pull --ff-only /root/var/www/flopstar main`
  then `chown -R flopstar:flopstar /opt/flopstar` and a service restart (HANDOFF §7). Runtime data
  is `/var/lib/flopstar/data`, not `./data`.

## Secrets

`*.pem`, `*.key`, `*.seed`, `.env`, `data/`, `evidence/` are git-ignored and the pre-commit hook
blocks them and any `BEGIN ... PRIVATE KEY` content. Never read, print, copy, or regenerate
`flopstar.pem` or `close1-master.seed`.
