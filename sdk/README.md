# agent-tessera

Python SDK for Agent Tessera: identity, permissions, and governance for AI agents,
whatever LLM they call.

`Tessera` loads a YAML policy, checks model and tool calls in process, and
appends a hash-chained audit log. Decorate your own functions, or wrap an
OpenAI or Anthropic client. The `tessera` command validates a policy, checks
one action, and verifies an audit log.

The quickstart needs no API key:

```bash
uv run python examples/quickstart/agent.py
uv run tessera audit verify .tessera/audit.jsonl
```

That program is `examples/quickstart/agent.py`. It decorates `files.list` and
`files.read`, then calls `@tessera.model_call("local/echo")`. An OpenAI
client is wrapped in `examples/openai_agent/agent.py`.
