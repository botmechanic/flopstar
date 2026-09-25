# Migration: /root/var/www/flopstar -> /opt/flopstar (DRAFT — not applied)

Goal: the agent runs as a dedicated non-root `flopstar` user; the owner key lives in
`/etc/flopstar/keys/` (0600, flopstar); the passphrase is a systemd encrypted credential that
only the signing unit receives. Nothing is ever printed, logged or committed.

What this protects, and what it does not: without a TPM, systemd-creds encrypts with the host
key `/var/lib/systemd/credential.secret` (root-only). So the `.cred` file is useless if copied
off the box (backups, snapshots, a stray scp), and the monitor and every other non-root
process can never read the key or passphrase. Root on this host can still decrypt everything,
and several services here run as root (e.g. the Bun bot on :8080 in /root/var/www/hoodtrencher).
A compromise of any root service is a compromise of the key, before and after this migration.

## 0. Stop what is running now
    pkill -f "flopstar monitor"          # the ad-hoc background monitor

## 1. User and directories
    useradd --system --home-dir /opt/flopstar --shell /usr/sbin/nologin flopstar
    install -d -o flopstar -g flopstar -m 0755 /opt/flopstar
    install -d -o flopstar -g flopstar -m 0700 /var/lib/flopstar /var/lib/flopstar/data
    install -d -o root     -g flopstar -m 0750 /etc/flopstar
    install -d -o flopstar -g flopstar -m 0700 /etc/flopstar/keys
    install -d -o root     -g root     -m 0700 /etc/flopstar/creds

## 2. Code (commit today's work first: this clones the committed repo, so no untracked files ride along) and venv
    git clone /root/var/www/flopstar /opt/flopstar          # target dir is empty
    cd /opt/flopstar && UV_LINK_MODE=copy UV_PYTHON=/usr/bin/python3.12 /root/.local/bin/uv sync --frozen
      # run as root because uv lives under /root; copy mode so the venv holds no hardlinks
      # into /root's uv cache before the chown below
    git -C /opt/flopstar config core.hooksPath .githooks   # hooksPath is per-clone, not versioned
    chown -R flopstar:flopstar /opt/flopstar

## 3. Key: move, not copy (one copy on the box)
    install -o flopstar -g flopstar -m 0600 /root/.config/flopstar/flopstar.pem /etc/flopstar/keys/flopstar.pem
    sudo -u flopstar FLOPSTAR_KEY_PATH=/etc/flopstar/keys/flopstar.pem /opt/flopstar/.venv/bin/flopstar verify-key
      # must print MATCH; only then:
    shred -u /root/.config/flopstar/flopstar.pem

## 4. Passphrase as an encrypted credential (typed once, never touches disk in clear)
    systemd-ask-password -n "Flopstar key passphrase:" \
      | systemd-creds encrypt --name=flopstar-passphrase - /etc/flopstar/creds/flopstar-passphrase.cred
    chmod 0600 /etc/flopstar/creds/flopstar-passphrase.cred

## 4b. Tree master seed as an encrypted credential (never in clear under /etc)
    systemd-creds encrypt --name=close1-master-seed /root/.config/flopstar/close1-master.seed \
      /etc/flopstar/creds/close1-master-seed.cred
    chmod 0600 /etc/flopstar/creds/close1-master-seed.cred
      # the tree unit will get LoadCredentialEncrypted=close1-master-seed:... and
      # FLOPSTAR_TREE_SEED_PATH=%d/close1-master-seed. Keep an OFFLINE backup of the seed
      # before shredding: it is the only way to recreate the 64 tree keys.
    shred -u /root/.config/flopstar/close1-master.seed

## 5. Data: carry the monitor DB and evidence over
    install -o flopstar -g flopstar -m 0600 /root/var/www/flopstar/data/* /var/lib/flopstar/data/

## 6. Units
    cp /opt/flopstar/deploy/*.service /opt/flopstar/deploy/*.timer /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now flopstar-monitor.service
    systemctl start flopstar-signer@verify.service  # decrypts the credential, loads the key,
    journalctl -u flopstar-signer@verify -n 5        # checks the DID; signs nothing
    systemctl enable --now flopstar-heartbeat.timer flopstar-reclaim.timer   # only after the room is claimed

## 7. Verify
    systemctl status flopstar-monitor; journalctl -u 'flopstar-*' -n 50
    sudo -u caddy cat /etc/flopstar/keys/flopstar.pem      # must be Permission denied
    ls -la /etc/flopstar/keys /etc/flopstar/creds

## Rollback
The old tree at /root/var/www/flopstar stays untouched until the new services have run a day;
the key's only copy is then /etc/flopstar/keys/flopstar.pem, with the original still on the Mac.
