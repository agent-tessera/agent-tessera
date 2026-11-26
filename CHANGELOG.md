# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.8.0] - 2026-11-25

First public release. Agent Tessera is open source under Apache 2.0 with no
commercial edition.

### Added

- Package metadata for PyPI: license, authors, project URLs, and classifiers
  for `agent-tessera` and `agent-tessera-server`.
- `examples/blocked_call`: the README demo, with a test that runs it and
  checks that audit tampering is detected.
- `CODE_OF_CONDUCT.md`, a release workflow with PyPI trusted publishing, and
  Dependabot and pull-request templates.

### Changed

- README rewritten around a two-minute quickstart, with an explicit section on
  what the SDK does and does not protect against.
- Versions of both packages, the console, and the OpenAPI document are 0.8.0.
- The PyPI pages for both packages were rewritten with `pip install`
  instructions, a quickstart that runs from the installed package, and
  absolute links.

### Fixed

- Console: the sidebar fills the window height and stays in place while the
  page scrolls; it used to end below the last link.
- Console: tables no longer add a long blank scroll area below the page.
- Console screenshots in the docs were retaken.

## [0.7.1] - 2026-11-19

### Added

- Public adapter registry for `wrap`: `Adapter` Protocol,
  `register_adapter` / `unregister_adapter`, and documented use of
  `governed_method` for custom LLM clients. Built-in OpenAI, Anthropic,
  LiteLLM, Bedrock, and Gemini adapters register at import (behavior
  unchanged). No first-party Ollama adapter; use LiteLLM or
  `@tessera.model_call`.

## [0.7.0] - 2026-11-10

### Breaking

- Classic Jinja operator pages under `/ui` are removed. The React console at
  `/app` is the only admin UI. Sign-in, logout, and OIDC move to `/login`,
  `/logout`, `/oidc/start`, and `/oidc/callback`. Legacy `/ui/login`,
  `/ui/logout`, and `/ui/oidc/*` redirect for one release; other `/ui/*`
  paths redirect to `/app/overview`.
- Default OIDC callback is `{issuer}/oidc/callback` (was
  `{issuer}/ui/oidc/callback`). Update IdP and `TESSERA_OIDC_REDIRECT_URL`
  if you relied on the old default.

### Changed

- Console login/logout URLs, Playwright helpers, noscript link, Helm OIDC
  docs, and server docs point at the short auth routes.
- Policy template YAML files live under `tessera_server/policy_templates/`
  (still served by the API).

## [0.6.2] - 2026-11-10

### Added

- Run documents expose `principal` (bound from the first event that names one);
  governance spans include principal from the paired decision.
- `GET /v1/runs` accepts `environment` and the Traces console forwards the shell
  filter.
- Baseline preview APIs: `GET /v1/baselines/{name}/agents`,
  `POST /v1/baselines/{name}:validate`, `POST /v1/baselines/{name}:dry-run`;
  Settings → Baseline detail shows inheritors plus Validate / Dry-run.

### Changed

- Console router uses a string-preserving search codec so all-digit `?span=`
  values are not JSON-quoted; approval deep-links use router `search`.

## [0.6.1] - 2026-11-10

### Changed

- Console UX polish for 0.6.1 surfaces: Fleet activity sits beside Needs
  attention / Recent runs, Access footprint uses toned count chips, Owners and
  Audit share panel chrome with status badges, Insights Quotas shows usage
  bars, and Webhooks use Active/Inactive chips. The shell auto-collapses the
  sidebar below 768px so phone-width layouts no longer overflow horizontally.
  Insights always resolves agent names for Quotas (even with All environments).
- Playwright `e2e/ux-polish.spec.ts` covers those surfaces (desktop + mobile
  overflow); CI `e2e` runs it with the full suite and uploads screenshots.

### Added

- Console Settings → Owners directory: create users/teams, rename, activate
  or deactivate, and open the agents list filtered by owner.
- Agent detail Overview can edit metadata (owner, risk, autonomy, labels,
  supervisors, description) via `PATCH /v1/agents/{id}`.
- Agent detail Access tab shows declared / observed / unused / denied
  grants from `GET /v1/agents/{id}/footprint/access`.
- Console Settings → Audit: chain verification and hash-chained log;
  Overview audit failures deep-link to `/settings/audit`.
- Console Settings → Webhooks can edit URL, events, active flag, and secret
  via `PUT /v1/webhooks/{id}`.
- `GET /v1/revocations` lists recent revocations for admins (newest first,
  filterable by `kind` and `agent_id`).
- Console Overview shows a Fleet activity strip that polls recent
  revocations every 12 seconds.
- `GET /v1/quotas` returns usage vs ceilings for agent policies
  (`tokens_per_day` and named quotas).
- Console Insights adds a Quotas tab listing ceilings vs usage with links
  to agents.
- E2E spike seed includes a team owner, `tokens_per_day`, access-footprint
  events, and revocation rows for Owners / Access / Quotas / Fleet demos.
- Playwright smoke covers Owners, Audit, Access, Quotas, and Fleet activity;
  OpenAPI types include `GET /v1/revocations` and `GET /v1/quotas`.

### Changed

- CI is cheaper on pull requests: lint, one Python version, docs, and the
  console `web` job. Draft PRs do not run Actions. Postgres, the Python
  matrix, lowest-deps, benchmarks, Docker, Helm, packaging, Playwright e2e,
  and dependency audit run on `main` (or manual dispatch).
