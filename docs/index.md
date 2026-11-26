# Agent Tessera

Identity, permissions, and governance for AI agents, whatever LLM they call.

The SDK evaluates a YAML policy in process and writes a hash-chained audit
log. Wrap a function, or an OpenAI or Anthropic client, and each call is
checked before it runs. A control plane can register the agent, sign the
policy bundle, and store the same events.

The Python API may still change.

- [Quickstart](quickstart.md)
- [Policies](policies.md)
- [Control plane](server.md), including the [console](server.md#console) and a [first hour](server.md#first-hour) on the development server
- [Governance traces](tracing.md)
- [Footprint](footprint.md)
- [Integrations](integrations.md)
- [Threat model](threat-model.md)
- [SDK reference](reference/sdk.md)
