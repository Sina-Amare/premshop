"""Wait for GitHub's CI runs on a commit and print one line each. `python scripts/ci.py [sha]`.

`gh run watch` streams a run's progress to whatever reads it; this prints only the verdict.
It waits until the runs for the commit exist and the same runs are seen complete twice in a
row, because right after a push GitHub may list one run before it lists the other. Exit
status: 0 every run succeeded, 1 a run did not, 2 still running at the deadline.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

POLL = 30
DEADLINE = 45 * 60
FIELDS = "databaseId,event,status,conclusion"


def runs(sha: str) -> list[dict]:
    command = ["gh", "run", "list", "--commit", sha, "--json", FIELDS]
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    return json.loads(result.stdout) if result.returncode == 0 and result.stdout.strip() else []


def settled(previous: list[dict], current: list[dict]) -> bool:
    """The same runs, all complete, two polls in a row."""
    ids = {run["databaseId"] for run in current}
    return (
        bool(current)
        and ids == {run["databaseId"] for run in previous}
        and all(run["status"] == "completed" for run in current)
    )


def main() -> int:
    sha = (
        sys.argv[1]
        if len(sys.argv) > 1
        else subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True  # noqa: S607
        ).stdout.strip()
    )
    previous: list[dict] = []
    end = time.monotonic() + DEADLINE
    while time.monotonic() < end:
        current = runs(sha)
        if settled(previous, current):
            for run in current:
                print(f"CI for {sha[:7]}: {run['event']} {run['conclusion']}")
            return 0 if all(run["conclusion"] == "success" for run in current) else 1
        previous = current
        time.sleep(POLL)
    print(f"CI for {sha[:7]}: still running after {DEADLINE // 60} minutes")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
