# V4 local dependency stack

This Compose file starts the V4 dependency stack only. It does not start the
V3 SQLite website and it does not expose PostgreSQL or Redis beyond loopback.

Before starting, provide the variables from `.env.v4.example` through a private
environment file or the process environment. Do not put real passwords in Git,
the repository, or chat. Validate first:

```powershell
docker compose --env-file .env.v4.private -f compose.yml config
```

Only after validation should the operator start the stack:

```powershell
docker compose --env-file .env.v4.private -f compose.yml up -d
docker compose --env-file .env.v4.private -f compose.yml ps
```

The PostgreSQL volume is the durable V4 database volume. Redis state is
rebuildable coordination/session state; its volume does not replace the
PostgreSQL backup. Stop/restart commands and volume identifiers must be added
to the V4 evidence record, and `down -v` is prohibited during development.
