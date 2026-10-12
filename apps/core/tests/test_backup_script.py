"""The parts of `scripts/backup_docs_local.py` that decide what key the archive gets.

An adversarial review on 2026-10-12 found the script had no tests at all, and one real
defect behind that: the stored passphrase was read as plain UTF-8, so a file written by hand
on Windows — which the published restore instructions tell the owner to do on a new machine —
starts with a byte-order mark that is not whitespace and silently became part of the key.
Every check inside the script passed, because they all used the same wrong value.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from django.conf import settings

SCRIPT = Path(settings.BASE_DIR) / "scripts" / "backup_docs_local.py"


def _module():
    """Import the script by path — `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("backup_docs_local", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


backup = _module()
REAL = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"


@pytest.mark.parametrize(
    ("label", "written"),
    [
        ("plain", REAL),
        ("trailing newline", REAL + "\n"),
        ("windows line ending", REAL + "\r\n"),
        ("byte-order mark, as Windows tools write it", "﻿" + REAL + "\n"),
        ("lower case, as it may be typed from paper", REAL.lower() + "\n"),
    ],
)
def test_the_stored_passphrase_is_read_as_written_on_paper(tmp_path, monkeypatch, label, written):
    stored = tmp_path / "passphrase"
    stored.write_text(written, encoding="utf-8")
    monkeypatch.setattr(backup, "PASSPHRASE", stored)

    assert backup._stored_passphrase() == REAL, label


@pytest.mark.parametrize("written", ["", "\n", "   \n", "﻿\n", "ABCD-EFGH\n"])
def test_a_passphrase_file_that_cannot_be_the_real_one_is_refused(tmp_path, monkeypatch, written):
    stored = tmp_path / "passphrase"
    stored.write_text(written, encoding="utf-8")
    monkeypatch.setattr(backup, "PASSPHRASE", stored)

    with pytest.raises(backup.BackupError):
        backup._stored_passphrase()


def test_a_typed_passphrase_and_a_stored_one_normalise_the_same_way():
    # Otherwise `verify --ask` can pass for a value that would not decrypt a real restore.
    assert backup._normalise(f"  {REAL.lower()} \n") == backup._normalise(f"﻿{REAL}\r\n")


def test_the_published_restore_block_is_one_pasteable_unit():
    # Pasted at once, an unbraced block hands its own next line to the passphrase prompt.
    assert backup.README.count("```bash") == 1
    assert backup.RESTORE.strip().startswith("{")
    assert backup.RESTORE.strip().endswith("}")
    assert "docs-local-backup" in backup.RESTORE
    assert backup.ENCRYPTED in backup.RESTORE
