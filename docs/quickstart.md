# Quickstart

This walkthrough governs two tools and a local model call. It does not call
an LLM provider and does not need an API key.

## Install

From a checkout of this repository:

```bash
uv sync --all-packages
```

The import name is `agent_tessera`. The distribution name is `agent-tessera`.

## Write a policy

Save this as `quickstart.yaml` next to your agent. The same file is checked
in at `examples/quickstart/quickstart.yaml`.

```yaml
apiVersion: tessera/v1
kind: AgentPolicy
metadata:
  agent: quickstart
  environment: dev
spec:
  models:
    allow: ["openai/gpt-4.1-mini", "anthropic/claude-*", "local/*"]
  tools:
    allow: ["files.read", "files.list"]
    require_approval:
      - files.delete
    deny: ["shell.*"]
  limits:
    max_steps_per_task: 10
```

`files.read` and `files.list` are allowed. `files.delete` asks for approval.
`shell.*` is denied. `local/*` is allowed, so a function you decorate can
stand in for a model.

Check that the file loads:

```bash
uv run tessera policy validate examples/quickstart/quickstart.yaml
```

A successful line looks like `OK examples/quickstart/quickstart.yaml (AgentPolicy quickstart)`.

## Decorate two tools

`examples/quickstart/agent.py` is the program below. `Tessera` loads the
policy beside the file, writes `.tessera/audit.jsonl` in the current
directory, and checks each decorated call.

```python
"""Quickstart agent governed by a local policy, with no model provider."""

from pathlib import Path

from agent_tessera import Tessera

tessera = Tessera("quickstart", policy=Path(__file__).with_name("quickstart.yaml"))


@tessera.tool("files.list")
def list_files(path: str) -> str:
    """Return the names in a directory, one per line."""
    return "\n".join(sorted(entry.name for entry in Path(path).iterdir()))


@tessera.tool("files.read")
def read_file(path: str) -> str:
    """Return the text of a file."""
    return Path(path).read_text(encoding="utf-8")


@tessera.model_call("local/echo")
def echo(prompt: str) -> str:
    """Return the prompt unchanged."""
    return prompt


def main() -> None:
    """Run one task and close the audit log."""
    root = Path(__file__).resolve().parent
    with tessera.task():
        print(list_files(str(root)))
        print(read_file(str(root / "quickstart.yaml")))
        print(echo("hello from quickstart"))
    tessera.close()


if __name__ == "__main__":
    main()
```

`list_files` and `read_file` are the two tools. `echo` is a model call named
`local/echo`, which matches `local/*`. The task block counts those calls
toward `max_steps_per_task`. `close` writes the stop event and closes the
audit file.

## Run

```bash
uv run python examples/quickstart/agent.py
```

The last line of output is `hello from quickstart`.

## Inspect the audit log

The log is `.tessera/audit.jsonl` in the directory you ran from. Each line is
one JSON event. The quickstart writes a `lifecycle` start, a `decision` and a
`call` for each tool and for the model, and a `lifecycle` stop. Arguments are
left out because the policy does not set `audit.capture_content`.

Verify the hash chain:

```bash
uv run tessera audit verify .tessera/audit.jsonl
```

A good log prints `OK: <n> events`.

You can also ask what one call would do without running the agent:

```bash
uv run tessera policy check \
  --policy examples/quickstart/quickstart.yaml \
  --tool files.delete
```

That command exits with status 3 and prints `effect:  require_approval`.
