# Migration: shared forge VPS → standalone Concordia box

Adversarially-reviewed cutover runbook (2026-07-23). The club is LIVE with real
money — the whole design exists so that **two boxes can never both trade** and a
failed step is always recoverable. Do the phases in order; the rollback rule in
Step 9 is the line of no return.

**Resolved facts (drift check, 2026-07-23):** live DB = `/opt/concord/backend/concord.db`
(`CONCORD_DB_PATH=./concord.db` + WorkingDirectory). Live units match repo
`infra/` except the repo's newer `UMask=077` hardening (deploy repo versions).
Old-box enabled set: api, web, backup, bot-reweight, bot-vote, close-vote,
execute, fresh-start, open-vote, research-vote, reset-account, weekly-power
timers (verify-report deliberately disabled).

## Phase A — before cutover week

1. **Domain first, not later.** Buy the .com (Porkbun ~$11/yr, privacy included)
   and point an A record at the new box the moment it exists. Reason: a fresh
   `*.sslip.io` name draws from Let's Encrypt's *shared* sslip.io rate bucket;
   the app sets secure cookies, so a failed cert at cutover = nobody can log in.
2. Provision the box (see README of the chosen provider; Debian/Ubuntu LTS,
   key-only SSH from the first boot). Allow lead time for signup verification.
