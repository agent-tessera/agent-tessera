# Footprint

`tessera footprint` compares the grants in an effective policy with a local
audit log. It answers four questions: what was granted, what was used, which
grants were never used, and what was refused because nothing granted it.

```bash
uv run tessera footprint \
  --policy policies/examples/support-triage.yaml \
  --baseline policies/examples/prod-baseline.yaml \
  --audit .tessera/audit.jsonl \
  --days 30
```

`--policy` is an agent policy. Repeat `--baseline` for each baseline file.
The command builds the same effective policy the SDK would enforce. `--days`
is the window ending now, and it must be at least 1. The default is 30 days.
`--json` prints the same report as JSON with keys `declared`, `observed`,
`unused`, and `denied`.

The text report has those four sections.

- **declared** lists allow patterns, then approval patterns that themselves
  grant the action. An approval entry that comes only from a baseline does
  not grant anything, so it is not listed. Each row has a kind (`model`,
  `tool`, `data`, or `agent`), the pattern, and the rule id.
- **observed** counts resources from two places: decision events whose effect
  is `allow` or `require_approval`, and `call` events. The same resource is
  summed across both. Each row is a kind, a resource, and a count.
- **unused** is a declared pattern that matched no observed resource of the
  same kind.
- **denied** counts decision events whose reason is `not_granted`. A deny
  rule is not included here.

Events whose `agent` field names a different agent are skipped. An event
with no `agent` field is included. An event with no timestamp, or a
timestamp that cannot be parsed, is skipped. Only events at or after the
start of the window are counted.

A missing audit file, a line that is not JSON, or a line that is not an
object prints the error and exits 1.

Server mode writes a local audit file only when `audit_path` or
`TESSERA_AUDIT_PATH` is set. Point `--audit` at that file. The command does
not read the server's audit API.
