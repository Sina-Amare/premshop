"""The canary's decisions: which hooks it looks at, which it removes, and what it says.

Before 2026-10-12 it read one settings file and only hooks whose command mentioned "rtk", so a
project-level `rtk init` would have rewritten commands while the canary printed the all-clear —
the one lie it exists to prevent. The repair writes to the owner's settings, so it is tested
here against a temporary file.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from django.conf import settings as django_settings

SCRIPT = Path(django_settings.BASE_DIR) / "scripts" / "rtk_canary.py"


def _module():
    spec = importlib.util.spec_from_file_location("rtk_canary", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


canary = _module()
GUARD = '"C:/Program Files/Git/bin/bash.exe" "C:/Users/x/.claude/hooks/rtk-guard.sh"'


def _settings_file(path: Path, *commands: str, matcher: str = "Bash") -> Path:
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": matcher,
                            "hooks": [{"type": "command", "command": c} for c in commands],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    ("matcher", "expected"),
    [
        ("Bash", True),
        (None, True),
        ("", True),
        ("Bash|Edit", True),
        ("Write|Edit", False),
        ("*", True),
    ],
)
def test_a_hook_is_examined_whenever_its_matcher_can_fire_on_bash(matcher, expected):
    assert canary.matches_bash(matcher) is expected


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rtk hook claude", True),
        ("rtk hook claude --quiet", True),
        (GUARD, False),  # the guard honours .claude/rtk-off; removing it would turn RTK back on
        ('"C:/nvm4w/nodejs/node.exe" "C:/Users/x/.claude/hooks/gsd-prompt-guard.js"', False),
    ],
)
def test_only_rtks_own_hook_is_ours_to_remove(command, expected):
    assert canary.is_rtks_own(command) is expected


def test_every_bash_hook_is_read_whatever_its_command_says(tmp_path):
    # The old version only looked at commands containing "rtk".
    path = _settings_file(tmp_path / "settings.json", "rtk hook claude", "node something-else.js")

    assert canary.bash_hooks(path) == ["rtk hook claude", "node something-else.js"]


def test_hooks_that_cannot_fire_on_bash_are_left_out(tmp_path):
    path = _settings_file(tmp_path / "settings.json", "rtk hook claude", matcher="Write|Edit")

    assert canary.bash_hooks(path) == []


@pytest.mark.parametrize("content", ["", "{", '{"hooks": null}', '\ufeff{"hooks": {}}'])
def test_an_unreadable_settings_file_is_skipped_not_fatal(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")

    assert canary.bash_hooks(path) == []


def test_removing_rtks_hook_keeps_everything_else_and_a_backup(tmp_path):
    path = _settings_file(tmp_path / "settings.json", "rtk hook claude", GUARD)

    assert canary.remove_rtks_own(path) == 1

    left = canary.bash_hooks(path)
    assert left == [GUARD], "the guard must survive, or RTK comes back on"
    backups = list(tmp_path.glob("settings.json.bak-*"))
    assert len(backups) == 1
    assert "rtk hook claude" in backups[0].read_text(encoding="utf-8")


def test_a_group_left_with_no_hooks_is_dropped(tmp_path):
    path = _settings_file(tmp_path / "settings.json", "rtk hook claude")

    assert canary.remove_rtks_own(path) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"] == []


def test_nothing_is_written_when_there_is_nothing_of_rtks_to_remove(tmp_path):
    path = _settings_file(tmp_path / "settings.json", GUARD)
    before = path.read_text(encoding="utf-8")

    assert canary.remove_rtks_own(path) == 0
    assert path.read_text(encoding="utf-8") == before
    assert list(tmp_path.glob("settings.json.bak-*")) == []


def test_the_line_says_what_was_checked_and_never_stays_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(canary, "SETTINGS", [_settings_file(tmp_path / "settings.json", GUARD)])
    monkeypatch.setattr(canary, "rewrites", lambda command: False)

    line = canary.report()

    assert line.startswith("RTK canary: Bash commands run unrewritten here")
    assert "1 Bash hook(s) across 1 settings file(s)" in line


def test_a_rewriting_hook_that_is_not_rtks_is_reported_and_left_alone(tmp_path, monkeypatch):
    path = _settings_file(tmp_path / "settings.json", "node something-else.js")
    monkeypatch.setattr(canary, "SETTINGS", [path])
    monkeypatch.setattr(canary, "rewrites", lambda command: True)

    line = canary.report()

    assert line.startswith("RTK CANARY FAILED")
    assert "something-else.js" in line
    assert canary.bash_hooks(path) == ["node something-else.js"]
    assert list(tmp_path.glob("settings.json.bak-*")) == []
