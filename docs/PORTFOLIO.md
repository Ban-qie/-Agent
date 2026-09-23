# EcomInsight V4

EcomInsight is an invited-user ecommerce analysis service built on the upstream
Data Formulator project. V3 adds server-owned accounts, PostgreSQL persistence,
object authorization, isolated workspaces, idempotent tasks, and durable usage
records. V4 adds the production profile, Redis coordination, bounded failure
handling, consistent backup and restore, a reviewed release tree, and a tested
rollback boundary.

The demonstration dataset is the Olist Brazilian ecommerce snapshot (Kaggle
API v2, CC BY-NC-SA 4.0). It is authorized here for noncommercial aggregate
demonstration only. The frozen snapshot contains 99,441 orders, 112,650 items,
and 99,441 customers; 96,478 orders have delivered status in the snapshot.
Its monetary currency is deliberately shown as undeclared source monetary units,
and timestamps retain the source's timezone-undeclared convention. The snapshot
ID and file hashes are recorded in `docs/verification/V4-S01/` and the V4
release manifest.

The tested local target is 2 GiB total: at most 10 online sessions and one
running analysis task. In the PostgreSQL 16 / Redis 8.8.2 gate, 40 concurrent
workspace reads had P95 673.534 ms, one stub analysis completed in 2.544 s, and
the second task returned 429 in 73.938 ms without durable task or usage rows.
The application Job peak was 916.852 MiB; PostgreSQL and Redis stayed healthy,
with no OOM or restart. These are local stub results, not a promise about Hong
Kong network latency or real Qwen latency.

The service is currently configured for invited password accounts with no open
registration. Production data migration is V3 multiuser data only. V1/V2
single-user `local:` history is excluded from production while local files,
locks, failed attempts, commits, tags, and accounting history remain preserved.
The 247 historical Qwen debug records, known cost, and two unknown usage records
remain byte-conserved; S01–S05 added zero real Qwen calls.

Evidence index:

- `docs/verification/V4-S01/handoff.md`: target, profile, Olist and migration rehearsal.
- `docs/verification/V4-S02/handoff.md`: fault isolation and log redaction.
- `docs/verification/V4-S03/handoff.md`: backup, new-instance restore and corruption refusal.
- `docs/verification/V4-S04/handoff.md`: two-instance matrix, 2 GiB gate and 391-test regression.
- `docs/verification/V4-S05/`: release manifest, rebuild, secret scan and rollback attempts.

V5 features are not enabled. Real public deployment, DNS/TLS acceptance, and
the separately budgeted Qwen smoke remain V4-S06 work.
