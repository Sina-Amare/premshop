"""Run exactly what CI runs, in CI's order. `python scripts/check.py`.

This exists because the gate was red on GitHub for seventeen commits while every
local run said green. The local habit had drifted from the workflow: black was run
on `apps/` instead of `.`, bandit was never run at all, and four tests leaned on
the developer's machine (a running Redis, relay credentials in `.env`). One command that mirrors `.github/workflows/ci.yml`
closes the first two gaps; the third is closed by checking the run on GitHub after
every push — a local pass is evidence, the workflow run is the verdict.

`--no-audit` skips pip-audit, which needs the network and is the slow one.
"""

from __future__ import annotations

import os
import subprocess
import sys

PY = sys.executable

# Keep in step with .github/workflows/ci.yml. If a step is added there, add it here.
STEPS: list[tuple[str, list[str]]] = [
    ("ruff", [PY, "-m", "ruff", "check", "."]),
    ("black", [PY, "-m", "black", "--check", "."]),
    ("mypy", [PY, "-m", "mypy", "apps", "config"]),
    ("pytest", [PY, "-m", "pytest", "-q"]),
    ("pip-audit", [PY, "-m", "pip_audit", "--skip-editable"]),
    ("bandit", [PY, "-m", "bandit", "-q", "-r", "apps", "config", "-x", "*/tests/*"]),
]


def main() -> int:
    skip = {"pip-audit"} if "--no-audit" in sys.argv else set()
    # bandit prints the offending source line; on a cp1256 console a Persian string
    # crashes its reporter before it can report anything. UTF-8 mode avoids that.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    failed: list[str] = []
    for name, command in STEPS:
        if name in skip:
            print(f"--- {name}: skipped")
            continue
        print(f"--- {name}", flush=True)
        if subprocess.run(command, env=env, check=False).returncode != 0:  # noqa: S603
            failed.append(name)
    print("\nFAILED: " + ", ".join(failed) if failed else "\nall checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
