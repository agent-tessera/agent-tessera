"""Wait for a server approval before deleting a file.

Set ``TESSERA_URL`` and ``TESSERA_AGENT_TOKEN``. The agent's policy must
require approval for ``files.delete`` and name a party an admin can approve.
This process blocks until that party approves or the policy timeout denies
the call.
"""

from __future__ import annotations

import os

from agent_tessera import Tessera


def main() -> None:
    """Delete one stand-in path after the server grants approval."""
    if not os.environ.get("TESSERA_URL"):
        raise SystemExit("set TESSERA_URL to the control plane")
    with Tessera("approver") as client:

        @client.tool("files.delete")
        def delete(path: str) -> str:
            """Return the path once the policy allows the delete."""
            return path

        with client.task("cleanup"):
            print(delete("notes.txt"))


if __name__ == "__main__":
    main()
