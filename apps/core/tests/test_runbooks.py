"""The runbooks are public, so they name no server, no panel path and no secret."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings

RUNBOOKS = Path(settings.BASE_DIR) / "docs" / "runbooks"
FORBIDDEN = {
    "an IP address": r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
    "a host name": r"\b[\w-]+(?:\.[\w-]+)*\.(?:ir|com|net|org|io|dev|cloud|app)\b",
    "the panel's path": r"admin/|ADMIN_PATH=[^<\s]",
    "a key or token": r"[A-Za-z0-9_-]{43}=|\b[0-9a-f]{32,}\b",
    "a secret's value": r"(?i)(?:KEY|SECRET|PASSWORD|TOKEN|DSN)=[^<\s]",
}


def test_deploy_rollback_and_restore_each_have_a_runbook():
    assert {"deploy.md", "rollback.md", "restore.md"} <= {p.name for p in RUNBOOKS.glob("*.md")}


@pytest.mark.parametrize("runbook", sorted(RUNBOOKS.glob("*.md")), ids=lambda path: path.name)
def test_a_runbook_names_no_server_panel_path_or_secret(runbook):
    text = runbook.read_text(encoding="utf-8")
    found = {what: re.findall(pattern, text) for what, pattern in FORBIDDEN.items()}
    assert not any(found.values()), found
