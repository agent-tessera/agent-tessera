# agent-tessera-server

Control plane for Agent Tessera. It registers agents, issues short-lived
credentials, signs policy bundles, stores audit events, and serves the React
console at `/app` (`GET /` and post-login land there). The classic Jinja UI
at `/ui` remains for one release. Sign-in creates a server-side session;
POST forms require CSRF.

The SDK evaluates policy in process. This package is the server the SDK
calls when `TESSERA_URL` is set. Install it next to `agent-tessera` and use
the `tessera server` command. Operators can also `tessera login` and use
`tessera status`, `tessera approvals`, and `tessera events tail`.

## Development server

```bash
uv run tessera server dev
```

This migrates `.tessera/server.db`, prints `http://127.0.0.1:8700`, and
prints an admin key. It binds `127.0.0.1` only. A client with
`TESSERA_URL` set, and no registration token, can register itself while this
mode is on. The [first hour](../docs/server.md#first-hour) starts at
`http://127.0.0.1:8700/` (console at `/app/overview`; classic `/ui` still
works), registers an agent, runs `examples/server_agent/agent.py`, then
walks Approvals, Activity, Insights, and Settings.

## Configured server

Set these variables, then migrate and serve:

- `TESSERA_SERVER_DATABASE_URL` — SQLAlchemy URL. SQLite or Postgres.
- `TESSERA_SERVER_ISSUER` — public base URL, with no trailing slash.
- `TESSERA_SERVER_KEK` — base64-encoded 32-byte key for signing keys at rest.
- `TESSERA_SERVER_ORG` — organization slug. Defaults to `default`.

```bash
uv run tessera server migrate
uv run tessera server create-admin-key --name ops
uv run tessera server serve --host 127.0.0.1 --port 8700
```

`serve` refuses to start until the database is migrated. `rotate-keys`
creates a new signing key and prints its id.

## Docker Compose

`.env.example` lists `TESSERA_SERVER_ISSUER`, `TESSERA_SERVER_KEK`, and
`POSTGRES_PASSWORD`. From the repository root:

```bash
docker compose up --build
```

The server container listens on port 8700. See `docs/server.md` for
registration, tokens, and the SDK.
