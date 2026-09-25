# Flopstar Handoff: moving to a dedicated droplet

**Project**: Flopstar, an agent for FLOP Labs' Close Call contest (`close-1`) on technocore.chat
**Repository**: https://github.com/subloop-xyz/flopstar (private)
**Written**: 25 September 2026, 15:40 UTC
**Current host**: `hoodwatch` (167.99.238.68), shared with other services and to be decommissioned for Flopstar
**Target**: a new droplet running only Flopstar

Read this whole document before touching the new droplet. Section 2 is the only place where a
mistake can't be undone.

---

## 1. State at handoff

| Item | State |
|---|---|
| Referee monitor | **Running on hoodwatch**, started by hand (not systemd); logs to `data/monitor.log`. Fully synced to sweep 37, no gaps, every record signature-verified. |
| Flopstar owner key | Verified: `uv run flopstar verify-key` printed MATCH for `did:key:z6MkjLpUAGLtNieLnCFQoUScwJxAKwo5PHZcHRsdyiCJG5Bv`. |
| Flopstar close-1 registration | **Posted** in `close1`, seq 548523, 15:14:12 UTC. The exact record is saved in `data/registration-close1.jsonl`. The mint can't be confirmed by name, because flow posts are truncated. |
| Own room `d-flopstar-close1` | **NOT CLAIMED YET.** No owner note, no messages. Claim it first (§4). |
| Key tree | Designed and dry-run only (`docs/TREE.md`). The master seed exists; no tree key is registered, allow-listed or trading. The live trader is **not built**. |
| systemd units | Drafted in `deploy/`, never installed anywhere. |
| Secrets hook | Enabled on hoodwatch (`core.hooksPath=.githooks`). It is per-clone, so enable it again on the droplet. |

### Why a dedicated droplet
- **Other services on hoodwatch run as root** (a Bun bot on :8080 and Docker). Any of them being
  compromised exposes the Flopstar key. On its own droplet, nothing else runs.
- **Rate limits are per IP** (600 reads/min, 300 writes/min, 4 long-polls). hoodwatch shares its
  IP with FlopWatch, which also reads technocore.chat. Flopstar gets its own budget.

---

## 2. Secrets inventory: read before moving anything

| Secret | Where it is now | Copies | Notes |
|---|---|---|---|
| `flopstar.pem` (Ed25519, PKCS8, **passphrase-encrypted**) | hoodwatch `/root/.config/flopstar/flopstar.pem` (0600) **and** the owner's Mac `~/.config/flopstar/flopstar.pem` | 2 | Never generate a replacement. The Flopstar DID is its identity. |
| Key passphrase | The owner's head / password manager | – | Never typed into chat, a repo, `data/` or a log. |
| `close1-master.seed` (32 bytes, hex) | hoodwatch `/root/.config/flopstar/close1-master.seed` (0600) **only** | **1** | Recreates all 64 tree keys. **Back it up offline before anything else.** Losing it loses the tree; leaking it leaks all 64 keys. |
| `evidence/` (pre-contest proof for the sonnet contest) | The owner's Mac only | 1 | Not needed on the droplet. Keep it backed up. |

**Rules**
- **Transfers:** move secrets only over SSH. Never via git, chat, email or logs.
- **Encrypt at rest:** on the droplet, the passphrase and the seed exist only as **systemd
  encrypted credentials**. Neither ever sits on disk in the clear.
- **One live signer at a time.** Don't run signing on hoodwatch and the droplet together.
- **Delete old copies only after verification.** Shred the hoodwatch copies only after the
  droplet has passed §3.8, and only after the seed has an offline backup.

**Step 0 (on your Mac, before anything else):** back up the seed offline.
```bash
ssh root@167.99.238.68 'cat /root/.config/flopstar/close1-master.seed'
# store the 64 hex characters in your password manager / offline backup
```

---

## 3. New droplet runbook

### 3.1 Droplet
- Ubuntu 24.04 LTS (needs systemd ≥ 254 for `%d` in unit files; 24.04 ships 255). The smallest
  size is plenty: the monitor uses under 100 MB of RAM and about 1 MB/day of disk.
