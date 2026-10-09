# Flopstar Deployment Handoff

**Project**: Flopstar - Technocore Close Call Contest Agent
**Status**: Phase 3 Complete (Read-only Monitor)
**Date**: September 25, 2026
**Repository**: https://github.com/botmechanic/flopstar

---

## Executive Summary

Flopstar is a Python agent for the FLOP Labs "Close Call" trading contest. Currently implemented:
- **Phase 1**: Security hygiene (key isolation, pre-commit hooks)
- **Phase 2**: Project setup (vendored challenge code, dependencies)
- **Phase 3**: Read-only monitor (tracks referee data, no trading yet)

The monitor runs continuously, tracking contest data from technocore.chat and storing it in SQLite. **It does not access the private key** and makes no trading decisions.

---

## Critical Security Requirements

### Private Key Management

**CRITICAL**: The private key (`flopstar.pem`) is stored **outside the git repository**.

**Local development**:
- Location: `~/.config/flopstar/flopstar.pem`
- Permissions: `600` (owner read/write only)
- Directory: `~/.config/flopstar/` with `700` permissions

**Cloud deployment**:
```bash
# DO NOT clone the key from git - it's not there
# Transfer the key separately using SCP or secrets manager

# Example deployment to droplet:
scp ~/.config/flopstar/flopstar.pem user@droplet:/etc/flopstar/keys/
ssh user@droplet

# On the droplet:
sudo mkdir -p /etc/flopstar/keys
sudo chown flopstar:flopstar /etc/flopstar/keys
sudo chmod 700 /etc/flopstar/keys
sudo mv ~/flopstar.pem /etc/flopstar/keys/flopstar.pem
sudo chmod 600 /etc/flopstar/keys/flopstar.pem
sudo chown flopstar:flopstar /etc/flopstar/keys/flopstar.pem

# Verify
ls -la /etc/flopstar/keys/
# Should show: -rw------- 1 flopstar flopstar 302 ... flopstar.pem
```

