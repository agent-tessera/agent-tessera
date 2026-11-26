"""The checked-in examples run offline and match the README."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUICKSTART = ROOT / "examples" / "quickstart" / "agent.py"
BLOCKED_CALL = ROOT / "examples" / "blocked_call"


def test_quickstart_example_exits_zero(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(QUICKSTART)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "hello from quickstart" in result.stdout
    assert (tmp_path / ".tessera" / "audit.jsonl").is_file()


def test_readme_blocked_call_matches_example() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agent = (BLOCKED_CALL / "agent.py").read_text(encoding="utf-8")
    policy = (BLOCKED_CALL / "policy.yaml").read_text(encoding="utf-8")
    assert f"```python\n{agent}```" in readme
    assert f"```yaml\n{policy}```" in readme


def _tessera(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-c",
            "import sys; from agent_tessera.cli import main; sys.exit(main(sys.argv[1:]))",
            *args,
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def test_blocked_call_example_denies_and_audit_detects_tampering(tmp_path: Path) -> None:
    for name in ("agent.py", "policy.yaml"):
        (tmp_path / name).write_text(
            (BLOCKED_CALL / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    result = subprocess.run(
        [sys.executable, "agent.py"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "blocked: deny tool 'shell.run': not_granted" in result.stdout

    log = tmp_path / ".tessera" / "audit.jsonl"
    verified = _tessera("audit", "verify", str(log), cwd=tmp_path)
    assert verified.returncode == 0, verified.stderr
    assert "OK: 5 events" in verified.stdout

    log.write_text(
        log.read_text(encoding="utf-8").replace('"effect":"deny"', '"effect":"allow"'),
        encoding="utf-8",
    )
    tampered = _tessera("audit", "verify", str(log), cwd=tmp_path)
    assert tampered.returncode != 0
    assert "hash mismatch" in tampered.stdout + tampered.stderr
