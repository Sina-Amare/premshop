"""`.env.example` documents every variable the settings read.

Its header has always said so, and it had still fallen four variables behind —
one of them CSRF_TRUSTED_ORIGINS, which production needs. A rule written in
prose drifts; this one is checked.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings

ENV_READ = re.compile(r"""\benv(?:_\w+)?\(\s*["']([A-Z][A-Z0-9_]*)["']""")
DOCUMENTED = re.compile(r"(?m)^#?\s*([A-Z][A-Z0-9_]*)=")


def test_every_variable_the_settings_read_is_in_env_example():
    root = Path(settings.BASE_DIR)
    read = {
        name
        for path in (root / "config").rglob("*.py")
        for name in ENV_READ.findall(path.read_text(encoding="utf-8"))
    }
    documented = set(DOCUMENTED.findall((root / ".env.example").read_text(encoding="utf-8")))

    assert read, "the pattern found no env() calls at all — the check itself is broken"
    assert read <= documented, f"undocumented in .env.example: {sorted(read - documented)}"