3. Drift check (done 2026-07-23 — rerun if cutover slips >1 week).
4. Run `infra/bootstrap-standalone.sh` (`HOST=… SRC=…`). It installs everything
   and enables **only nginx**. Then:
   - `systemctl mask concord-fresh-start.timer concord-reset-account.timer` —
     past-dated Persistent one-shot **liquidation** units; masked forever.
   - Fill the API nginx allow-lines for `/api/admin` + `/api/program` with
     `127.0.0.1` **and `5.78.193.177`** (forge dashboard + research voter live there).
   - **Do NOT build the web app yet** (build bakes env; wait for Step 7's .env).
   - Keep nginx serving 444/nothing for the API until the real .env lands —
     bootstrap generates a *transient* Fernet key; nothing may ever store tokens
     under it.
   - Collaborator access: own unix user, sudoers scoped to
     `systemctl restart concord-api concord-web` + a root-owned deploy script.
     Never general sudo — root here = custody of members' brokerage tokens.
5. TLS early: `certbot --staging` to prove plumbing, then production cert.

## Phase B — freeze day (a trading day; start after ~10:00 ET, done before 18:00 ET)

6. **Freeze = disable + MASK, never just stop** (a rebooted old box would
   catch-up-fire Persistent timers against the stale DB, refresh RH tokens, and
   Robinhood's rotation-reuse detection would revoke every member's token
   family — the historical forced-reconnect bug, cross-host edition):
   ```
   systemctl disable --now concord-api concord-web 'concord-*.timer'
   systemctl mask concord-api 'concord-*.timer'
   ```
   First confirm the morning verify report is clean (no deferred fill-recovery).
   From now until Step 10 there is NO arm/halt control plane — keep it short.
7. Copy to the new box (ONE ssh ControlMaster socket — fail2ban bans rapid SSH):
   - DB: `sudo -u concord sqlite3 /opt/concord/backend/concord.db ".backup /tmp/concord-migrate.db"`,
     sha256 both ends.
   - `.env`: SAME `CONCORD_FERNET_KEY`, `SESSION_SECRET`, `CONCORD_ADMIN_TOKEN`,
     `INVITE_CODE`; NEW `API_PUBLIC_URL`/`WEB_ORIGIN` (the new domain);
     **`EXECUTION_MODE=dry_run`** (flip to live only at Step 11).
   - ALSO append the staged Google line from /root/cutover-env-additions.txt on the
     new box (`GOOGLE_CLIENT_ID=…`) — the web build already carries the matching
     NEXT_PUBLIC_GOOGLE_CLIENT_ID (baked 2026-07-23; OAuth app published to production).
8. Restore hygiene: DB → `/opt/concord/backend/concord.db`, chown concord,
   chmod 600, no stray `-wal/-shm`. **Immediately disarm the copied DB** (the
   `armed=true` kv row traveled with it):
   `sudo -u concord sqlite3 …/concord.db "UPDATE kv SET value='false' WHERE key='armed'"`
   Belt (dry_run) + braces (disarmed). Now build the web app, open nginx.
9. Verify in two halves — **write the rollback rule down first**:
   - (A) Non-token: api boots, `/health` shows armed:false; `vault.load_bundle`
     decrypts for every member (proves the Fernet key survived); member login
     over HTTPS works.
   - (B) Token-spending: forced DRY-RUN execute pinned to the latest closed
     session (`run(session, force_dry_run=True)` — an idle-day `run_today` proves
     nothing) + `verify.verify_all()`. These refresh/rotate RH tokens — safe only
     because the old box is masked, and they make the NEW DB the sole token store.
   - **Rollback rule:** before (B): unmask old box, done. After (B): copy the
     NEW DB back to the old box *first* — re-enabling the old box on the stale
     DB after (B) mass-bricks members via family revocation.

## Phase C — cutover (same day, before 18:00 ET)

10. Enable exactly the old box's enabled set MINUS fresh-start + reset-account
    (masked). **Bots stay (owner decision 2026-07-23): bot-vote/bot-reweight run
    HERE (they need the live DB) with LLM inference tunneled to the old box's
    Ollama** — set up `concord-ollama-tunnel.service` first (dedicated
    `ollama-tunnel` user on the forge box, authorized_keys entry
    `restrict,port-forwarding,permitopen="127.0.0.1:11434" ssh-ed25519 …`, key at
    /opt/concord/.ssh/ollama_tunnel), verify `curl 127.0.0.1:11434/api/tags`
    through it, then:
    `systemctl enable --now concord-api concord-web concord-ollama-tunnel concord-{open-vote,close-vote,execute,backup,weekly-power,research-vote,bot-vote,bot-reweight}.timer`
    (Bots exit executable consensus later per the GTM plan, as membership grows.)
    Check `systemctl list-timers 'concord-*'` next-fire times look ET-sane. If
    timers aren't live before 18:00 ET, run `jobs/open_vote.py` manually.
11. Flip `EXECUTION_MODE=live`, restart concord-api, owner re-arms via the
    normal human two-key flow. Arming stays human-only.
12. Repoint cross-services (both break otherwise):
    - forge dashboard on 5.78.193.177: `CONCORD_API_URL=https://<new-domain>`
      (it assumed same-box `127.0.0.1:8780`). Carries the admin token → HTTPS only.
    - `programs/research_voter.py` DEFAULT_URL → new domain (its ck_ key hashes
      traveled in the DB; only the URL is stale).
13. Member comms: new URL; everyone logs in again (host-scoped cookies —
    expected); Robinhood connections carry over (localhost-paste OAuth, nothing
    host-bound). Any `rh_reauth_required` audit row afterward = something ran on
    the old box — investigate immediately.
14. Babysit the first live 09:31 ET run: `journalctl -fu concord-execute.service`
    + the 09:50 verify report.

## Phase D — DR + decommission

15. Cross-backup: ed25519 key on the new box → rrsync-restricted receiver user
    on the forge VPS (never root); set `CONCORD_BACKUP_DEST`; one manual
    `jobs/backup.py` run verified landing; then enable `concord-backup.timer`.
    Wire a sentinel alert on ship failure (backup.py only WARNs). Restore drill
    quarterly.
16. After ~5 clean trading days: delete `CONCORD_FERNET_KEY` + `SESSION_SECRET`
    from the OLD box's .env (offline copies out-of-band) — the retired box then
    holds ciphertext-only backups and can never reassemble live brokerage
    tokens. Old concord-* units stay masked forever. Do NOT rescale the old
    Hetzner box (it keeps grandfathered pre-2026-06 pricing only until rescaled).