- Log in with SSH keys only. Disable password logins.
- **No inbound ports other than SSH.** Flopstar only makes outbound HTTPS requests.
```bash
ufw default deny incoming && ufw allow OpenSSH && ufw enable
```

### 3.2 User, directories, uv
```bash
useradd --system --home-dir /opt/flopstar --shell /usr/sbin/nologin flopstar
install -d -o flopstar -g flopstar -m 0755 /opt/flopstar
install -d -o flopstar -g flopstar -m 0700 /var/lib/flopstar /var/lib/flopstar/data
install -d -o root     -g flopstar -m 0750 /etc/flopstar
install -d -o flopstar -g flopstar -m 0700 /etc/flopstar/keys
install -d -o root     -g root     -m 0700 /etc/flopstar/creds
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
```

### 3.3 Code
The repo is private. Add a **read-only deploy key** for the droplet on GitHub (repo → Settings →
Deploy keys) and use it only for this clone.
```bash
git clone git@github.com:subloop-xyz/flopstar.git /opt/flopstar
git -C /opt/flopstar config core.hooksPath .githooks     # per-clone, not versioned
chown -R flopstar:flopstar /opt/flopstar
cd /opt/flopstar && sudo -u flopstar env UV_PYTHON=python3.12 UV_CACHE_DIR=/opt/flopstar/.cache/uv uv sync --frozen
sudo -u flopstar /opt/flopstar/.venv/bin/python -m pytest -q     # expect all tests to pass
```

### 3.4 Owner key: from the Mac
Run this **on your Mac**, replacing `NEW` with the droplet's address:
```bash
ssh root@NEW 'umask 077; cat > /etc/flopstar/keys/flopstar.pem && chown flopstar:flopstar /etc/flopstar/keys/flopstar.pem && chmod 0600 /etc/flopstar/keys/flopstar.pem' \
  < ~/.config/flopstar/flopstar.pem
```
Then check it on the droplet. It prompts for the passphrase and must print `MATCH`:
```bash
cd /opt/flopstar && sudo -u flopstar env FLOPSTAR_KEY_PATH=/etc/flopstar/keys/flopstar.pem .venv/bin/flopstar verify-key
```

### 3.5 Passphrase: encrypted credential
You type it once. It's encrypted with the droplet's host key and never written in the clear.
```bash
systemd-ask-password -n "Flopstar key passphrase:" \
  | systemd-creds encrypt --name=flopstar-passphrase - /etc/flopstar/creds/flopstar-passphrase.cred
chmod 0600 /etc/flopstar/creds/flopstar-passphrase.cred
```
With no TPM, `systemd-creds` uses `/var/lib/systemd/credential.secret` (root-only). A copied
`.cred` file is useless off the box. Root on the droplet can still decrypt it, which is why
nothing else should run there.

### 3.6 Master seed: from hoodwatch straight into a credential
Run this **on your Mac**. The seed streams from hoodwatch into `systemd-creds` on the droplet and
never lands on the droplet's disk in the clear:
```bash
ssh root@167.99.238.68 'cat /root/.config/flopstar/close1-master.seed' \
  | ssh root@NEW 'systemd-creds encrypt --name=close1-master-seed - /etc/flopstar/creds/close1-master-seed.cred && chmod 0600 /etc/flopstar/creds/close1-master-seed.cred'
```
Check it by comparing the derived DIDs with the published list. This prints only public DIDs:
```bash
systemd-run --pipe --wait -p User=flopstar -p WorkingDirectory=/opt/flopstar \
  -p LoadCredentialEncrypted=close1-master-seed:/etc/flopstar/creds/close1-master-seed.cred \
  -p Environment=FLOPSTAR_DATA_DIR=/var/lib/flopstar/data \
  /bin/sh -c 'FLOPSTAR_TREE_SEED_PATH=$CREDENTIALS_DIRECTORY/close1-master-seed /opt/flopstar/.venv/bin/flopstar tree dids && head -2 /var/lib/flopstar/data/tree-dids.txt'
```
The first line must be `0 did:key:z6MkoW9KCbdiBrftF8We3uZSXnyjY8HTZvAtkw5Hyg4E9PM2`.

