# Governance traces

Every governed call gets a span id so the control plane can pair the
decision with the call and draw a waterfall across agents. Spans are for
display and correlation only. They are never used for authorization.

## Span ids

When the SDK makes a decision for a call it assigns a `span_id`: 16
lowercase hex characters. The `decision` event and the matching `call`
event share that id. A denied call still gets a `span_id` on the decision
and produces no call event. `approval` and `effect` events emitted during
a call carry the same id.

`parent_span_id` is the innermost enclosing governed call in the same
process (a tool called from inside another tool or agent call). When there
is no local parent, the SDK uses the inbound `X-Tessera-Parent-Span`
header if it is valid 16-hex; otherwise the field is null.

In the console, open **Traces**, pick a run, and expand the waterfall.
Spans whose parent is missing from the run are roots. Events without
`span_id` (SDKs older than 0.6) appear under **Unparented** in time order.

## Cross-agent parents

Outbound hops attach the current span with
`agent_tessera.delegation.chain_headers` (also used by
`Tessera.http_client()`):

```python
from agent_tessera.delegation import chain_headers

headers = chain_headers(
    access_token=token,
    run_id=run_id,
    parent_span_id=current_span_id,
)
# headers include X-Tessera-Parent-Span when the span is valid
```

The callee binds that header before enforcing work:

```python
with worker.handle_call(request.headers):
    worker.enforce(Action.tool("files.read"))
```

A malformed header is ignored. Call events also record `chain`: the list of
agent ids for the handoff (outermost first). The seeded two-agent e2e run
shows agent B's root span parented to A's agent-call span in the waterfall.

## OpenTelemetry

Install the optional extra and point at a collector:

```bash
pip install "agent-tessera[otel]"
export TESSERA_OTEL_ENDPOINT=http://127.0.0.1:4318/v1/traces
```

```python
from agent_tessera.otel import configure

configure()  # or configure("http://collector:4318/v1/traces")
```

When OTel is configured, the SDK starts a `tessera.call` span at decision
time. Decision and call events reuse that span's id as `span_id` and add
`trace_id` (32 hex). Without OTel, events omit `trace_id`.

Set `TESSERA_SERVER_TRACE_URL` on the server to a template such as
`https://tracing.example/traces/{trace_id}?span={span_id}`. When both
`trace_id` and the template are present, the console span drawer shows
**Open in tracing tool**. `GET /v1/me` exposes the template as `trace_url`.

## Compatibility

Agents on SDK versions older than 0.6 still upload events. Those events
lack `span_id` / `parent_span_id`, so the trace API lists them in
`unparented` and the waterfall cannot parent them. Upgrade the SDK to 0.6
or later for full nesting.
