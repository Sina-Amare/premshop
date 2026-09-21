"""Session-start canary: RTK must not rewrite Bash commands in this repository.

RTK made failed checks look clean here (AGENTS.md §7), so `.claude/rtk-off` switches its
hook off for this repository. This runs every configured PreToolUse hook that mentions rtk
on a sample command, the way Claude Code would, and reports whether any still rewrites it.
It always prints a line: a canary that stays silent when all is well looks exactly like one
that never ran. Run by the SessionStart hook in `.claude/settings.json`.
"""

import json
import subprocess
from pathlib import Path

BASH = r"C:\Program Files\Git\bin\bash.exe"
SAMPLE = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "find . -name x"},
}

settings = Path.home() / ".claude" / "settings.json"
groups = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}).get("PreToolUse", [])
commands = [
    h["command"] for g in groups for h in g.get("hooks", []) if "rtk" in h.get("command", "")
]


def rewrites(command: str) -> bool:
    # The owner's own configured hook, run the way Claude Code runs it.
    hook = subprocess.run(  # noqa: S603
        [BASH, "-c", command], input=json.dumps(SAMPLE), capture_output=True, text=True
    )
    return "updatedInput" in hook.stdout


rewriting = [c for c in commands if rewrites(c)]
if rewriting:
    print(
        f"RTK CANARY FAILED: {len(rewriting)} hook(s) in {settings} still rewrite Bash commands in"
        " this repository, so a failed check can look clean. Trust no Bash output until this is"
        " fixed: docs/development.md, 'RTK is off here'."
    )
else:
    print(f"RTK canary: Bash commands run unrewritten here ({len(commands)} rtk hook(s) checked).")
