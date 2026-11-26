# Policies

A policy is one YAML document with `apiVersion: tessera/v1`. `kind` is
`AgentPolicy` or `BaselinePolicy`. Unknown fields are rejected. Names for
agents, environments, and baselines are lowercase letters, digits, and
hyphens, from 1 to 63 characters, and cannot start or end with a hyphen.

Lists in the sections below default to an empty list. An omitted block uses
the defaults of every field inside it.

Patterns are globs and are case-sensitive. `zendesk.*` matches
`zendesk.refund` and does not match `Zendesk.refund`.

## Agent policy

An agent policy grants what that agent may do. Anything that is not granted
is denied.

```yaml
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: support-triage
  environment: prod
  owner: team-support@acme.com
  risk_tier: medium
  labels: { domain: support }
spec:
  models:
    allow: ["openai/gpt-4.1*"]
    deny: ["*/*-preview"]
  tools:
    allow: ["zendesk.read_ticket"]
    require_approval:
      - name: zendesk.refund
        when:
          - arg: amount
            op: gt
            value: 100
    deny: ["shell.*"]
  data:
    allow: ["crm.customers:read"]
  agents:
    may_invoke: ["billing-agent"]
    may_be_invoked_by: ["orchestrator"]
  delegation:
    allow_on_behalf_of: true
    user_groups: ["support-staff"]
  limits:
    cost_usd_per_day: 50
    tokens_per_day: 2000000
    calls_per_minute: { "zendesk.*": 30 }
    max_steps_per_task: 25
  guardrails:
    required: ["input.prompt_injection"]
    on_missing: block
  audit:
    capture_content: false
```

### `metadata`

| Field | Default | Meaning |
| --- | --- | --- |
| `agent` | required | Agent name. The `Tessera` constructor must use this name. |
| `environment` | `dev` | Environment name. Baseline selectors compare it for equality. |
| `owner` | `null` | Who is accountable. Required when an applicable baseline sets `require_owner: true`. |
| `risk_tier` | `low` | `low`, `medium`, or `high`. |
| `labels` | `{}` | String labels. A baseline selector matches when every one of its labels is present with the same value. |

### `spec.models`, `spec.tools`, and `spec.data`

Each resource section has the same fields.

| Field | Default | Meaning |
| --- | --- | --- |
| `allow` | `[]` | Globs that grant the resource. |
| `deny` | `[]` | Globs that deny the resource. A deny match wins over an allow. |
| `require_approval` | `[]` | Globs that grant the resource and can require approval. |

A `require_approval` entry is either a glob string or a mapping:

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | required | Glob for the model, tool, or data resource. |
| `when` | `[]` | Conditions on the call arguments. All of them must match. An empty list matches every call. |

A string such as `files.delete` is the same as `{name: files.delete}`.

Each condition is:

