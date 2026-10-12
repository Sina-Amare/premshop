"""Session-start canary: nothing may rewrite this repository's Bash commands.

RTK, a token-saving layer, turned failed checks into clean-looking ones here (AGENTS.md §7),
so `.claude/rtk-off` switches it off for this repository and `~/.claude/hooks/rtk-guard.sh`
honours that marker. RTK puts its own hook back unasked — it did on 2026-10-06 — so this runs
every PreToolUse hook that Claude Code would apply to a Bash command, from every settings file
it merges hooks from, and reports whether any of them rewrites one. RTK's own entry is removed
when found (the owner's call, 2026-10-12), keeping the previous file as a dated `.bak`; a hook
belonging to anything else is reported and left alone.

It always prints exactly one line, including on its own failure: a canary that is silent when
all is well looks exactly like one that never ran. Run by the SessionStart hook in
`.claude/settings.json`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

BASH = r"C:\Program Files\Git\bin\bash.exe"
REPO = Path(__file__).resolve().parent.parent
MARKER = REPO / ".claude" / "rtk-off"
# Every file Claude Code merges PreToolUse hooks from, most global first. Reading only the
# global one was the hole an adversarial review found on 2026-10-12: `rtk init` without -g
# writes a project-level hook, which would have rewritten commands unseen.
SETTINGS = [
    Path("C:/ProgramData/ClaudeCode/managed-settings.json"),
    Path.home() / ".claude" / "settings.json",
    Path.home() / ".claude" / "settings.local.json",
    REPO / ".claude" / "settings.json",
    REPO / ".claude" / "settings.local.json",
]
SAMPLE = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "find . -name x"},
}


def matches_bash(matcher: str | None) -> bool:
    """Claude Code reads a hook's matcher as a regex over the tool name; no matcher means all."""
    if not matcher:
        return True
    try:
        return re.search(matcher, "Bash") is not None
    except re.error:
        return True  # an unreadable matcher may still fire: check the hook rather than skip it


def is_rtks_own(command: str) -> bool:
    """RTK's hook, as opposed to the guard that honours the marker."""
    return "rtk" in command and "rtk-guard" not in command


def rewrites(command: str) -> bool:
    # The owner's own configured hook, run the way Claude Code runs it.
    hook = subprocess.run(  # noqa: S603
        [BASH, "-c", command], input=json.dumps(SAMPLE), capture_output=True, text=True
    )
    return "updatedInput" in hook.stdout


def bash_hooks(path: Path) -> list[str]:
    try:
        settings = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    return [
        hook.get("command", "")
        for group in _pre_tool_use(settings)
        if matches_bash(group.get("matcher"))
        for hook in group.get("hooks") or []
    ]


def _pre_tool_use(settings: object) -> list[dict]:
    # A settings file may hold `null` or a wrong type anywhere: a canary must skip, never throw.
    if not isinstance(settings, dict) or not isinstance(settings.get("hooks"), dict):
        return []
    groups = settings["hooks"].get("PreToolUse")
    return (
        [group for group in groups if isinstance(group, dict)] if isinstance(groups, list) else []
    )


def remove_rtks_own(path: Path) -> int:
    """Drop RTK's own hook entries from `path`, keeping the previous file as a dated .bak."""
    settings = json.loads(path.read_text(encoding="utf-8-sig"))
    groups = _pre_tool_use(settings)
    removed = 0
    for group in groups:
        if not matches_bash(group.get("matcher")):
            continue
        hooks = group.get("hooks") or []
        keep = [h for h in hooks if not is_rtks_own(h.get("command", ""))]
        removed += len(hooks) - len(keep)
        group["hooks"] = keep
    if removed:
        settings["hooks"]["PreToolUse"] = [g for g in groups if g.get("hooks")]
        shutil.copyfile(path, path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8", newline="\n")
    return removed


def report() -> str:
    checked = 0
    present = 0
    offenders: list[tuple[Path, str]] = []
    for path in SETTINGS:
        commands = bash_hooks(path)
        present += path.exists()
        for command in commands:
            checked += 1
            if rewrites(command):
                offenders.append((path, command))

    repaired = 0
    if MARKER.exists():
        for path in dict.fromkeys(p for p, command in offenders if is_rtks_own(command)):
            repaired += remove_rtks_own(path)
        if repaired:
            offenders = [(p, c) for p, c in offenders if not is_rtks_own(c)]

    where = f"{checked} Bash hook(s) across {present} settings file(s)"
    if offenders:
        named = "; ".join(f"{path.name}: {command[:60]}" for path, command in offenders)
        return (
            f"RTK CANARY FAILED: {len(offenders)} hook(s) still rewrite Bash commands here, so a"
            f" failed check can look clean — {named}. Trust no shell output until this is fixed:"
            " docs/development.md, 'RTK is off here'."
        )
    if repaired:
        return (
            f"RTK canary: removed {repaired} rtk hook(s) that were rewriting Bash commands here"
            f" (previous settings kept as a .bak). Restart the session if output still looks"
            f" filtered. {where} checked."
        )
    return f"RTK canary: Bash commands run unrewritten here ({where} checked)."


if __name__ == "__main__":
    try:
        print(report())
    except Exception as error:  # noqa: BLE001 — a silent canary is the one failure it must not have
        print(f"RTK CANARY ERROR: {type(error).__name__}: {error}. Trust no shell output yet.")