- Dependabot is monthly with `open-pull-requests-limit: 0` so it does not
  open update PRs while the private free-plan Actions quota is exhausted.
- Agents list accepts `owner_id` in the URL search and forwards it to
  `GET /v1/agents`.

## [0.6.0] - 2026-11-10

### Breaking

- None. Events from SDKs older than 0.6 omit `span_id` / `parent_span_id` and
  appear under **Unparented** on the trace detail page; upgrade agents to 0.6
  for full waterfall parenting.

### Added

- Classic `/ui` operator pages show a banner: "You are using the classic UI.
  Open the console." with a link to `/app`.
- Console Playwright accessibility and performance suite: axe-core on every
  `/app` route in light and dark (fails on serious/critical), M6 checks for
  initial JS ≤ 250 KB gzip and explorer 10k-row scroll without long tasks over
  200 ms, API seed script at `web/tests/e2e/seed.py`, and CI job `e2e`
  (Node 22, Playwright Chromium). DataTable scroll regions are keyboard
  focusable; Policy YAML editor exposes an accessible name; Approvals list
  options are no longer nested interactive controls; light muted foreground
  meets AA contrast on table headers.
- Console onboarding (`/app/welcome`): 4-step stepper (create agent → policy
  template cards with YAML preview → connect with live first-connection poll →
  first event with a link to its governance trace). Uses
  `GET /v1/policy-templates` (now includes `yaml` for preview) and
  `POST /v1/agents/{id}/policy:from-template`. Overview first-run card and the
  `/app/onboarding` stub redirect to `/app/welcome`. Empty e2e seed runs with
  `dev=True` so Playwright can finish via `grant_type=dev`.
