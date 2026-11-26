# Integrations

Each integration is an optional extra. Importing `agent_tessera` does not
import it. If the extra is missing, the import helper raises `ImportError`
and names the extra to install.

The extras are `openai`, `anthropic`, `bedrock`, `gemini`, `litellm`,
`otel`, `langchain`, `mcp`, `openai-agents`, and `crewai`. The first six
are client adapters and OpenTelemetry. This page covers the last four,
plus how to register a custom LLM client adapter.

There is **no first-party Ollama adapter**. Use LiteLLM with `ollama/…`
model names, or `@tessera.model_call` / `Action.model("ollama/…")`.

## Custom LLM client adapters

`tessera.wrap(client)` walks a process-global adapter registry. Built-in
adapters cover OpenAI (and Azure OpenAI), Anthropic, LiteLLM, Bedrock, and
Gemini. Register your own when you have a different client shape:

```python
from agent_tessera.adapters import governed_method, register_adapter, wrap


class EchoAdapter:
    def matches(self, client: object) -> bool:
        return type(client).__name__ == "EchoClient"

    def paths(self, client: object) -> tuple[tuple[str, ...], ...]:
        return (("complete",),)

    def bind(self, tessera, client):
        def binder(_path, method):
            return governed_method(
                tessera,
                method,
                prefix="echo",
                style="tokens",
                asynchronous=False,
            )

        return binder


register_adapter(EchoAdapter())  # prepends by default
governed = wrap(tessera, echo_client)
```

`register_adapter(..., prepend=False)` appends instead. `unregister_adapter`
removes by identity. When nothing matches, `wrap` raises `ConfigError` and
points at `@tessera.model_call`.

## LangChain and LangGraph

```bash
pip install "agent-tessera[langchain]"
```

A LangGraph node is a function. Wrap it before you add it to the graph:

```python
from agent_tessera.integrations.langchain import wrap_tool


def read_file(path: str) -> str:
    return path


read_file = wrap_tool(tessera, "files.read", read_file)
```

A LangChain tool object has `name` plus `func` or `coroutine`. `govern`
replaces those callables:

```python
from agent_tessera.integrations.langchain import govern

govern(tessera, tool)
tool.func("hello")
```

The policy tool name is the object's `name`. A denial raises `Denied` before
the original function runs.

## MCP

```bash
pip install "agent-tessera[mcp]"
```

Wrap the client session. `call_tool` is checked before the session runs it.
When the session method is async, `call_tool` returns a coroutine.

```python
from agent_tessera.integrations.mcp import GovernedSession

governed = GovernedSession(tessera, session)
governed.call_tool("files.read", {"path": "notes.txt"})
```

## OpenAI Agents SDK

```bash
pip install "agent-tessera[openai-agents]"
```

`Runner.run` accepts a hooks object. `build_hooks` subclasses `RunHooks` and
enforces the tool in `on_tool_start` and the model in `on_llm_start`. A model
name without a provider prefix is recorded as `openai/<name>`.

```python
from agents import Runner
from agent_tessera.integrations.openai_agents import build_hooks

result = await Runner.run(agent, "hello", hooks=build_hooks(tessera))
```

## CrewAI

```bash
pip install "agent-tessera[crewai]"
```

`govern_tool` replaces a tool's `_run`, the same way LangChain tools are
wrapped. The policy name is the tool's `name`.

```python
from agent_tessera.integrations.crewai import govern_tool

govern_tool(tessera, tool)
tool._run("hello")
```
