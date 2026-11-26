# agent-tessera

Default-deny permissions and a tamper-evident audit log for AI agents,
whatever LLM they call.

Write what an agent may do in a short YAML file. Agent Tessera checks every
model call, tool call, and agent-to-agent call against it, in your process,
with no network round trip, and writes each decision to a hash-chained audit
log you can verify.

```bash
pip install agent-tessera
```

Python 3.11 or newer. No server and no API key needed.

## Quickstart

Save a policy as `policy.yaml`. Anything it does not allow is denied:

```yaml
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: demo
spec:
  tools:
    allow: ["files.read"]
```

Save an agent as `agent.py`:

```python
from pathlib import Path

from agent_tessera import Denied, Tessera

tessera = Tessera("demo", policy="policy.yaml")


@tessera.tool("files.read")
def read_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


@tessera.tool("shell.run")
def run_shell(command: str) -> str:
    raise RuntimeError("never reached")


with tessera.task():
    print(read_file("policy.yaml").splitlines()[0])
    try:
        run_shell("rm -rf /")
    except Denied as err:
        print("blocked:", err)

tessera.close()
```

Run it and verify the audit log:

```bash
python agent.py
tessera audit verify .tessera/audit.jsonl
```

```text
apiVersion: tessera/v1
blocked: deny tool 'shell.run': not_granted
OK: 5 events
```

The denied function never ran. Edit any event in `.tessera/audit.jsonl` and
verification fails.

To govern an LLM client instead of a function, wrap it:

```python
from openai import OpenAI

client = tessera.wrap(OpenAI())  # needs: pip install "agent-tessera[openai]"
```

## Extras

The base install depends on `httpx`, `pydantic`, `pyjwt`, and `pyyaml` only.
Provider and framework support are extras: `openai`, `anthropic`, `bedrock`,
`gemini`, `litellm`, `otel`, `langchain`, `mcp`, `openai-agents`, and
`crewai`. For example:

```bash
pip install "agent-tessera[anthropic,otel]"
```

## The `tessera` command

`tessera policy validate` and `tessera policy check` load a policy and
evaluate one action. `tessera audit verify` checks an audit log. With the
control plane (`pip install agent-tessera-server`), `tessera server dev` starts
a local server and web console.

## What it does not do

It enforces policy on the calls you route through it, inside your process. It
does not sandbox the process or defend against hostile code running in it.
Read the
[threat model](https://github.com/agent-tessera/agent-tessera/blob/main/docs/threat-model.md)
before relying on it.

## Links

- [Source and full README](https://github.com/agent-tessera/agent-tessera)
- [Documentation](https://github.com/agent-tessera/agent-tessera/tree/main/docs)
- [Policy reference](https://github.com/agent-tessera/agent-tessera/blob/main/docs/policies.md)
- [Examples](https://github.com/agent-tessera/agent-tessera/tree/main/examples)
- [Changelog](https://github.com/agent-tessera/agent-tessera/blob/main/CHANGELOG.md)

Apache 2.0.