| Field | Default | Meaning |
| --- | --- | --- |
| `arg` | required | Argument name. At least one character. |
| `op` | required | `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, or `matches`. |
| `value` | required | Value to compare against. |

`gt`, `gte`, `lt`, and `lte` require a number that is not a boolean.
`in` and `not_in` require a list. `matches` requires a regular expression,
and the argument is tested with a full match against its string form.

If the argument is missing, or the comparison cannot be performed, the
condition counts as a match. An approval rule then requires approval rather
than allowing the call through.

### `spec.agents`

| Field | Default | Meaning |
| --- | --- | --- |
| `may_invoke` | `[]` | Globs for agents this agent may call. This is the allow list for agent actions. |
| `may_be_invoked_by` | `[]` | Globs for agents that may call this one. The evaluator does not deny a call from this list. |
| `max_chain_depth` | none | Most agents in a chain that reaches this one, including this agent. Omit it for no limit. |
| `allow_reentry` | `false` | Whether this agent may appear twice in one chain. |
| `caller_constraint` | `own` | `own`, `intersect`, or a list of `{callers, constraint}` rules. The first matching rule wins. `intersect` allows an action only when every agent in the chain also grants it. |

Those grants travel in the policy bundle. The server adds them when any
agent in the same environment uses `intersect`. A chain member with no grants there
is denied with `chain_policy_unavailable`. A member that does not grant the
resource is denied with `not_granted`. `own` does not consult peer grants.

`policies/examples/chain-worker.yaml` sets a depth, forbids re-entry, and
uses a per-caller constraint list. A chain longer than `max_chain_depth` is
denied with `chain_too_deep`. A second visit is denied with `chain_reentry`
unless `allow_reentry` is true. `delegation.principal: required` denies a
hop that has no principal (`principal_required`). `forbidden` denies a call
that has one (`principal_forbidden`).

### `spec.supervision`

| Field | Default | Meaning |
| --- | --- | --- |
| `cap_to_supervisor` | `false` | When `true`, a model or tool call must also match the resolved supervisor's grant document. |
| `required` | `false` | When `true`, every governed action is denied with `unsupervised` until a supervisor is resolved. A baseline can set this too. |
| `max_unacknowledged` | none | Dead-man duration, such as `24h`. The shortest value from the agent and its baselines wins. An overdue acknowledgement suspends an autonomous agent. |

`PUT /v1/supervisors/{id}/grants` stores that document (`kind: SupervisorGrants`).
`policies/examples/supervisor-grants.yaml` allows `files.read` and one model.
A missing document denies the call with `supervisor_grants_missing`. A document
that does not list the resource denies it with `not_granted`. The server adds
the stored grants to a bundle when any agent in that environment sets the cap.
The flag is read from the agent policy.

### `spec.delegation`

| Field | Default | Meaning |
| --- | --- | --- |
| `allow_on_behalf_of` | `false` | Whether a call may name the user it is acting for. |
| `user_groups` | `[]` | When this list is non-empty, the user must belong to at least one of these groups. An empty list allows any user once delegation itself is allowed. |
| `principal` | `optional` | `required` denies a chain with no principal. `forbidden` denies a chain that has one. `optional` allows either. |

### Handoff and on-behalf-of

Two clients in one process share a run with `receive_handoff`. The worker
sees the orchestrator as the previous agent in the chain. `intersect` then
requires every agent in that chain to grant the resource.

```python
with orchestrator.task("run-1"):
    with worker.receive_handoff(orchestrator):
        worker.enforce(Action.tool("files.read"))
```

`on_behalf_of` sets the principal for the block. Offline, the user string is
trusted. In server mode, pass `user_token` and the SDK exchanges it for a
delegated runtime JWT for the length of the block. The policy still has to
allow that principal.

```python
with client.on_behalf_of("ada", ["support"], user_token=user_jwt):
    client.enforce(Action.tool("files.read"))
```

### `spec.limits`

| Field | Default | Meaning |
| --- | --- | --- |
| `cost_usd_per_day` | `null` | Daily spend cap in US dollars. The value is accepted and is not enforced. |
| `tokens_per_day` | `null` | Maximum input plus output tokens for model calls on the current UTC day. |
| `calls_per_minute` | `{}` | Map of tool glob to the maximum matching calls in a 60-second window. |
| `max_steps_per_task` | `null` | Maximum model and tool steps inside one task. |

A set numeric limit must be greater than zero. `null` means the limit is unset.

### `spec.guardrails`

| Field | Default | Meaning |
| --- | --- | --- |
| `required` | `[]` | Guardrail names. Each name has at least one dot, such as `input.prompt_injection`. |
| `on_missing` | `flag` | What to do when a required guardrail has not run: `block`, `warn`, or `flag`. |

Only a model call is denied for a missing guardrail, and only when the name
starts with `input.` and `on_missing` is `block`. `warn` and `flag` record
that the guardrail was missing and do not deny the call. A `block` guardrail
whose name does not start with `input.` is treated as `warn`.

### `spec.audit`

| Field | Default | Meaning |
| --- | --- | --- |
| `capture_content` | `false` | When `true`, decision events include the call arguments. Prompts are omitted when this is `false`. |

## Baseline policy

A baseline adds restrictions. It cannot grant resources: `models`, `tools`,
and `data` have `deny` and `require_approval` only, and no `allow`. A baseline
approval rule can require approval, and it does not grant a resource the agent
policy omitted.

```yaml
apiVersion: tessera/v1
kind: BaselinePolicy
metadata:
  name: prod-baseline