**Never**:
- Commit the key to git (even force-pushing later won't help - history persists)
- Send the key via Slack, email, or logs
- Use `git add -f` to bypass `.gitignore`
- Copy the key into the project directory

**Environment variable**:
```bash
export FLOPSTAR_KEY_PATH=/etc/flopstar/keys/flopstar.pem
```

### Multi-Layer Protection

1. **`.gitignore`**: Blocks `*.pem`, `*.key`, `*.seed`, `.env` files
2. **Pre-commit hook**: Scans staged files for PEM headers (skips docs)
3. **Key isolation**: Key stored outside project directory entirely

---

## Deployment Architecture

### Recommended Setup

```
Cloud VM (Ubuntu 22.04+ / Debian 12+)
├── Application user: flopstar (non-root)
├── Application directory: /opt/flopstar
├── Keys directory: /etc/flopstar/keys (mode 700)
├── Data directory: /var/lib/flopstar/data
├── Logs: journald (systemd) or /var/log/flopstar
└── Process manager: systemd
```

### System Dependencies

```bash
# Python 3.12+
sudo apt update
sudo apt install -y python3.12 python3.12-venv curl

# uv package manager
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env

# Optional: monitoring tools
sudo apt install -y htop iotop
```

### Application User

```bash
sudo useradd -r -m -d /opt/flopstar -s /bin/bash flopstar
sudo mkdir -p /etc/flopstar/keys /var/lib/flopstar/data
sudo chown -R flopstar:flopstar /opt/flopstar /var/lib/flopstar
sudo chmod 700 /etc/flopstar/keys
```

### Clone and Install

```bash
sudo -u flopstar -i
cd /opt/flopstar

# Clone repository
git clone https://github.com/subloop-xyz/flopstar.git .

# Install dependencies
uv sync --dev

# Configure environment
cp .env.example .env
nano .env
# Set:
#   FLOPSTAR_KEY_PATH=/etc/flopstar/keys/flopstar.pem
#   FLOPSTAR_DATA_DIR=/var/lib/flopstar/data

# Test
uv run pytest -v
uv run ruff check
```

### Systemd Service

Create `/etc/systemd/system/flopstar-monitor.service`:

```ini
[Unit]
Description=Flopstar Close Call Contest Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=flopstar
Group=flopstar
WorkingDirectory=/opt/flopstar
Environment="PATH=/opt/flopstar/.local/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=/opt/flopstar/.env
ExecStart=/opt/flopstar/.local/bin/uv run flopstar monitor
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=flopstar-monitor

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/flopstar/data
ReadOnlyPaths=/etc/flopstar/keys

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable flopstar-monitor
sudo systemctl start flopstar-monitor

# Check status
sudo systemctl status flopstar-monitor
sudo journalctl -u flopstar-monitor -f
```

---

## Architecture Overview

### Components

```
src/flopstar/
├── cli.py           Entry point (flopstar monitor)
├── config.py        Contest config, referee DID, room names
├── didkey.py        DID:key ↔ Ed25519, signature verification
├── technocore.py    HTTP client for technocore.chat
├── store.py         Append-only SQLite storage
└── monitor.py       Main monitoring loop
```

### Data Flow

```
technocore.chat
    ↓ (long-poll d-close1-price every 10s)
TechnocoreClient
    ↓ (verify signature against REFEREE_DID)
ContestMonitor
    ↓ (store raw JSON)
MessageStore (SQLite)
    ↓ (query for analysis)
Future: Trading Agent
```

### Monitor Behavior

1. **Startup**: Syncs all referee rooms from sequence 0 (or last known)
2. **Long-poll**: Only `d-close1-price` (10s timeout, returns immediately if new data)
3. **Trigger**: On new price message → sync all other rooms once
4. **Gap detection**: If `first_seq > last_seq + 1` → backfill via export API
5. **Rate limiting**: Honors 429, backs off 60s
6. **Signature verification**: Every message verified against referee DID before storage

**Key constraint**: Only 4 concurrent long-polls per IP. We use 1 to stay safe.

### Database Schema

```sql
CREATE TABLE messages (
    room TEXT NOT NULL,
    seq INTEGER NOT NULL,
    raw_json TEXT NOT NULL,
    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (room, seq)
);

CREATE INDEX idx_messages_room ON messages(room, seq);
```

Location: `$FLOPSTAR_DATA_DIR/flopstar.db` (default: `./data/flopstar.db`)

---

## Contest Information

### Timeline

| Event | Date/Time (UTC) | Status |
|-------|-----------------|--------|
| Opening | Sep 25, 2026 12:00 | **LIVE** |
| Sweeps | Every 5 min (2,556 total) | Running |
| Lock | Oct 4, 2026 09:00 | 8.5 days remaining |
| Closing Price | Oct 4, 2026 10:00 | Final settlement |

### Referee Rooms (Read-Only)

| Room | Purpose | Long-Poll? |
|------|---------|------------|
| `d-close1-price` | Reference price, limits, global price | **YES** |
| `d-close1-flow` | Trade outcomes (counts only) | No |
| `d-close1-positions` | Open interest, positions | No |
| `d-close1-pnl` | PnL and leaderboard | No |
| `d-close1-state` | State roots | No |

### Trust Anchor (PROVISIONAL)

**⚠️ WARNING**: Using referee DID from sonnet-2 launch record:
```
did:key:z6MkowHQwsx9xr84WbWN3YCnKutyBnBXkT1ChKY4uEAAMzte
```

Close-1 **does not have a signed launch record yet**. Monitor logs loud warning on startup. All referee messages are verified against this DID before storage.

### Known Issue: Flow Files Not Downloadable

**Investigation result**: `d-close1-flow` posts contain only **counts** (`settled: N`, `void: M`). Per-trade outcomes with reasons are in flow **files** (referenced by hash), which are **not downloadable** (issue #6 on challenge repo).

**Implication**: Cannot do full-ledger replay from flow room alone. Would need to track all trades from trading rooms to replay with vendored fold.

---

## Monitoring and Operations

### Health Checks

```bash
# Service status
sudo systemctl status flopstar-monitor

# Live logs
sudo journalctl -u flopstar-monitor -f

# Database check
sqlite3 /var/lib/flopstar/data/flopstar.db "SELECT room, COUNT(*), MAX(seq) FROM messages GROUP BY room;"

# Disk usage
du -sh /var/lib/flopstar/data/
```

### Expected Log Output

```
2026-09-25 12:00:01 [WARNING] flopstar.monitor: ⚠️  Using PROVISIONAL trust anchor...
2026-09-25 12:00:01 [INFO] flopstar.monitor: Using database: /var/lib/flopstar/data/flopstar.db
2026-09-25 12:00:01 [INFO] flopstar.monitor: Starting initial sync of all referee rooms
2026-09-25 12:00:02 [INFO] flopstar.monitor: Synced d-close1-price: 5 new messages
2026-09-25 12:00:03 [INFO] flopstar.monitor: Synced d-close1-flow: 3 new messages
...
2026-09-25 12:00:10 [INFO] flopstar.monitor: Starting long-poll on d-close1-price
```

### Troubleshooting

**No new messages**:
- Check network: `curl https://technocore.chat/r/d-close1-price?format=json`
- Verify sequence: Query DB for last seq, compare to live room

**Rate limited**:
- Monitor logs for "Rate limited (429)" messages
- Automatic 60s backoff, will retry
- Check for other processes hitting technocore.chat from same IP

**Signature verification failures**:
- **CRITICAL**: Invalid signatures mean compromised data or wrong referee DID
- Check logs for "INVALID SIGNATURE" errors
- Verify referee DID against launch record

**Database locked**:
- SQLite doesn't support high concurrency writes
- Only one monitor process per database file
- Check for stale processes: `ps aux | grep flopstar`

**High memory usage**:
- Check message backlog: `SELECT COUNT(*) FROM messages;`
- Monitor processes with `htop`
- Expected: <100MB for monitor process

---

## Next Steps / TODOs

### Phase 4: Trading Logic (Not Yet Implemented)

**Prerequisites**:
1. Decide on trading strategy (market making, directional, arbitrage, etc.)
2. Implement position risk management
3. Build trade signing and posting logic
4. Add monitoring for our own trades in flow room

**Components to build**:
```
src/flopstar/
├── signer.py        Load key, sign trades (did:key format)
├── trader.py        Trading strategy and execution
├── position.py      Position tracking and risk management
└── market.py        Order book / market data analysis
```

**New commands**:
- `uv run flopstar register` - Post owner registration message
- `uv run flopstar trade` - Run trading agent (with key access)
- `uv run flopstar status` - Check positions, PnL from DB

### Phase 5: FlopWatch Integration

**Opportunity**: Use FlopWatch API at `https://flopwatch.xyz/api/snapshot` for:
1. Cross-validation of our stored referee data
2. Backup data source during technocore.chat downtime
3. Historical backfill if we miss sequences
4. Aggregated analytics and derived metrics

**Implementation**:
```python
# src/flopstar/flopwatch.py
class FlopWatchClient:
    async def get_snapshot(self) -> dict:
        """Fetch latest contest snapshot."""

    async def validate_against_local(self, store: MessageStore):
        """Cross-check our DB against FlopWatch data."""
```

### Infrastructure Improvements

1. **Backup automation**:
   ```bash
   # Cron job to backup SQLite daily
   0 0 * * * sqlite3 /var/lib/flopstar/data/flopstar.db ".backup /backups/flopstar-$(date +\%Y\%m\%d).db"
   ```

2. **Metrics and alerting**:
   - Prometheus exporter for message counts, sync lag
   - Alert on signature verification failures
   - Alert on prolonged rate limiting

3. **Multi-instance deployment**:
   - Run monitor on multiple IPs/regions for redundancy
   - Sync databases periodically for consensus
   - Load balancer for API (when trading is implemented)

4. **Testing improvements**:
   - Integration tests against live technocore.chat (test rooms)
   - Mock referee for unit testing signature verification
   - Property-based tests for DID:key encoding/decoding

---

## Vendored Code

**Location**: `vendor/close-call/`
**Source**: https://github.com/flop-labs/technocore-close-call-challenge
**Commit**: `66c1da3`
**Manifest SHA256**: `bae09812e25eb6f1369c611f24964f7ea0acafddfc45301a16f33f941296dafa`

**Verification**:
```bash
shasum -a 256 vendor/close-call/manifest.json
python3 vendor/close-call/scripts/verify.py
```

**Important**: Never edit vendored files. Import or subprocess `close_call_fold.py` as-is.

---

## Development Workflow

### Local Testing

```bash
# Run monitor locally (will create ./data/flopstar.db)
uv run flopstar monitor

# In another terminal, query DB
sqlite3 data/flopstar.db "SELECT * FROM messages ORDER BY indexed_at DESC LIMIT 5;"

# Run tests
uv run pytest -v

# Lint
uv run ruff check
uv run ruff format --check
```

### Git Workflow

```bash
# Pre-commit hook is active - test it
echo "-----BEGIN PRIVATE KEY-----" > test.pem
git add test.pem
git commit -m "test"  # Should fail with hook error
rm test.pem

# Normal workflow
git add .
git commit -m "Your message"
git push origin main
```

### Updating Dependencies

```bash
# Add new dependency
uv add package-name

# Update all
uv sync --upgrade

# Lock file
git add uv.lock pyproject.toml
git commit -m "Update dependencies"
```

---

## API Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLOPSTAR_KEY_PATH` | `~/.config/flopstar/flopstar.pem` | Path to Ed25519 private key |
| `FLOPSTAR_DATA_DIR` | `./data` | SQLite database directory |

### Commands

```bash
uv run flopstar monitor    # Run read-only monitor (no key access)
uv run pytest -v           # Run tests
uv run ruff check          # Lint code
```

### Database Queries

```sql
-- Message counts by room
SELECT room, COUNT(*), MIN(seq), MAX(seq)
FROM messages
GROUP BY room;

-- Recent messages
SELECT room, seq, json_extract(raw_json, '$.text')
FROM messages
ORDER BY indexed_at DESC
LIMIT 10;

-- Check for gaps
SELECT room, seq,
       LAG(seq) OVER (PARTITION BY room ORDER BY seq) as prev_seq
FROM messages
WHERE seq != prev_seq + 1 AND prev_seq IS NOT NULL;

-- Referee messages by type
SELECT
    json_extract(raw_json, '$.text') ->> '$.t' as msg_type,
    COUNT(*)
FROM messages
WHERE room LIKE 'd-close1-%'
GROUP BY msg_type;
```

---

## Support and Resources

### Documentation

- **Main README**: `README.md` - User-facing documentation
- **Challenge Rules**: `vendor/close-call/close-call-game.md`
- **Contest Config**: `vendor/close-call/contest.json`
- **API Docs**: https://technocore.chat/llms.txt

### Key Contacts

- **FLOP Labs**: Check challenge repo for official announcements
- **Contest Room**: https://technocore.chat/r/close1
- **Issues**: https://github.com/flop-labs/technocore-close-call-challenge/issues

### Debugging

```bash
# Enable debug logging
# In monitor.py, change: level=logging.DEBUG

# Inspect HTTP traffic
uv add httpx[cli]
uv run httpx https://technocore.chat/r/d-close1-price?format=json

# Database introspection
sqlite3 data/flopstar.db .schema
sqlite3 data/flopstar.db .tables
```

---

## Security Checklist

Before deploying to production:

- [ ] Private key stored outside git repository
- [ ] Private key permissions: `600` (owner read/write only)
- [ ] Keys directory permissions: `700` (owner access only)
- [ ] Application runs as non-root user
- [ ] Systemd security hardening enabled
- [ ] Environment variables set (not hardcoded paths)
- [ ] Pre-commit hook active and tested
- [ ] No `.pem` files in git history (`git log --all --oneline -- '*.pem'`)
- [ ] Database directory writable by app user only
- [ ] Firewall configured (if exposing API later)
- [ ] Backups automated and tested
- [ ] Logs rotated (journald handles this)
- [ ] Secrets never logged (review logging statements)

---

## Performance Expectations

### Resource Usage

- **CPU**: <5% idle, <20% during sync
- **Memory**: 50-100MB
- **Disk**: ~1MB/day for message storage (varies by activity)
- **Network**: Long-poll persistent connection, ~1KB/10s when idle

### Scale Limits

- **SQLite**: Handles millions of rows fine for read-heavy workload
- **Concurrency**: Monitor is single-threaded (one process per DB)
- **Backfill**: Export API can be slow, expect ~1 sec per 100 messages

---

## Final Notes

This monitor is **production-ready for data collection**. It does not trade or access the private key yet.

**Before implementing trading logic**:
1. Thoroughly test signature verification against known valid trades
2. Implement dry-run mode (sign trades but don't post)
3. Start with tiny positions (0.1 contracts) to test settlement
4. Monitor flow room for your trade IDs to confirm settlement
5. Build kill switch (emergency position exit)

**Risk warning**: This is a contest with real FLOP token prizes. Bugs in trading logic could result in:
- Locked collateral (every contract ties up its price in POLF)
- Fee losses (1% each side)
- Void trades (wasted time/effort)
- Missed opportunities (if monitor falls behind)

Test extensively before deploying to production.

---

**Questions?** Review:
1. Main README: `README.md`
2. Challenge docs: `vendor/close-call/close-call-game.md`
3. Source code: Well-commented, start with `src/flopstar/monitor.py`

Good luck! 🚀
