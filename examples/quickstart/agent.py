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
