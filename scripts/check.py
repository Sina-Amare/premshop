"""Run exactly what CI runs. `python scripts/check.py`.

This exists because the gate was red on GitHub for seventeen commits while every
local run said green. The local habit had drifted from the workflow: black was run
on `apps/` instead of `.`, bandit was never run at all, and four tests leaned on
the developer's machine (a running Redis, relay credentials in `.env`). One command that mirrors `.github/workflows/ci.yml`
closes the first two gaps; the third is closed by checking the run on GitHub after
every push — a local pass is evidence, the workflow run is the verdict.

`--no-audit` skips pip-audit, which needs the network and is the slow one.
Installing packages is not mirrored: `npm ci` and Playwright's Chromium are set
up once (docs/development.md), as `uv sync` is.

pytest is most of the wait. CI runs it as a job of its own beside the linters,
so it runs beside them here too, once the CSS it renders with is built; its
output is held and printed when it ends.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

PY = sys.executable
# On Windows npm is npm.cmd, which a bare "npm" in an argv list does not find.
NPM = shutil.which("npm") or "npm"

# Keep in step with .github/workflows/ci.yml. If a step is added there, add it here.
STEPS: list[tuple[str, list[str]]] = [
    ("ruff", [PY, "-m", "ruff", "check", "."]),
    ("black", [PY, "-m", "black", "--check", "."]),
    ("mypy", [PY, "-m", "mypy", "apps", "config"]),
    ("css", [NPM, "run", "css"]),
    ("migrations", [PY, "manage.py", "makemigrations", "--check", "--dry-run"]),
    ("pytest", [PY, "-m", "pytest", "-q"]),
    ("pip-audit", [PY, "-m", "pip_audit", "--skip-editable"]),
    ("bandit", [PY, "-m", "bandit", "-q", "-r", "apps", "config", "-x", "*/tests/*"]),
]


def main() -> int:
    skip = {"pip-audit"} if "--no-audit" in sys.argv else set()
    # bandit prints the offending source line; on a cp1256 console a Persian string
    # crashes its reporter before it can report anything. UTF-8 mode avoids that.
    # REQUIRE_BROWSER as in CI: a browser test skipped here would pass here only.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "REQUIRE_BROWSER": "1"}
    failed: list[str] = []

    def run(name: str, command: list[str]) -> None:
        if name in skip:
            print(f"--- {name}: skipped")
            return
        print(f"--- {name}", flush=True)
        if subprocess.run(command, env=env, check=False).returncode != 0:  # noqa: S603
            failed.append(name)

    steps = dict(STEPS)
    run("css", steps.pop("css"))
    with tempfile.TemporaryFile() as out:
        tests = subprocess.Popen(  # noqa: S603
            steps.pop("pytest"), env=env, stdout=out, stderr=subprocess.STDOUT
        )
        for name, command in steps.items():
            run(name, command)
        print("--- pytest", flush=True)
        if tests.wait() != 0:
            failed.append("pytest")
        out.seek(0)
        sys.stdout.buffer.write(out.read())
        sys.stdout.flush()
    print("\nFAILED: " + ", ".join(failed) if failed else "\nall checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
