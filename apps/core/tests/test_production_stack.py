"""The production stack's files, read the way Docker, Compose and nginx read them.

CI builds the image and drives the stack (`deploy/smoke.sh`); these tests hold the
properties a smoke run would only notice once they were already broken: what
reaches the image, who publishes a port, where a secret comes from, and which
responses nginx compresses.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from django.conf import settings

from apps.core.support import TELEGRAM_HANDLE

ROOT = Path(settings.BASE_DIR)
DEPLOY = ROOT / "deploy"
SECRET_NAME = re.compile(r"PASSWORD|SECRET|KEY|TOKEN|DSN")
# A value Compose takes from the environment file outside the repository.
REFERENCE = re.compile(r"\$\{[A-Z_]+(?::\?[^}]*)?\}")


def services() -> dict:
    return yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))["services"]


def test_only_nginx_publishes_a_port():
    published = {name for name, service in services().items() if service.get("ports")}
    assert published == {"nginx"}
    assert not any(service.get("network_mode") == "host" for service in services().values())


def test_every_service_log_is_size_capped():
    for name, service in services().items():
        logging = service.get("logging", {})
        assert logging.get("driver") == "json-file", name
        assert {"max-size", "max-file"} <= set(logging.get("options", {})), name


def test_redis_needs_its_password():
    stack = services()
    assert "--requirepass" in " ".join(stack["redis"]["command"])
    assert "$REDIS_PASSWORD" in " ".join(stack["redis"]["command"])
    for name in ("web", "worker"):
        url = urlsplit(stack[name]["environment"]["REDIS_URL"])
        assert url.password == "${REDIS_PASSWORD}"  # noqa: S105 — a Compose reference, not a value


def test_no_service_gets_a_secret_value_inline():
    for name, service in services().items():
        if "env_file" in service:
            assert REFERENCE.fullmatch(service["env_file"]), name
        for key, value in (service.get("environment") or {}).items():
            if SECRET_NAME.search(key):
                assert REFERENCE.fullmatch(str(value)), (name, key)
            if "://" in str(value):
                password = urlsplit(str(value)).password
                assert password is None or REFERENCE.fullmatch(password), (name, key)


def dockerignored(path: str) -> bool:
    """Docker's rule: the last pattern matching the path or a parent directory wins."""
    patterns = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    parts = path.split("/")
    prefixes = ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]
    ignored = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        glob = pattern.removeprefix("!").strip("/")
        if any(fnmatch.fnmatchcase(prefix, glob) for prefix in prefixes):
            ignored = not negated
    return ignored


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        "docs-local/progress.md",
        "backups/premshop.dump",
        "media/products/a.png",
        ".venv/Lib/site.py",
        "node_modules/tailwindcss/index.js",
        ".git/HEAD",
        ".pytest_cache/v/cache",
        ".mypy_cache/3.14/x.json",
        ".ruff_cache/x",
        "apps/core/__pycache__/views.cpython-314.pyc",
        "staticfiles/staticfiles.json",
        "static/css/app.css",
    ],
)
def test_private_and_built_paths_stay_out_of_the_image(path):
    assert dockerignored(path)


@pytest.mark.parametrize(
    "path",
    ["manage.py", "uv.lock", "apps/core/views.py", "static/css/input.css", "templates/base.html"],
)
def test_the_application_reaches_the_image(path):
    assert not dockerignored(path)


def nginx_directives() -> list[tuple[tuple[str, ...], str]]:
    """Each directive of the nginx config with the blocks it sits in."""
    text = (DEPLOY / "nginx" / "premshop.conf").read_text(encoding="utf-8")
    directives, blocks, word, quote = [], [], "", ""
    for line in text.splitlines():
        for char in line:
            if quote:
                word += char
                quote = "" if char == quote else quote
            elif char in "\"'":
                word, quote = word + char, char
            elif char == "#":
                break
            elif char == "{":
                blocks.append(" ".join(word.split()))
                word = ""
            elif char == "}":
                blocks.pop()
            elif char == ";":
                directives.append((tuple(blocks), " ".join(word.split())))
                word = ""
            else:
                word += char
        word += " "
    return directives


def test_nginx_compresses_only_static_files():
    # Static files carry no secret and no text a visitor wrote, so their size leaks
    # nothing (BREACH). Pages are compressed by Django (ADR-0026): keeping a response
    # that carries a delivered credential out of that is the job of the step that
    # first puts one in a response, not of this file.
    gzip = [(blocks, line) for blocks, line in nginx_directives() if line.startswith("gzip")]
    assert "gzip on" in {line for _, line in gzip}, "the static location lost its compression"
    for blocks, line in gzip:
        assert any(block.startswith("location /static/") for block in blocks), line


def test_hashed_static_files_are_cached_as_immutable():
    assert any(
        "immutable" in line and any(block.startswith("location /static/") for block in blocks)
        for blocks, line in nginx_directives()
        if line.startswith("add_header Cache-Control")
    )


def test_the_proxy_overwrites_the_client_address_headers():
    # Appending would keep whatever the visitor wrote in front of the real address.
    lines = {line for _, line in nginx_directives()}
    assert "proxy_set_header X-Real-IP $remote_addr" in lines
    assert "proxy_set_header X-Forwarded-For $remote_addr" in lines
    assert not any("$proxy_add_x_forwarded_for" in line for line in lines)


def test_a_missing_application_answers_with_the_maintenance_page():
    lines = {line for _, line in nginx_directives()}
    assert "error_page 502 503 504 =503 /maintenance.html" in lines


def test_the_maintenance_page_stands_alone_in_written_persian():
    page = (DEPLOY / "nginx" / "maintenance.html").read_text(encoding="utf-8")
    assert '<html lang="fa" dir="rtl">' in page
    assert re.search(r"<h1>[^<]+</h1>", page)
    # nginx serves it while Django is down: nothing to fetch, nothing to render.
    for needed in ("http:", "https:", "//", "url(", "src=", "<link", "<script", "{%", "{{"):
        assert needed not in page, needed
    for banned in ("letter-spacing", "italic", "<i>", "<em>"):
        assert banned not in page, banned
    assert TELEGRAM_HANDLE in page
