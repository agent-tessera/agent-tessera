# Threat model

This page says what Agent Tessera is built to stop, what it only detects, and
what it does not cover. Read it before you rely on it for an untrusted agent.

## The core assumption

The SDK enforces policy **inside the agent's process**. It checks the calls
you route through it: functions decorated with `@tessera.tool` or
`@tessera.model_call`, clients wrapped with `tessera.wrap`, and the framework
integrations. It assumes the agent's code is **not hostile**. The risk it
addresses is an agent, driven by a model that can be wrong or manipulated,
doing something its owner did not intend.

## What it is built to stop

| Threat | How it is handled |
| --- | --- |
| A model asks a governed tool or client to do something outside the policy | Default deny. The call raises `Denied` before the function or request runs. |
| An agent runs away on cost, calls, or time | Hard limits and quotas are checked in process. Hard limits never fail open, even in development fail-open mode. |
| An agent takes a risky action unnoticed | `require_approval` holds the call until a person approves it. An agent cannot approve its own request. |
| A subagent gets more access than its caller | `caller_constraint: intersect` limits a callee to what both agents may do (confused deputy). |
| Loops and runaway fan-out between agents | Chain depth and re-entry limits, quotas, and a circuit breaker. |
| Someone edits the audit log after the fact | Events are hash-chained. `tessera audit verify` reports the first event that no longer matches. |
| A forged or tampered policy | With the server, policy bundles are signed and the SDK rejects bundles with an invalid signature. A cache file that fails verification is treated as missing. |
| A stolen credential | Runtime tokens are short-lived and can be revoked. Registration tokens and admin keys are stored hashed. |
| An agent keeps running after you want it stopped | The server's kill switch and run revocation reach connected instances. |

## What it only detects

- **Calls that skip the SDK.** If code calls a provider directly, nothing
  checks it. With `detect_bypass=True` (or `TESSERA_DETECT_BYPASS=1`) the SDK
  records calls to OpenAI, Azure OpenAI, Anthropic, Gemini, and Bedrock that
  do not go through an adapter, as `ungoverned_call` events. The server counts
  them against the agent's guardrail posture. Detection covers those hosts
  only, and it does not block anything.
- **Tampering by someone who can rewrite the whole log.** The hash chain shows
  that events were changed or removed in the middle. It cannot show that the
  most recent events were deleted, or that the whole file was replaced. Ship
  events to the server or to external storage to keep a copy out of reach.

## What it does not cover

- **A hostile agent process.** Code that can run arbitrary Python in the agent's
  process can unregister hooks, patch the SDK, or read credentials from
  memory. Use container or OS isolation, a restricted service account, and
  network egress rules for agents you do not trust.
- **Prompt injection itself.** Tessera limits what an injected agent can do
  through governed calls. It does not detect or filter malicious prompts, and
  it does not inspect model output.
- **Data laundering between agents.** Agent A may read data that agent B may
  not, and pass it to B in a message. Per-agent policy cannot see this.
- **What an allowed tool does with its arguments.** Policy decides whether a
  tool may be called, and can match on argument values with conditions. It
  does not sandbox the tool's implementation.
- **Content.** Prompts and responses are not stored unless the policy sets
  `audit.capture_content: true`. When it does, redaction runs in the agent
  process before data leaves it.
- **Enterprise identity features.** SAML, SCIM, multi-tenancy, and per-team
  admin permissions are not built.

## Failure behavior

When the server cannot be reached, the SDK starts from its signed cache if it
has one. With no usable cache, `fail_mode="closed"` refuses to start and
`fail_mode="open"` allows actions. The default is `open` only when
`TESSERA_ENV=dev`. See [Fail modes](server.md#fail-modes).

## Reporting a problem

If you find a way past a control described here, report it privately. See
`SECURITY.md` in the repository root.