- Console Settings (`/app/settings/*`): Alerts (create/delete with 422 field
  mapping), Webhooks (create/delete and Send test via
  `POST /v1/webhooks/{id}:ping`), Baselines (list, CodeMirror YAML view,
  replace, delete), Custody (create/replace/delete with password inputs;
  plaintext and ciphertext never echoed), and Admin keys (list, create shown
  once with copy, revoke ending that key's sessions). Nested section nav under
  `/app/settings`.
- Console Insights (`/app/insights`): Spend (by agent/model with stacked
  bars over the selected range), Denials (by agent or rule), Footprint
  (tokens, calls, cost, energy/CO₂e labelled *estimate*), and Posture
  board (weakest component, permissive). Shared time-range presets and
  the shell environment filter. Totals come from
  `/v1/dashboards/spend`, `/v1/dashboards/denials`, `/v1/footprint`, and
  `/v1/posture`.
- Console Approvals inbox (`/app/approvals`): split view (list + detail),
  status/agent filters, keyboard `j`/`k`/`a`/`d`/`Enter`, decide with
  comment, deny confirm, and a run link that opens the trace with the
  waiting span highlighted (`?span=`). `GET`/`POST` approve and deny on
  `/v1/approvals/{id}` accept the admin session cookie (with CSRF) in
  addition to bearer credentials.
- Console Agent Connect tab (`/app/agents/{id}?section=connect`): registration
  token options (reusable, expires in), token shown once with copy plus
  `TESSERA_URL` / `TESSERA_AGENT_TOKEN` env lines and a Python bootstrap
  snippet, and a 2 s poll that flips from “Waiting for the first
  connection…" to "Connected: <short id>" after an instance registers.
  Playwright mints a token in the UI then registers via `POST /v1/token`.
- Console Agent Policy tab (`/app/agents/{id}?section=policy`): CodeMirror YAML
  editor (lazy-loaded), lint markers from `POST …/policy:validate` /
  failed save, side-by-side stored vs draft diff before save, dry-run panel
  (`POST …/policy:dry-run`), and the L5 permissive warning. Playwright covers
  invalid YAML (PolicyError at the reported line, no save) and dry-run deny
  + rule against the seeded palette-probe policy.
- Console Agent detail (`/app/agents/{id}`): Overview (L5 tiles, posture card,
  permissive warning, recent runs), Activity (explorer embedded with fixed
  `agent:` filter), Traces (runs for this agent), Instances (table + revoke
  with confirm), and Connect. State-aware lifecycle actions
  (activate/suspend/retire) use ConfirmDialog + optional reason; retired
  agents show none. `POST /v1/agents/{id}:activate|suspend|retire` and
  `POST /v1/instances/{id}:revoke` accept an optional JSON `reason`.
  Playwright covers retired (no actions) and instance revoke.
- Console Agents list (`/app/agents`): DataTable (name, environment, status,
  autonomy, risk, owner, posture score, permissive chip, last seen, denials
  24 h) with status/environment/autonomy facets, shell environment filter,
  and Create agent dialog matching `POST /v1/agents` (422 field errors and
  409 name conflict on the name field). Row opens the agent detail stub.
  Playwright covers duplicate-name create conflict.
- Console Trace detail (`/app/traces/{run}`): span Drawer (decision,
  outcome, timings, effects, approval link, raw events, optional "Open in
  tracing tool" from `GET /v1/me` `trace_url` / `TESSERA_SERVER_TRACE_URL`),
  Effects and Events tabs, unparented pre-0.6 event list, and Revoke run
  with confirm + reason (`POST /v1/runs/{id}:revoke`). Playwright covers
  revoke→audit and the external link show/hide paths.
- Console Trace detail (`/app/traces/{run}`): header (run id, status,
  principal, supervisor, started, duration), virtualized waterfall (depth
  indent, time-axis bars, approval hatching, denial markers, agent
  swimlanes, wheel/drag zoom, fit, collapsible subtrees), Tree tab with
  agent → span nesting and counts, 3 s live refetch while
  `status: running`, and `?span=` highlight from paste-jump. Playwright
  covers the seeded two-agent screenshot and a 5,000-span scroll long-task
  budget.
- Console Traces list (`/app/traces`): runs table (status, started, duration,
  agents, spans, denials, approvals, errors, principal, supervisor) with
  shareable filters (status, agent, has denials, has errors, time range),
  cursor pagination, and row navigation to the stub detail route. Playwright
  covers the "Has denials" filter against the seeded deny-span run.
- Console Explorer live tail: `GET /v1/events/stream` subscription (cookie
  fetch stream + `Last-Event-ID`), auto-pause on scroll or row open, "N new
  events" resume that flushes the buffer once, brief row highlight, and
  `lag` → list refetch. URL `live` flag mirrors the toggle. Playwright uses
  the e2e background `live.tail` producer.
- Console Explorer table: stacked histogram with brush zoom (`since` /
  `until` in the URL), virtualized DataTable with column visibility in
  `localStorage`, event detail Drawer (`JsonView`, filter/copy/trace
  actions), cursor infinite scroll, and client-side NDJSON export of loaded
  rows. Playwright covers brush → URL, drawer JSON, and export line count.
- Console Explorer (`/app/explorer`): query bar with removable chips and
  structured token autocomplete (`agent:`, `type:`, `effect:`, `status:`,
  `rule:`, `run:`, `span:`, `kind:`), facet sidebar with counts from
  `GET /v1/events/facets`, time range / selected event / live flag in the
  URL, and a basic results list. Playwright covers filter → URL → reload
  round-trip and facet count parity with the API.
- Console Overview page: first-run onboarding card (stub `/app/onboarding`),
  KPI row with 24h sparklines and period deltas, Needs attention (pending
  approvals, denial spikes, orphaned/dormant, low posture, audit verify), and
  recent runs. Playwright covers spike and empty-seed paths.
- Console command palette (`⌘K` / `Ctrl-K` / `/`): debounced `GET /v1/search`,
  recent items, static Go to… / create / approvals / live-tail / theme
  actions, span and run paste-jump, `?` keyboard map, and `g` section
  chords. Stub `/agents/$id` and `/traces/$runId?span=` routes carry
  selection for later detail work. Playwright covers agent search and span
  paste.
- Console app shell: collapsible sidebar, top bar with breadcrumbs and
  environment filter (`?environment=`), pending-approvals badge (10 s
  refresh), actor menu (theme + CSRF logout), auth gate via `GET /v1/me`
  with login `next` return, stub routes, and a 404 page. Playwright covers
  the unauthenticated → login → `/app/agents` → logout round-trip.
- Console design system: light/dark tokens (teal accent, zinc neutrals),
  vendored Manrope and IBM Plex Mono fonts, `ThemeToggle`, and shared
  components (`DataTable`, badges, `PageHeader`, empty/error states, and more)
  with a dev-only `/app/_kitchen-sink` showcase.
- Decision, call, approval, and effect events carry a `span_id` (16 hex) so a
  decision pairs with its call. Nested governed tools set `parent_span_id` to
  the enclosing call. Denied decisions keep a span and produce no call event.
- Cross-agent hops propagate `X-Tessera-Parent-Span` via `chain_headers` /
  `http_client`, and `Tessera.handle_call` binds it on the callee. Call events
  record the agent `chain` from in-process handoff.
- When OpenTelemetry is enabled, `tessera.call` starts at decision time and
  decision/call events reuse its `span_id` and add `trace_id`. Without OTel,
  events omit `trace_id`.
- Migration `0012` indexes `events` with `run_id`, `span_id`, `parent_span_id`,
  `kind`, `resource`, `effect`, `status`, and `rule` (filled on ingest and
  backfilled from existing payloads).
- `/v1` admin routes accept the `tessera_admin` session cookie after a bearer
  key. Cookie callers must send `X-CSRF-Token` on non-GET requests; `GET /v1/me`
  returns `csrf` for cookie sessions. Login `next` accepts only `/app/…` and
  `/ui/…` paths.
- `web/` scaffolds the React console (Vite, TanStack Router/Query, Tailwind,
  shadcn button primitive, Vitest, Playwright config, license allow-list). CI
  gains a Node 22 `web` job; Dependabot watches npm under `web/`.
- `GET /v1/events` and `GET /v1/events/facets` query indexed events for the
  console explorer (filters, cursor pagination, top facet counts).
- `GET /v1/events/histogram` returns stacked bucket counts for the explorer.
- `GET /v1/events/stream` streams matching events over SSE with resume and lag.
- `GET /v1/runs/{id}/trace` returns a governance span tree; `GET /v1/runs`
  gains status/agent/time/denial/error filters and counts.
- `GET /v1/search` finds agents, runs, approvals, spans, resources, and rules
  for the command palette (never searches payloads).
- Console parity JSON routes: `GET /v1/posture`, agent instances, policy
  validate/dry-run/from-template, policy templates, admin keys, and webhook
  ping.
- The server serves the React console at `/app` (SPA fallback, security
  headers, immutable `/app/assets` caching). The wheel includes `web/dist`.
- Console API client: `npm run gen:api` dumps OpenAPI into `web/openapi.json`
  and generates `web/src/api/schema.d.ts`; the typed fetch wrapper sends
  credentials, attaches CSRF from `GET /v1/me` on mutating calls, redirects
  401 to `/ui/login`, maps 422 `details` to field errors, and clears CSRF on
  `csrf_failed`. TanStack Query key factories live under `web/src/api/keys.ts`.
- Docs: console walkthrough with screenshots in `docs/server.md`,
  `docs/tracing.md` (span ids, cross-agent parents, OTel), and
  `web/README.md` screenshot regeneration notes.

### Changed

- `GET /` and post-login (key or SSO) redirect to the React console
  (`/app`; default landing `/app/overview`). Classic `/ui` paths stay
  available.

## [0.5.0] - 2026-11-09

### Security

- Admin UI sign-in now creates a server-side session. The `tessera_admin`
  cookie holds only an opaque `tss_` value; it used to hold the raw admin key
  after key login and an unsigned `oidc:<subject>` value after SSO, which a
  browser could forge to get an admin UI session while SSO was configured.
  Sessions end after `TESSERA_SERVER_SESSION_IDLE` (default `8h`) without use,
  `TESSERA_SERVER_SESSION_MAX` (default `24h`) after sign-in, on log out, and
  when the admin key is revoked or rotated. Migration `0011` adds
  `admin_sessions`.
- Admin UI POST forms require a CSRF token (hidden `csrf` field, or
  `X-CSRF-Token` for htmx). A mismatch returns 403. Login and development
  loopback stay exempt.

### Added

- Request validation `422` responses include `details` with `field` and
  `reason` for each pydantic error. Request values are never echoed.
- OpenAPI documents the package version and tags every `/v1` operation.
- `GET /v1/me` returns the authenticated admin (`actor`, `method`,
  `admin_key_id`).
- `GET /v1/approvals` accepts `limit` and `cursor` and returns `next_cursor`.
- Admin UI Jinja filters `money` and `compact`, shared macros in
  `_macros.html`, and first-party `app.js` helpers for copy buttons and
  confirm dialogs (pages still work when the script is blocked).
- Admin UI primary nav is Overview, Agents, Approvals, Activity, and
  Insights, with Settings and Log out on the right. Activity and Insights
  redirects, shared sub-navs, breadcrumbs, titled pages, and flash codes.
- Destructive admin UI forms use `data-confirm` dialogs with an optional
  reason written to the audit payload. `/ui/search` and the Ctrl/⌘-K quick
  switcher find agents, runs, and approvals.
- `tessera login` / `logout` / `status` store an admin profile under
  `~/.config/tessera/credentials.yaml` (mode `0600`). `TESSERA_URL` and
  `TESSERA_ADMIN_KEY` still override the file.
- Agent detail header shows status-aware lifecycle actions, overview tiles
  (denials / tool calls / cost / pending approvals), posture components as
  points out of max, and a warning when a policy allow list contains `*`.
- `/ui/settings/alerts` creates and deletes alert rules with the same
  validation as `POST /v1/alerts`. `/ui/alerts` redirects there.
- `/ui/settings/webhooks` lists, creates, and deletes endpoints and can
  Send test a signed `webhook.ping`. Endpoint secrets are write-only.
- `/ui/baselines/{name}` views, replaces, and deletes a baseline. `/ui/custody`
  creates, replaces, and deletes provider keys without showing plaintext.
- `/ui/settings/admin-keys` creates keys (shown once), lists them, and revokes
  a key together with its UI sessions.
- `tessera approvals ls|show|approve|deny` and `tessera events tail`
  (`--agent`, `--type`, `--follow`) use the stored admin profile.
- Agent detail splits Activity and Instances into routed tabs under
  `/ui/agents/{id}/activity` and `/instances`.
- Agent Policy tab edits YAML in place with Validate (diff, no save), Save,
  and Dry run against the effective policy.
- Agent Connect tab creates registration tokens (shown once with env and
  Python snippets) and polls for the first instance connection.
- Approvals inbox adds Why and Run columns, Load more, a live nav badge,
  keyboard shortcuts, and a richer detail page.
- Activity audit stream uses select filters (agent name, type, effect, since),
  expandable JSON rows, and ok/failed verify-chain banners. Live pages add an
  Agent column and a Pause control. Run detail shows participants by agent
  name and chronological live-style event rows.
- Insights `/ui/posture` lists agents by score ascending with coverage,
  weakest component, and a permissive chip. Overview links to the board.
- Empty-org Overview links to `/ui/welcome` onboarding: create an agent, pick
  a shipped policy template (`read-only`, `support-with-approvals`,
  `dev-permissive`), connect, then open the agent after the first event.
- Admin UI templates use shared chips, effect chips, and empty-state macros
  consistently; pages no longer render raw `True` / `False` / `None`.
- Docs first hour and server README cover the regrouped nav, sessions/CSRF,
  Settings pages, onboarding, and CLI login / approvals / events tail.

### Changed

- Signing in to the admin UI lands on `/ui/overview` instead of `/ui/agents`.
- Key sign-in and log out are audited as `admin_login` and `admin_logout`;
  the SSO `admin_login` row now also records `method` and `session`.
- `GET /v1/audit` lists events by `received_at`, then id. The admin audit page
  shows newest first.

### Fixed

- Live activity rows read `action.kind` / `action.resource` for decision
  events, so Resource is no longer blank.
- The empty alerts page no longer links to a broken `docs/server.md` path.

### Breaking

- Admin UI cookies issued before the upgrade are no longer accepted. Users
  sign in again once.

## [0.4.1] - 2026-11-09

### Added

- The control-plane first-hour walkthrough lists overview, approvals, live,
  dashboards, and the agent posture block after the server agent demo, with
  expected allow / approval / deny outcomes and a link to the offline
  quickstart.
- Helm upgrade notes for a 0.3 database: reuse the existing KEK and database,
  migrate through Alembic head `0010`, OIDC optional. `check-template.sh`
  renders the external-database default, bundled-Postgres trial, and
  existing-Secret shapes.
- `/ui/custody` lists stored provider-secret id, name, and provider. Creating
  or revealing a secret stays on the API; the page never shows plaintext.
- `tessera server rotate-admin-key --name NAME` creates a break-glass admin
  key, revokes earlier keys with the same name, prints the new key once, and
  audits only the key ids.

### Fixed

- The Postgres CI job also runs the chain-intersect and dead-man SDK
  end-to-end tests. The live-server fixture uses `TESSERA_TEST_DATABASE_URL`
  when set.
- Dashboard and agent pages use the same empty-state copy as other admin
  pages when a window or posture block has no data. Agent events show
  relative times when present.
- `GET /v1/dashboards/summary` spend and denial totals use the inclusive
  `[now - 24h, now]` window, matching the dashboard UI pages. Future events
  are not counted.
- Live-server test restarts dispose the previous SQLite engine, tear down the
  restarted uvicorn thread, and retry event flush after recovery so the
  outage spool test stays stable under lowest dependency versions.
- Server tests dispose every temporary SQLAlchemy engine in a `finally` block
  so Postgres runs no longer leak connections on assertion failures.

### Changed

- Dashboard and posture pages use the Signal theme (sub-nav, chart panel,
  posture tiles). Charts stay server-rendered SVG; zero series still omit
  the chart.
- Admin UI visual redesign ("Signal" light theme): vendored Manrope and IBM
  Plex Mono fonts, charcoal/teal shell with branded top bar, clearer
  overview tiles, approvals Approve/Deny hierarchy, and panel tables. No
  route or governance behaviour change.

## [0.4.0] - 2026-11-07

### Security

- An admin key still authorizes the API when OIDC is required. Treat it as
  break-glass. The server does not log that key, the OIDC client secret, or
  a provider API key.

### Added

- The docs cover admin SSO, workload identity, delegation handoff, key
  custody, and the Helm chart.
- A Helm chart at `deploy/helm/agent-tessera` installs the control plane.
  An external database is the default. Set `postgresql.enabled` to try the
  bundled Postgres subchart. Ingress is off until `ingress.enabled` is true.
- `POST /v1/custody/issue` returns a provider key to a runtime JWT for at
  most 120 seconds. With `TESSERA_KEY_CUSTODY=1`, the SDK fetches that key
  before a governed model call, sets it on the wrapped client, and removes
  it when the call returns. The key is not written to the audit log.
  Offline mode does not fetch a key.
- `POST`, `GET`, `PUT`, and `DELETE /v1/custody/secrets` store provider API
  keys encrypted with the server key. Responses and the list include the
  name and provider, never the plaintext.
- `/ui/dashboards` shows inventory, spend, and denials. The nav links to
  Dashboards. A bar chart is included when a value is above zero.
- `GET /v1/footprint` adds `energy_j`, `co2e_g`, and `estimate: true`.
  The numbers come from `pricing/energy.json`. An unknown model makes the
  energy and carbon fields null.
- `GET /v1/agents/{id}/footprint/access` reports declared grants, observed
  resources, unused grants, and `not_granted` denials for the last 30 days.
- `GET /v1/dashboards/summary` reports inventory, orphaned agents, dormant
  agents, pending approvals, and the last 24 hours of spend and denials.
  `GET /v1/dashboards/spend` and `GET /v1/dashboards/denials` total a
  `from`/`to` window. Spend groups by agent or model.
- The agent page and the overview show the posture score and coverage.
  The agent page also lists each score component.
- `GET /v1/agents/{id}/posture` returns a 0–100 score for the last seven
  days: an active owner, required guardrails declared on the agent policy,
  the share of required input guardrails that ran, the denial rate, and
  whether any `ungoverned_call` was uploaded. Probe points stay at 0.
- `supervision.required` denies every governed action with `unsupervised`
  when no supervisor is resolved. A baseline can require it too.
  `supervision.max_unacknowledged` suspends an active agent when that long
  passes without `POST /v1/agents/{id}/supervisor-ack`. The suspension writes
  the same revocation as an admin suspend, and a `supervisor.digest` webhook
  when one is configured. An acknowledgement resets the timer.
- `supervision.cap_to_supervisor` requires a model or tool call to match the
  resolved supervisor's grant document. `GET` and `PUT
  /v1/supervisors/{id}/grants` store that YAML. A missing document denies
  the call with `supervisor_grants_missing`. A document that does not list
  the resource denies it with `not_granted`.
- A token request may send `bound_supervisor` (`user:` or `group:`). That
  value is stored on the instance. The first event that names a supervisor,
  or `Tessera.task(..., supervisor=)`, stores it on the run and later events
  do not replace it. The supervisor for a run is the run binding, then the
  instance binding, then the first static supervisor.
- A signed policy bundle includes other agents' allow lists when any agent
  in that environment uses `caller_constraint: intersect`. Bundles stay
  without those grants when nobody does. Offline mode does not embed them.
- With `caller_constraint: intersect`, a callee may only take an action that
  every agent in the chain also grants. The grants come from `peers` on the
  policy bundle. A missing peer denies with `chain_policy_unavailable`.
  `own` does not consult those grants. A per-caller list uses the first
  matching rule.
- The evaluator denies a call chain that exceeds `agents.max_chain_depth`
  (`chain_too_deep`), re-enters an agent when `allow_reentry` is false
  (`chain_reentry`), hops with no principal when `delegation.principal` is
  `required` (`principal_required`), or carries a principal when it is
  `forbidden` (`principal_forbidden`). `receive_handoff` records the caller
  on that chain.
- Agent policies accept `agents.max_chain_depth`, `agents.allow_reentry`,
  `agents.caller_constraint` (`own`, `intersect`, or a per-caller list), and
  `delegation.principal` (`required`, `optional`, or `forbidden`). An invalid
  constraint is a policy error. See `policies/examples/chain-worker.yaml`.
- `Tessera.http_client()` attaches `Authorization` and `Tessera-Run` on each
  request. `Tessera.receive_handoff(caller)` joins the caller's run id and
  principal for an in-process hop.
- In server mode, `on_behalf_of(..., user_token=)` exchanges that user token
  for a delegated runtime JWT and restores the agent token when the block
  exits. Offline mode still trusts the user string.
- Admins can sign in with an OpenID Connect provider. The session cookie is
  `oidc:<sub>`, and an `admin_login` audit row records the issuer and subject.
  `TESSERA_OIDC_REQUIRED=1` rejects the admin-key form.
- The login page links to SSO when it is configured, and hides the admin-key
  form when SSO is required. `TESSERA_OIDC_ADMIN_GROUP` requires that value
  in the ID token `groups` claim. A missing group returns 403.
- `POST /v1/token` accepts `grant_type: workload`. A configured binding
  exchanges an OIDC assertion for the same runtime JWT as registration.
  A wrong audience returns 401.
- A Kubernetes service-account token can bootstrap an agent. The binding
  issuer is configurable; `https://kubernetes.default.svc` is the usual
  in-cluster value. The server docs describe the projected token.
- `POST /v1/token` accepts `grant_type` `token-exchange` and the RFC 8693
  URN. The issued JWT sets `sub` to the user, `sub_iss` to the identity
  provider, and nests the agent in `act`.

## [0.3.1] - 2026-11-06

### Added

- The server docs' first hour follows the overview navigation, including
  Approvals and Live.
- The admin UI suite fails if the pending-approval badge or a live
  fragment route is missing.
- Agent, approval, run, and live pages share one relative-time format:
  `just now`, `5m ago`, `3h ago`, and `2d ago`.
- The baselines page lists stored names and confirms a successful upload.
- The audit page marks event types and explains an empty log. The alerts
  page lists each rule's threshold and points at the API when there are none.
- The runs list shows status, first and last seen, participant and event
  counts, and links to the live page and the effects table.
- Live agent and run pages poll a row fragment every two seconds. A
  Refresh link reloads the page without JavaScript.
- The admin pages load a vendored copy of htmx 2.0.4. No script is loaded
  from a CDN.
- The approvals inbox defaults to pending requests, can approve or deny
  from the list, and marks a request stale after half of its timeout. The
  navigation shows the pending count.
- A registration token can be copied, and the text stays selectable without
  JavaScript. A policy upload shows a success or error banner.
- An agent page shows a status chip, risk tier, autonomy, and a 24 hour
  strip of denials, tool calls, cost, and pending approvals. Sections link
  to the live page and that agent's approvals.
- The agents list filters by status, environment, and name. Each row shows
  a status chip, a relative last-seen time, the pending approval count, and
  the first 12 characters of the policy digest.
- The overview page counts agents by status, pending approvals, runs in
  the last 24 hours, and denials in the last 24 hours. An empty org shows
  a first-run checklist.
- The admin UI opens on an overview page. The navigation is Overview, Agents,
  Approvals, Runs, Live, Audit, Alerts, and Baselines.

## [0.3.0] - 2026-11-06

### Breaking

- With a control plane configured, a call that requires approval waits for
  the server by default. Set `approval_wait=False` to raise
  `ApprovalRequired` immediately.

### Added

- The README lists every optional extra. Installing `agent-tessera` still
  pulls in only `httpx`, `pydantic`, `pyjwt`, and `pyyaml`.
- The server stores every uploaded event. It does not sample events or drop
  them by age.
- The admin navigation includes Approvals and Live. Empty approval, alert,
  and live pages say so, and error text uses the alert style.
- Quota checks and effect classification stay within the existing p99
  evaluation budget.
- `examples/approvals/agent.py` waits for a server approval before a tool
  runs. The live agent page, an approved delete, and an instance quota
  denial are covered together.
- `govern_tool` enforces a CrewAI tool's `_run` before the original method
  runs. Install the `crewai` extra for CrewAI itself.
- `build_hooks` subclasses the OpenAI Agents SDK `RunHooks` and enforces
  tools and models before the runner continues. Install the `openai-agents`
  extra for that SDK.
- `GovernedSession` enforces MCP `call_tool` before the underlying session
  runs. Install the `mcp` extra for the MCP SDK.
- `wrap_tool` and `govern` enforce LangChain tools and LangGraph node
  functions. Install the `langchain` extra for LangChain itself.
- `wrap` governs a Google GenAI client's `models.generate_content` calls.
  Install the `gemini` extra to import `google.genai`.
- `wrap` governs a Bedrock runtime client's `converse` and `invoke_model`
  calls. The model name is read from `modelId`. Install the `bedrock` extra
  to import boto3.
- `wrap` governs a LiteLLM client's `completion` and `acompletion` calls.
  Install the `litellm` extra to import LiteLLM itself.
- The admin UI has live pages for an agent and a run. Each shows the 50
  most recent call and decision events and refreshes every five seconds.
- `detect_bypass=True` or `TESSERA_DETECT_BYPASS=1` records an
  `ungoverned_call` when HTTP calls a known model host outside an adapter.
  The call is not blocked. The default leaves `httpx` unchanged.
- Alert rules watch footprint totals. A threshold emits `monitor.alert`, and
  `action: suspend` also suspends the agent. `GET /ui/alerts` lists the rules.
- The optional `otel` extra exports `tessera.evaluate`, `tessera.enforce`, and
  `tessera.call` spans. Set `TESSERA_OTEL_ENDPOINT` or call
  `agent_tessera.otel.configure()`. Without the extra, tracing is a no-op.
- `GET /v1/footprint` totals input and output tokens, cost, model calls,
  tool calls, and denials from stored events. The agent page shows the
  same totals.
- `record_effect` stores an opaque reference on the current run. The server
  lists those references at `GET /v1/runs/{id}/effects`, and the run page
  shows the same refs.
- In server mode the SDK posts an approval and waits until it is decided.
  A timeout denies the action with reason `approval_timeout`.
  `approval_wait=False` raises `ApprovalRequired` with `approval_id`.
  A granted wait records `approval_wait_ms` on the following call event.
- The admin UI lists approvals and can approve or deny a pending request.
  Webhook endpoints receive `approval.requested` and `approval.resolved` as
  signed JSON. Delivery retries three times, and a failure is audited.
  `TESSERA_WEBHOOK_URL` registers one catch-all endpoint at startup.
- The server stores approval requests and offers them to the policy route.
  An admin key, or the party currently offered the request, can approve or
  deny it. `forbid_self_approval` rejects the requesting principal. When a
  step expires, the server asks the next party, and the last expiry sets
  status `timeout`.
- In-flight model and tool calls, run step, cost, and duration caps, and
  concurrent runs are enforced while a call is in progress. When the circuit
  breaker opens, the client and the server suspend the agent or revoke the
  run. The server counts uploaded denial decisions; a `circuit_open` decision
  does not count again.
- The SDK counts instance quotas locally and leases other named quotas from
  the server. Before a model call it reserves `ceil(estimate * 1.1) +
  max_output_tokens` and replaces that hold with the measured usage.
- The server leases named quotas as well as the daily token budget.
  `POST /v1/quotas/leases` accepts `metric` (default `tokens`), `quota_name`
  (default `tokens_per_day`), `resource`, `principal`, `run_id`, and
  `requested`. The response adds `remaining` and still includes `day` and
  `limit`. A quota with `on_exhausted: suspend` suspends the agent when the
  budget is empty.
- Policies can declare quotas, per-call and run caps, a circuit breaker,
  approval routing, effect tags, and an operating window. A token or cost
  quota that applies to models requires `limits.per_call.max_output_tokens`.
- Evaluation enforces operating windows, effect tags, quotas, per-call and
  run caps, and the circuit breaker. Baselines tighten those controls.
  A supervised or autonomous agent with no approval handler is denied.
- Call events record the run id, an empty chain, the principal, model
  `streamed` and `finish_reason`, and for tools the effect tags plus argument
  and result sizes. Approval wait is not recorded yet.
- Usage events include `cost_usd` from a built-in model price table.
  `TESSERA_PRICE_TABLE` or `Tessera(..., price_table=path)` replaces it.
  An unknown model costs 0 and emits one `cost.unknown_model` event per
  process.

## [0.2.1] - 2026-11-05

### Added

- The admin UI uses a light stylesheet served at `/ui/static/style.css`.
- `docs/server.md` has a first-hour walkthrough: start the development
  server, register an agent, run the example, and suspend it from the UI.
- The admin UI can register an agent, upload a policy or baseline, and create
  a reusable registration token. A failed form shows the API error on the
  same page.

### Fixed

- `tessera server dev` flushes the URL and the admin key before the server
  starts, so both lines show up when the process is piped or backgrounded.

## [0.2.0] - 2026-11-05

### Breaking

- `Tessera.policy` may be `None` while server mode has no policy bundle yet.

### Changed

- Importing `agent_tessera` loads each public name the first time it is used.
- SQLite connections are closed by the thread that opened them.

### Added

- `tessera agents apply` makes a local registry or a control plane match a
  registry file. `docs/registry.md` describes the local registry, the agents
  commands, and moving that file to a server.
- `tessera agents` can register and manage agents on a control plane when
  `--server` or `TESSERA_URL` is set. `GET /v1/owners` accepts an exact
  `email` filter.
- `tessera agents` registers agents, lists them, and changes their lifecycle
  in the local registry.
- A local registry records agents, their policies, and their lifecycle in one
  YAML file. Offline clients refuse an agent that is missing, draft, or
  retired, and a running client follows suspend and activate from that file.
  `tessera policy validate` accepts a registry file.
- Guides for running the control plane and for `tessera footprint`, and an
  example agent that takes its policy from a server.
- `tessera footprint` compares a policy's grants with a local audit log and
  reports what was used, what was unused, and what was denied because it was
  not granted.
- A Docker image and a Compose file run the control plane with Postgres. The
  image listens on port 8700 as a non-root user.
- `tessera server dev` migrates a local database and serves it on
  `127.0.0.1`. A client that has only a server URL registers itself when
  that server is in development mode.
- Admin UI at `/ui`. An admin key signs in through a cookie, and the pages
  list agents, runs, and recent events. Forms suspend, activate, or retire an
  agent, revoke an instance or a run, create a registration token, and verify
  the audit chain.
- Server mode takes a daily token lease so every instance of an agent shares
  `tokens_per_day`. Model calls are denied with `agent:limits.tokens_per_day`
  once the local grant is used up, and closing the client reports that usage.
- Server mode follows the revocation stream. Suspending an agent, revoking
  its instance, or revoking a run denies the affected actions, and activating
  the agent allows them again. A dropped stream resumes from the last event.
- Server mode uploads audit events to the control plane, keeps a disk spool
  when the server cannot be reached, and sends the stop event when the client
  closes. A local audit file is written only when a path is configured.
- Server mode. A client with a server URL registers, caches a signed policy
  bundle, refreshes its credential, and can start from that cache when the
  server cannot be reached.
- Daily token leases at `POST /v1/quotas/leases`, so a token budget is shared
  by every instance of an agent.
- Run listing and revocation, and a server-sent revocation stream that
  resumes with `Last-Event-ID`.
- Event ingestion at `POST /v1/events`, with duplicate detection, run
  tracking, and admin reads for the event list, the audit chain, and a
  chain verification check.
- Agent policies, baselines, and signed policy bundles. A bundle can be
  fetched again unchanged with `If-None-Match`.
- `POST /v1/token` exchanges a registration token or a current runtime JWT
  for a short-lived credential and records the instance.
- Registry for owners and agents, including lifecycle changes, registration
  tokens, instance revocation, and a hash-chained audit log of admin actions.
- Admin API keys and `tessera server` commands to migrate the database,
  create an admin key, rotate signing keys, and serve the API.
- Signing keys encrypted at rest, a public JWKS document, and runtime JWTs.
- Control-plane settings, database schema, and a `/healthz` check.
- Ed25519 signing keys, JWKS loading, and signed policy bundles.
- Decision reasons for a revoked run, a missing policy, and fail-open.

## [0.1.0] - 2026-11-05

### Added

- Quickstart, policy reference, and examples for a local agent and an OpenAI
  client.
- `tessera` command to validate policy files, check one action, and verify an
  audit log.
- Governed proxy for Anthropic clients. Message calls are checked
  against policy, and token usage is recorded when the provider returns it.
- Governed proxy for OpenAI and Azure OpenAI clients. Chat and response
  calls are checked against policy, and token usage is recorded when the
  provider returns it.
- Decorators for tools, guardrails, and model calls, plus token usage
  recording. Each allowed call writes a timed `call` audit event.
- `Tessera` client that loads a local policy, checks and enforces actions,
  counts steps, and writes a hash-chained audit log.
- Per-client task and guardrail context, a shared principal, and counters for
  daily tokens and tool calls in a 60-second window.
- Append-only audit log with a hash chain that can be verified after the
  file is reopened or written from several threads.
- In-process policy evaluation: deny, delegation, input guardrails, limits, and
  approval, in that order, with a default of deny.
- Effective policy merge: applicable baselines constrain an agent policy into
  one set of allow, deny, approval, limit, and guardrail rules.
- Case-sensitive glob and condition matching for policy resources.
- YAML loading for agent and baseline policies, with example policies under
  `policies/examples/`.
- Pydantic models for agent and baseline policies, including resource rules,
  conditions, limits, guardrails, and baseline selectors.
- Public SDK errors (`TesseraError`, `ConfigError`, `PolicyError`,
  `GovernanceError`, `Denied`, `ApprovalRequired`) and core types (`Principal`,
  `Action`, `Decision`, `ActionKind`, `Effect`, `Reason`).
- Project scaffolding: `agent-tessera` SDK and `agent-tessera-server` packages,
  linting, type checking, tests, and API documentation tooling.
