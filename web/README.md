# Tessera console (`web/`)

React operator console served at `/app` (default landing from `/` in v0.6).
Python owns the API and classic `/ui` sign-in; this package is the SPA.
Operator docs: [Control plane](../docs/server.md#console) and
[Governance traces](../docs/tracing.md).

## Prerequisites

- Node.js 22 LTS
- npm 10+

## Commands

```bash
npm ci
npm run dev          # Vite on :5173, proxies /v1 /ui /healthz → :8700
npm run check        # lint, format, typecheck, unit tests, licenses, build
npm run build        # writes server/src/tessera_server/web/dist
npm run test         # Vitest
npm run e2e          # Playwright against a local non-dev server (see below)
npm run screenshots  # write docs/assets/console/*.png (opt-in; not in e2e)
npm run gen:api      # dump OpenAPI → openapi.json + src/api/schema.d.ts
```

## Layout

- `src/routes/` — TanStack Router file routes (authenticated shell under `_console`)
- `src/api/` — typed fetch client, query keys, generated OpenAPI types
- `src/components/` — design-system and shell chrome
- `src/components/ui/` — shadcn primitives (copied, not linked)
- `src/styles/` — design tokens and Tailwind entry
- `e2e/` — Playwright specs; `e2e/serve.py` boots a non-dev server with a
  temp DB and writes `e2e/.admin-key`
- `tests/e2e/seed.py` — API seeder for a running `tessera server` (admin key +
  registration token + `POST /v1/events` via SDK `remote_event`)
- `tests/e2e/screenshots.spec.ts` — captures overview / explorer / agents /
  approvals / trace PNGs into `docs/assets/console/`
- `scripts/check-licenses.mjs` — allow-list MIT / ISC / BSD / Apache-2.0
- `scripts/gen-api.mjs` — regenerates `openapi.json` and `schema.d.ts`

## Playwright

Auth and later e2e specs hit the **built** console served by FastAPI at
`/app` (not the Vite dev server), so cookie sessions and CSRF match
production. Build first, then run Playwright (it starts `e2e/serve.py`):

```bash
npm run build
npx playwright install chromium   # once per machine
npm run e2e
```

Defaults: server on `127.0.0.1:18700` (avoids clashing with a local
`tessera server` on `:8700`), `PLAYWRIGHT_BASE_URL=http://127.0.0.1:18700/app/`.
Override with `TESSERA_E2E_PORT` / `PLAYWRIGHT_BASE_URL`.

CI job `e2e` builds the console, installs Chromium, and runs the full suite
(including axe on every route in light/dark, M6 performance smokes, and
`e2e/ux-polish.spec.ts` layout / overflow checks). On success CI uploads
`e2e/artifacts/ux-polish/*.png`; on failure it uploads Playwright traces.

Docs screenshots (committed under `docs/assets/console/`) are opt-in so CI
does not rewrite them:

```bash
npm run build
npm run screenshots
```

To seed a local `tessera server` (or `tessera server dev`) via the public API
instead of `e2e/serve.py`:

```bash
uv run python web/tests/e2e/seed.py \
  --base-url http://127.0.0.1:8700 \
  --admin-key "$TESSERA_ADMIN_KEY" \
  --out web/e2e/.seed.api.json
```

## License check

```bash
npm run check:licenses
# Expected failure on the GPL fixture:
LICENSE_CHECK_START=scripts/fixtures/gpl-package npm run check:licenses
```
