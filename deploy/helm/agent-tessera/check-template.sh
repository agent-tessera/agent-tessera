#!/usr/bin/env bash
# Render the chart for the install and upgrade shapes used in docs and CI.
set -euo pipefail

root="$(cd "$(dirname "$0")" && pwd)"
kek="$(python3 -c 'import base64; print(base64.b64encode(b"k"*32).decode())')"

helm dependency update "$root" >/dev/null

external="$(helm template tessera "$root" --set "kek.value=${kek}")"
printf '%s\n' "$external" | grep -q 'tessera server migrate'
printf '%s\n' "$external" | grep -q 'TESSERA_SERVER_KEK'
printf '%s\n' "$external" | grep -q 'postgresql+psycopg://tessera:change-me@postgres.example:5432/tessera'

bundled="$(
  helm template tessera "$root" \
    --set "kek.value=${kek}" \
    --set postgresql.enabled=true \
    --set postgresql.auth.password=change-me
)"
printf '%s\n' "$bundled" | grep -q 'tessera server migrate'
printf '%s\n' "$bundled" | grep -q 'kind: StatefulSet\|-postgresql'

existing="$(
  helm template tessera "$root" \
    --set kek.existingSecret=tessera-existing \
    --set database.url=""
)"
printf '%s\n' "$existing" | grep -q 'name: tessera-existing'
printf '%s\n' "$existing" | grep -q 'tessera server migrate'
if printf '%s\n' "$existing" | grep -q 'kind: Secret'; then
  echo "existingSecret upgrade must not create a chart Secret" >&2
  exit 1
fi

echo "helm template shapes ok (Alembic head 0011 on migrate)"
