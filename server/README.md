# agent-tessera-server

The control plane for [Agent Tessera](https://pypi.org/project/agent-tessera/).
It registers agents, issues short-lived signed credentials, signs and
distributes policy bundles, collects audit events, tracks guardrail posture,
and serves a web console at `/app`.

The SDK enforces policy inside each agent's process and works without this
server. Run the server when you want to manage a fleet of agents from one
place.

```bash
pip install agent-tessera-server
```

This also installs `agent-tessera` and adds `tessera server` commands. Python
3.11 or newer.

## Try it locally

```bash
tessera server dev
```

This creates a SQLite database in `.tessera/server.db`, listens on
`http://127.0.0.1:8700` (loopback only), and prints an admin key for the API
and the `tessera` command. Open `http://127.0.0.1:8700/app`; in dev mode the
console needs no sign-in from the same machine. Point an agent at the server
by setting `TESSERA_URL=http://127.0.0.1:8700`.

## Run it for real

Set these environment variables:

- `TESSERA_SERVER_DATABASE_URL`: a SQLAlchemy URL for SQLite or Postgres.
  For Postgres, install `agent-tessera-server[postgres]`.
- `TESSERA_SERVER_ISSUER`: the public base URL, with no trailing slash.
- `TESSERA_SERVER_KEK`: a base64-encoded 32-byte key that protects signing
  keys at rest.
- `TESSERA_SERVER_ORG`: the organization slug. Defaults to `default`.

Then migrate, create an admin key, and serve:

```bash
tessera server migrate
tessera server create-admin-key --name ops
tessera server serve --host 0.0.0.0 --port 8700
```

`serve` refuses to start until the database is migrated. `tessera server
rotate-keys` rotates the policy signing key.

The repository also has a Docker Compose file (server plus Postgres) and a
Helm chart.

## Links

- [Server guide](https://github.com/agent-tessera/agent-tessera/blob/main/docs/server.md):
  registration, the kill switch, approvals, fail modes, and the console
- [Threat model](https://github.com/agent-tessera/agent-tessera/blob/main/docs/threat-model.md)
- [Source](https://github.com/agent-tessera/agent-tessera)
- [Changelog](https://github.com/agent-tessera/agent-tessera/blob/main/CHANGELOG.md)

Apache 2.0.
