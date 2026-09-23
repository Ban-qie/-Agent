# V4 production stack

`compose.yml` is the production stack: Caddy, the Gunicorn application,
PostgreSQL 16, and Redis 8.8.2. Production publishes only ports 80 and 443;
the application, PostgreSQL, and Redis remain on private container networks.
`compose.local.yml` is a diagnostic overlay. It publishes PostgreSQL and Redis
on host loopback and replaces the public Caddy ports with local HTTPS on
`127.0.0.1:58443` using an internal test certificate.

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

For an isolated local migration or HTTP diagnostic, add the overlay to both
validation and startup commands:

```powershell
docker compose --env-file .env.v4.private -f compose.yml -f compose.local.yml config
docker compose --env-file .env.v4.private -f compose.yml -f compose.local.yml up -d
```

Local S01/S02 validation must inject a clearly fake Qwen key and a disposable
session secret through the process environment. It must not copy a production
key into `.env.v4.private`, and it must verify a zero model-call delta.

The PostgreSQL volume is the durable V4 database volume. Redis state is
rebuildable coordination/session state; its volume does not replace the
PostgreSQL backup. Stop/restart commands and volume identifiers must be added
to the V4 evidence record, and `down -v` is prohibited during development.

Both dependencies join the internal application network and a second bridge
used only for host loopback publication. PostgreSQL and Redis remain bound to
`127.0.0.1`; the loopback bridge must never be paired with a wildcard host
binding or a public firewall rule.

The 2 GiB target budget reserves 256 MiB for PostgreSQL and 96 MiB for Redis.
The future application container is capped at 1 GiB and the TLS proxy at
64 MiB, leaving about 544 MiB for the OS, Docker, page cache, and bounded
overhead. These are release limits, not performance claims; the local 2 GiB
purchase gate has passed, while the full V4-S04 request matrix remains required.

The production runtime is frozen to one Gunicorn 23.0.0 process using the
gthread worker with four HTTP threads. Caddy 2.10.2 terminates TLS and is the
only public listener on ports 80 and 443. The application accepts exactly one
proxy hop from Caddy's fixed private address. PostgreSQL, Redis, and port 5567
must not be published publicly. `Caddyfile` and `gunicorn.conf.py` are parsed
as part of S01 validation before either is used on the target.

Never run `docker compose down -v` for this project. Named volumes contain the
database, runtime state, and proxy certificate state; backup and restore steps
must be used when a clean isolated instance is required.
