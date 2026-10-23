"""Run exactly what CI runs. `python scripts/check.py`.

This exists because the gate was red on GitHub for seventeen commits while every
local run said green. The local habit had drifted from the workflow: black was run
on `apps/` instead of `.`, bandit was never run at all, and four tests leaned on
the developer's machine (a running Redis, relay credentials in `.env`). One command that mirrors `.github/workflows/ci.yml`
closes the first two gaps; the third is closed by checking the run on GitHub after
every push — a local pass is evidence, the workflow run is the verdict.

Each step prints one line, and its full output only when it fails; pytest adds its
summary line. `--verbose` prints everything as it runs. `--no-audit` skips pip-audit,
which needs the network and is the slow one. `--docs` is for a change that touches
only `docs/` and Markdown files: it runs the written-voice test alone, and falls back
to the whole gate when anything else has changed. Installing packages is not mirrored:
`npm ci` and Playwright's Chromium are set up once (docs/development.md), as `uv sync` is.

pytest is most of the wait. CI runs it as a job of its own beside the linters,
so it runs beside them here too, once the CSS it renders with is built.
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
VOICE = [PY, "-m", "pytest", "-q", "apps/core/tests/test_written_voice.py"]


def changed_files() -> list[str] | None:
    """Everything that differs from origin/main, committed or not; None if git cannot say."""
    found: set[str] = set()
    for args in (
        ["diff", "--name-only", "origin/main...HEAD"],
        ["diff", "--name-only", "HEAD"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        result = subprocess.run(  # noqa: S603
            ["git", *args], capture_output=True, text=True, check=False  # noqa: S607
        )
        if result.returncode != 0:
            return None
        found.update(line for line in result.stdout.splitlines() if line)
    return sorted(found)


def text_only(paths: list[str] | None) -> bool:
    return paths is not None and all(
        path.startswith("docs/") or path.endswith(".md") for path in paths
    )


def summary(output: bytes) -> str:
    """pytest's last line, "N passed in Ns", which is the evidence a report quotes."""
    lines = [line for line in output.decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def main() -> int:
    verbose = "--verbose" in sys.argv
    skip = {"pip-audit"} if "--no-audit" in sys.argv else set()
    # bandit prints the offending source line; on a cp1256 console a Persian string
    # crashes its reporter before it can report anything. UTF-8 mode avoids that.
    # REQUIRE_BROWSER as in CI: a browser test skipped here would pass here only.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "REQUIRE_BROWSER": "1"}
    failed: list[str] = []

    def report(name: str, ok: bool, output: bytes, note: str = "") -> None:
        print(f"--- {name}: {'ok' if ok else 'FAILED'}{note}", flush=True)
        if not ok and output:
            sys.stdout.buffer.write(output)
            sys.stdout.flush()
        if not ok:
            failed.append(name)

    def run(name: str, command: list[str]) -> None:
        if name in skip:
            print(f"--- {name}: skipped")
            return
        if verbose:
            print(f"--- {name}", flush=True)
            ok = subprocess.run(command, env=env, check=False).returncode == 0  # noqa: S603
            if not ok:
                failed.append(name)
            return
        result = subprocess.run(  # noqa: S603
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
        )
        report(name, result.returncode == 0, result.stdout)

    if "--docs" in sys.argv:
        paths = changed_files()
        if text_only(paths):
            result = subprocess.run(  # noqa: S603
                VOICE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
            )
            report(
                "written voice",
                result.returncode == 0,
                result.stdout,
                f" ({summary(result.stdout)})",
            )
            print("\nFAILED: " + ", ".join(failed) if failed else "\nall checks passed (text only)")
            return 1 if failed else 0
        others = [p for p in paths or [] if not (p.startswith("docs/") or p.endswith(".md"))]
        print(
            f"--docs: not text only ({', '.join(others[:5]) or 'git could not say'}); running the whole gate"
        )

    steps = dict(STEPS)
    run("css", steps.pop("css"))
    with tempfile.TemporaryFile() as out:
        tests = subprocess.Popen(  # noqa: S603
            steps.pop("pytest"), env=env, stdout=out, stderr=subprocess.STDOUT
        )
        for name, command in steps.items():
            run(name, command)
        ok = tests.wait() == 0
        out.seek(0)
        output = out.read()
        if verbose:
            print("--- pytest", flush=True)
            sys.stdout.buffer.write(output)
            sys.stdout.flush()
            if not ok:
                failed.append("pytest")
        else:
            report("pytest", ok, output, f" ({summary(output)})")
    print("\nFAILED: " + ", ".join(failed) if failed else "\nall checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