### 3.7 Data: carry history over
The monitor DB can be rebuilt by backfill, but only while the referee rooms still hold the
records. The registration evidence can't be rebuilt at all, because `close1` keeps only about
6 minutes of history. Copy everything, on your Mac:
```bash
ssh root@167.99.238.68 'tar -C /root/var/www/flopstar/data -cz .' \
  | ssh root@NEW 'tar -C /var/lib/flopstar/data -xz && chown -R flopstar:flopstar /var/lib/flopstar/data && chmod 0600 /var/lib/flopstar/data/*'
```

### 3.8 Services and verification
```bash
cp /opt/flopstar/deploy/*.service /opt/flopstar/deploy/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl start flopstar-signer@verify.service      # decrypts credential, loads key, checks DID
journalctl -u flopstar-signer@verify -n 5           # must show the Flopstar DID; signs nothing
```
Pass criteria: `verify` shows the Flopstar DID; the §3.6 check matches; `pytest` passes; and
`sudo -u nobody cat /etc/flopstar/keys/flopstar.pem` is refused.

### 3.9 Cutover
Keep this order so that exactly one monitor is running at any time:
```bash
# on hoodwatch
pkill -f "flopstar monitor"
# on the droplet
systemctl enable --now flopstar-monitor.service
journalctl -u flopstar-monitor -f                   # expect "Synced ..." then long-polling
```
Enable the timers **only after the room is claimed** (§4):
```bash
systemctl enable --now flopstar-heartbeat.timer flopstar-reclaim.timer
```

### 3.10 Decommission on hoodwatch
Only after §3.8 has passed and the seed has an offline backup:
```bash
shred -u /root/.config/flopstar/flopstar.pem /root/.config/flopstar/close1-master.seed
rm -rf /root/var/www/flopstar/data        # already copied in §3.7
```
The Mac's copy of `flopstar.pem` remains as the backup.

`deploy/MIGRATION.md` covers the same-host variant (moving within hoodwatch). This section
replaces it for the droplet move.

---

## 4. Own room `d-flopstar-close1`: do this first once the key is on the droplet

We trade in our own `d-` room because `close1` keeps only about 6 minutes of history, and a
message only counts if the referee reads it before it drops out. The referee has stalled for
13+ minutes before. A `d-` room can be claimed **only before its first message**, and a lost
claim can never be retaken.

Run on the droplet as `flopstar`, with the key path set. Each command is a dry run without `--post`.
```bash
cd /opt/flopstar
export FLOPSTAR_KEY_PATH=/etc/flopstar/keys/flopstar.pem FLOPSTAR_DATA_DIR=/var/lib/flopstar/data
sudo -E -u flopstar .venv/bin/flopstar room claim --post       # must print OWNED BY FLOPSTAR
sudo -E -u flopstar .venv/bin/flopstar room heartbeat --post   # first message, only after the claim
sudo -E -u flopstar .venv/bin/flopstar room register --post    # {"t":"room",...} in close1; saves evidence
sudo -E -u flopstar .venv/bin/flopstar room status             # "listed by referee: sweep N" once listed
```
If `claim` doesn't print `OWNED BY FLOPSTAR`, **stop**. Someone else holds the name, and the
room name in `config.OWN_ROOM` has to change before anything else happens.

**Keepalive.**
- **Why:** notes and rooms with no write for 7 days are deleted, and a room with a single message
  goes after 12 hours.
- **The timers:** `flopstar-heartbeat.timer` (every 6 h) and `flopstar-reclaim.timer` (rewrites
  the owner note every 4 days).
- **Their log:** both write to `data/signatures.log`, as every signature does.

---

## 5. Decisions and policies (agreed with the owner)

- **Flopstar's main key stays flat.** It holds the identity, owns the room, and makes the
  registration posts. It never trades.
- **Trading is done by a 64-key tree.** The design, dry-run results and remaining work are in
  `docs/TREE.md`.
  - The close-1 rules allow it: "One operator may run many keys and hold several places";
    `identity_policy: any did:key`; the organiser "disqualifies nobody at discretion".
  - The one-DID rule came from the **sonnet** contest and doesn't apply here.
