# Agent Tessera

Helm chart for the control plane. It installs a Deployment, a Service, a
ConfigMap, and a Secret for the key-encryption key. Ingress is off until
`ingress.enabled` is true.

Use an external Postgres database. That is the default: set `database.url`
to a `postgresql+psycopg://` URL you run yourself. The chart can start a
Postgres subchart when `postgresql.enabled` is true. That subchart is only
for a trial. Do not use it as the database you intend to keep.

## Required values

`kek.value` is the base64 of 32 bytes (`TESSERA_SERVER_KEK`). To use a Secret
you already manage, set `kek.existingSecret` instead. That Secret must
contain `TESSERA_SERVER_KEK` and `TESSERA_SERVER_DATABASE_URL`.

`issuer` is the public base URL of this server, without a trailing slash.

`image.repository` and `image.tag` select the image built from
`server/Dockerfile`.

## OIDC

Set `oidc.issuer`, `oidc.clientId`, and `oidc.clientSecret` to turn on admin
SSO. `oidc.required` rejects the admin-key form. `oidc.adminGroup` limits
login to that group. The client secret is stored in the chart Secret, not
the ConfigMap.

## Ingress

```yaml
ingress:
  enabled: true
  host: tessera.example.com
```

## Install

```bash
helm dependency update deploy/helm/agent-tessera
helm template tessera deploy/helm/agent-tessera --set kek.value="$TESSERA_SERVER_KEK"
```

Replace the sample `database.url` password before you install the chart.

## Upgrade from 0.3

Keep the same Postgres database and the same key-encryption key. Point the
chart at that database with `database.url`, or set `kek.existingSecret` to a
Secret that already holds `TESSERA_SERVER_KEK` and
`TESSERA_SERVER_DATABASE_URL`. The chart does not generate a KEK.

OIDC settings stay optional: leave `oidc.issuer` empty, or set
`oidc.issuer` / `oidc.clientId` / `oidc.clientSecret` when you want admin SSO.

The Deployment runs `tessera server migrate` before `serve`. That applies
Alembic revisions through head `0012` (event index columns for the console).
Confirm the revision after migrate with the database:

```bash
# alembic_version.version_num should be 0012
```

Upgrading through `0011` signs out every admin UI user once: earlier cookies
are not sessions, so the browser is sent to the login page. `0012` backfills
indexed columns on existing `events` rows from their JSON payload.

Render both shapes without a cluster:

```bash
helm dependency update deploy/helm/agent-tessera
kek="$(python3 -c 'import base64; print(base64.b64encode(b"k"*32).decode())')"
# External database (default)
helm template tessera deploy/helm/agent-tessera --set "kek.value=${kek}"
# Bundled Postgres trial
helm template tessera deploy/helm/agent-tessera \
  --set "kek.value=${kek}" \
  --set postgresql.enabled=true \
  --set postgresql.auth.password=change-me
# Existing Secret (reuse KEK and database URL from 0.3)
helm template tessera deploy/helm/agent-tessera \
  --set kek.existingSecret=tessera-existing \
  --set database.url=""
```