spec:
  selector:
    environment: prod
    risk_tier: ["medium", "high"]
    labels: { domain: support }
  tools:
    deny: ["shell.*"]
  limits:
    tokens_per_day: 1000000
  guardrails:
    required: ["input.prompt_injection"]
    on_missing: block
  require_owner: true
```

### `metadata`

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | required | Baseline name. |

### `spec.selector`

A baseline applies when every criterion it sets matches the agent. An empty
selector matches every agent.

| Field | Default | Meaning |
| --- | --- | --- |
| `environment` | `null` | Environment that must equal the agent's. `null` does not filter. |
| `risk_tier` | `[]` | Risk tiers the agent may have. Empty does not filter. |
| `labels` | `{}` | Labels that must all be present on the agent with the same values. |

### `spec.models`, `spec.tools`, and `spec.data`

| Field | Default | Meaning |
| --- | --- | --- |
| `deny` | `[]` | Extra deny globs, checked after the agent policy's deny list. |
| `require_approval` | `[]` | Extra approval rules. Same shape as on an agent policy, including a plain string. These rules do not grant access. |

### Other `spec` fields

`limits` and `guardrails` use the same fields and defaults as an agent policy.

| Field | Default | Meaning |
| --- | --- | --- |
| `require_owner` | `false` | When `true`, loading fails if the agent policy has no `owner`. |

When several policies set the same numeric limit, the smallest value is the
one that is enforced. The same `calls_per_minute` glob keeps the smallest
maximum. A guardrail required by more than one policy uses the strictest
`on_missing`, ordered `block`, then `warn`, then `flag`.

## Decision order

`check` and `enforce` walk this list and stop at the first step that applies.
The default is deny.

1. **Suspended.** If the client is suspended, the action is denied.
2. **Run revoked.** A revoked run is denied.
3. **Unsupervised.** `supervision.required` denies the action when no
   supervisor is resolved.
4. **Dead man.** A runtime dead-man flag denies the action.
5. **Operating window.** An action outside the window is denied.
6. **Circuit breaker.** An open breaker denies the action.
7. **Deny rules.** The first deny glob that matches the resource denies the
   action. Agent patterns are checked before baseline patterns.
8. **Chain.** A chain that is too deep, re-enters this agent, or breaks the
   principal rule is denied. See `spec.agents` and `spec.delegation`.
9. **Delegation.** If the action is on behalf of a user, it is denied when
   `allow_on_behalf_of` is false, or when `user_groups` is non-empty and the
   user is in none of those groups.
10. **Input guardrails.** A model call is denied when a required guardrail
    starts with `input.`, has `on_missing: block`, and has not run.
11. **Grant.** The resource must match an `allow` glob, or an agent
    `require_approval` glob (those grant access even before the approval step).
    Otherwise the action is denied. Baseline approval rules are not grants.
12. **Supervisor cap.** A model or tool call must match the supervisor grant
    when `cap_to_supervisor` is set.
13. **Peer grants.** With `caller_constraint: intersect`, every earlier agent
    must grant the resource.
14. **Quota scope.** A matching quota with no scope id denies the action.
15. **Limits.** A model or tool call inside a task is denied when
    `max_steps_per_task` has been reached. A model call is denied when
    `tokens_per_day` has been reached. A tool call is denied when a matching
    `calls_per_minute` pattern has been reached. `cost_usd_per_day` is not
    applied here.
16. **Effect tags.** A tag on the grant can deny the action or require approval.
17. **Approval.** The first approval rule whose glob matches, and whose `when`
    conditions match, requires approval. This includes baseline rules.
18. **Allow.** Otherwise the action is allowed under the grant from step 11.

`tessera policy check` prints the effect, the reason, the rule that matched,
and the policy version. See the [quickstart](quickstart.md) for the command.
