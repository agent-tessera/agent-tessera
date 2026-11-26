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