- **Tree keys are close-1-only.** They sign only in `close1` and `d-flopstar-close1`, and never
  anywhere else on technocore.chat.
  - They register in `d-flopstar-close1` after the referee lists it, and each is allow-listed first.
  - After the lock, Flopstar signs and publishes a statement listing all 64 tree DIDs, in its
    room and in this repo.
- **Only a party to a trade posts it.** This is our **policy choice**. The confirmed rule is
  only that the poster must be a registered key.
- **Signer policy.** All owner-key signing goes through `PolicySigner`: rooms `close1` and
  `d-flopstar-close1`; notes `room-owners` and `room-allow` for our room; message types `owner`,
  `room`, `trade` and `heartbeat`. Every signature is logged. Never print key material.
- **Never generate a replacement Flopstar key.**

---

## 6. Contest facts (verified live, 25 Sep 2026)

- **Referee DID** (provisional: close-1 has no signed launch record yet):
  `did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte`. Pinned in FLOP Labs' signed sonnet-2
  launch record; it owns the `d-close1-*` rooms and signed the seed.
- **Seed:** `d-close1-price` seq 1 pins package `bae09812…96dafa`, which matches
  `vendor/close-call/manifest.json` (commit 66c1da3). Seed price 226.14.
- **Timeline:** sweeps every 5 min from 12:05 UTC on 25 Sep; lock at sweep 2556, 4 Oct 09:00 UTC;
  *S* = the last `xyz:NVDA` trade before 10:00 UTC on 4 Oct.
  - **The referee runs late:** sweep 32 posted 3 minutes late, then nothing came for 13+ minutes.
  - **Stale reference:** the reference can be old (`age_s` 3002 at sweep 32), because
    `xyz:NVDA` trades thinly.
- **Field size:** 352,876 owners and 51 registered rooms at sweep 32, so sybil farms are active.
  The top PnL was about +104.
- **Record format:** `{"seq","ts","from","text","nonce","sig"}`.
  - `from` is the signer DID. `sig` is 86 characters of unpadded base64url over
    `<room>|<nonce>|<text>`.
  - Nonces can be 19 digits: parse them as ints or strings, never floats.
  - Signed POSTs send `nonce` as a **digit string**.
- **Flow posts** list `settled`/`void` as `[id, reason]` pairs, but they're **truncated**; an
  `omitted` object counts what was left out. The full flow files can't be downloaded (issue #6),
  so a full replay is blocked. Confirm our own trades with a local shadow fold of our keys.
- **API behavior:**
  - `?since=S&limit=L` returns the **newest** L messages after S.
  - `/r/<room>/export` returns the whole retained history as raw JSONL and takes no parameters.
  - A 429 body says "retry after: Ns".
  - The server refuses the same text more than 5 times in 120 s (a 422).
- **Claim notes:**
  - `room-owners` and `room-allow` are signed writes over `<ns>|<key>|<nonce>|<value>`.
  - Both share `/kv/room-nonce/<room>` as their replay counter; a new nonce must be above it.
  - The allow-list note is capped at 8,192 characters, which is enough for about 140 DIDs.

---

## 7. Code map and commands

```
src/flopstar/
├── cli.py         entry point
├── config.py      referee DID, rooms, OWN_ROOM, paths
├── didkey.py      did:key → Ed25519, signature verification (base64url)
├── technocore.py  HTTP client: reads, long-poll, export, signed posts, signed notes
├── store.py       append-only SQLite (room, seq) → raw record
├── monitor.py     referee monitor: long-poll price, backfill gaps, sync the other rooms
├── signer.py      encrypted key loading, did derivation, PolicySigner
├── register.py    Flopstar's close-1 owner registration
├── room.py        own room: status | verify | claim | register | heartbeat | reclaim
├── evidence.py    save our exact signed records from the room export
├── tree.py        key tree: HKDF derivation, sizing, round/split engine
├── dryrun.py      tree against the vendored fold over simulated paths
└── treecli.py     tree: init | dids | dryrun
deploy/            systemd units, timers, same-host MIGRATION.md
docs/TREE.md       key tree design and dry-run results
vendor/close-call/ challenge package at 66c1da3. Never edit it.
```

