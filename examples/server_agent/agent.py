"""Agent that takes its policy from a control plane.

Set ``TESSERA_URL`` to the server. In development mode that is enough: the
server registers this agent. Otherwise set ``TESSERA_AGENT_TOKEN`` to a
registration token for the agent named on the command line (default
``helper``).
"""

from __future__ import annotations

import os
import sys

from agent_tessera import Tessera
from agent_tessera.types import Action


def main() -> None:
    """Print decisions for three tools and close the client."""
    if not os.environ.get("TESSERA_URL"):
        raise SystemExit("set TESSERA_URL to the control plane")
    name = sys.argv[1] if len(sys.argv) > 1 else "helper"
    with Tessera(name) as client, client.task("demo"):
        for tool in ("files.read", "files.delete", "shell.exec"):
            decision = client.check(Action.tool(tool))
            print(f"{tool}: {decision.effect} ({decision.reason})")


if __name__ == "__main__":
    main()
