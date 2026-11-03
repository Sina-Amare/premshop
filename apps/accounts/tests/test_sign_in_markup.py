"""The sign-in pages carry no inline script and no style attribute, so a content
security policy can refuse both without breaking sign-in."""

import re

import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

# base.html becomes a bare shell, so the check holds the sign-in templates
# alone; the shell's own scripts are not theirs.
SHELL = "{% block content %}{% endblock %}{% block scripts %}{% endblock %}"


@pytest.fixture
def bare_shell(settings):
    engine = {**settings.TEMPLATES[0], "APP_DIRS": False}
    engine["OPTIONS"] = {
        **engine["OPTIONS"],
        "loaders": [
            ("django.template.loaders.locmem.Loader", {"base.html": SHELL}),
            "django.template.loaders.filesystem.Loader",
            "django.template.loaders.app_directories.Loader",
        ],
    }
    settings.TEMPLATES = [engine]


@pytest.mark.parametrize("name", ["login-code", "login-code-verify", "login"])
def test_the_sign_in_pages_have_no_inline_script_or_style(client, bare_shell, name):
    if name == "login-code-verify":
        client.post(reverse("login-code"), {"email": "markup@example.test"})

    body = client.get(reverse(name)).content.decode()

    assert 'class="card auth-card"' in body, "positive control: the sign-in card rendered"
    assert re.findall(r"<script\b(?![^>]*\bsrc=)[^>]*>", body) == []
    assert re.findall(r"<[^>]+\sstyle=", body) == []


def test_the_code_page_loads_its_script_from_a_file(client, bare_shell):
    client.post(reverse("login-code"), {"email": "markup@example.test"})

    body = client.get(reverse("login-code-verify")).content.decode()

    assert re.search(r'<script src="/static/js/login-code\.js" defer>', body)