| Command | Signs? | Needs |
|---|---|---|
| `flopstar monitor` | no | network |
| `flopstar verify-key` | no | key + passphrase |
| `flopstar register [--post]` | yes | key + passphrase (done; repeating is harmless) |
| `flopstar room status` | no | network |
| `flopstar room verify` | no | key + passphrase |
| `flopstar room claim/register/heartbeat/reclaim [--post]` | only with `--post` | key + passphrase |
| `flopstar tree init` | no | creates the seed; **refuses if one exists**. Do NOT run on the droplet; the seed comes from hoodwatch (§3.6). |
| `flopstar tree dids` | no | seed |
| `flopstar tree dryrun [paths]` | no | nothing (throwaway seed) |

Environment variables: `FLOPSTAR_KEY_PATH`, `FLOPSTAR_PASSPHRASE_FILE`, `FLOPSTAR_DATA_DIR`,
`FLOPSTAR_TREE_SEED_PATH`.

Checks: `uv run pytest -q` (14 tests), `uv run ruff check`, and `python3 vendor/close-call/scripts/verify.py`.

---

## 8. Next work, in order

1. **Droplet move** (§3) and **room claim** (§4).
2. **Wait for the referee to list the room** (`room status`).
3. **Build the live trader,** per `docs/TREE.md` "Still to build":
   - Hyperliquid price reader;
   - shadow fold of our 64 keys;
   - tree signer policy;
   - registration sequence (allow-list → 64 owner posts);
   - kill switch and alerts;
   - a `flopstar-tree.service` unit with both credentials.
4. **Owner reviews the trader's dry run, then round 0:** all 32 pairs in one sweep.
5. **After the lock:** Flopstar signs a statement listing all 64 tree DIDs.

---

## 9. Operations

```bash
systemctl status flopstar-monitor; journalctl -u 'flopstar-*' -n 50
cd /opt/flopstar && sudo -u flopstar env FLOPSTAR_DATA_DIR=/var/lib/flopstar/data .venv/bin/flopstar room status
```

```sql
-- sqlite3 /var/lib/flopstar/data/flopstar.db
SELECT room, COUNT(*), MIN(seq), MAX(seq) FROM messages GROUP BY room;

-- gaps (should be empty)
SELECT room, prev_seq, seq FROM (
    SELECT room, seq, LAG(seq) OVER (PARTITION BY room ORDER BY seq) AS prev_seq FROM messages
) WHERE prev_seq IS NOT NULL AND seq != prev_seq + 1;

-- referee messages by type
SELECT json_extract(json_extract(raw_json, '$.text'), '$.t') AS t, COUNT(*) FROM messages GROUP BY t;
```

**Troubleshooting**
- **Long-poll quiet:** compare the latest `d-close1-price` `ts` with the clock. A referee stall
  looks exactly like this and isn't our fault.
- **INVALID SIGNATURE in a referee room:** stop and investigate. Either the data is forged or
  the referee key changed. Check for a signed close-1 launch record.
- **429:** the client waits for the time the server names. Check that nothing else on the droplet
  hits technocore.chat.
- **`claim` returns 409 / not ours:** someone else holds the name. Don't post anything in it.
- **The hook didn't run on a commit:** run `git config core.hooksPath .githooks` in that clone.

**Security checklist (droplet)**
- [ ] Seed backed up offline before any move
- [ ] SSH keys only; `ufw` allows only OpenSSH
- [ ] `flopstar` user is non-root; key `0600 flopstar`; creds `0600 root`
- [ ] Passphrase and seed exist only as `.cred` files
- [ ] `flopstar-signer@verify` passes; `sudo -u nobody cat` on the key is refused
- [ ] `core.hooksPath` set in `/opt/flopstar`
- [ ] hoodwatch copies shredded after verification; only one monitor and one signer running
- [ ] `git log --all -- '*.pem' '*.seed'` is empty
