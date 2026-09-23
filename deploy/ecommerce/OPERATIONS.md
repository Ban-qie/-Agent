# EcomInsight V4 operations

This runbook applies only to the reviewed V4 release archive and the single
Tencent Cloud Hong Kong target. Run commands from the extracted release root.
Do not edit source files on the server.

## Private configuration

Create `deploy/ecommerce/.env.v4.private` from `.env.v4.example`, set mode 600,
and fill every placeholder with a unique secret. Keep the file outside backups,
archives, logs, Git, and chat. The public origin is fixed to
`https://ecominsight.cn`.

Validate the exact stack before creating containers:

```bash
docker compose --env-file deploy/ecommerce/.env.v4.private \
  -f deploy/ecommerce/compose.yml config --quiet
```

## First deployment and migration

Keep ports 80 and 443 closed while restoring. Start only the data services:

```bash
docker compose --env-file deploy/ecommerce/.env.v4.private \
  -f deploy/ecommerce/compose.yml up -d postgres redis
docker compose --env-file deploy/ecommerce/.env.v4.private \
  -f deploy/ecommerce/compose.yml ps
```

The private migration backup is separate from the release archive. Verify its
`manifest.sha256`, database dump hash, snapshot hashes, source commit, V3-only
migration policy, 247 archived debug records, and two unknown-usage records
before restore. Restore to a new database name; never overwrite the initialized
admin database or an existing production database. Revoke all restored sessions
and mark any active task interrupted. Set `V4_POSTGRES_DB` and
`V4_POSTGRES_SCHEMA` to the verified restored target only after row, owner,
ledger, and snapshot checks pass.

Build and start the application only after restore verification:

```bash
docker compose --env-file deploy/ecommerce/.env.v4.private \
  -f deploy/ecommerce/compose.yml build --pull app
docker compose --env-file deploy/ecommerce/.env.v4.private \
  -f deploy/ecommerce/compose.yml up -d app caddy
docker compose --env-file deploy/ecommerce/.env.v4.private \
  -f deploy/ecommerce/compose.yml ps
```

Only Caddy may publish ports. PostgreSQL, Redis, and port 5567 must stay on the
private Docker network. Open public 80/443 only in V4-S06 after local release and
rollback acceptance pass.

## Routine backup

Quiesce writes by closing the public analysis entry and stopping `caddy` and
`app`. Confirm that no task is accepted or running. Use
`devtools/backup_ecommerce.py` with an output directory that does not exist and
an immutable snapshot root. A valid backup contains `database.dump`, snapshot
files, `manifest.json`, and `manifest.sha256`; copy it off the instance only
after every recorded SHA-256 verifies. Redis is rebuildable and is not the
business-data backup.

The daily steady-state target is RPO 24 hours and RTO 30 minutes. A planned
release backup is taken after writes stop, so its target RPO is zero.

## Restart and stop

```bash
docker compose --env-file deploy/ecommerce/.env.v4.private \
  -f deploy/ecommerce/compose.yml restart
docker compose --env-file deploy/ecommerce/.env.v4.private \
  -f deploy/ecommerce/compose.yml stop
```

Never run `docker compose down -v`. The named PostgreSQL volume is the durable
business-data source. Caddy and application volumes also contain runtime state.

## Rollback

1. Close the analysis entry and stop `caddy` and `app`.
2. Preserve logs and take a final verified backup when storage is healthy.
3. Restore the last known-good backup into a new database and a new runtime
   directory. Use a new Redis namespace; do not restore sessions.
4. Set the private environment to the old release's compatible database and
   schema. Never point old code at an unreviewed newer schema.
5. Start the old fixed release, verify health, readiness, A/B ownership, ledger
   hash, and zero automatic model dispatch, then reopen the entry.
6. If verification fails, keep the entry closed and retain both databases,
   backups, logs, and receipts for console recovery.

The authorized maintenance window is 30 minutes. The user owns Tencent Cloud
console recovery. Rollback never deletes the failed database or resets usage.
