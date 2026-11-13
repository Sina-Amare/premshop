import pytest
from django.contrib.messages import ERROR, SUCCESS
from django.contrib.messages.storage.base import Message
from django.template.loader import render_to_string


@pytest.mark.parametrize(
    "level,css,role", [(ERROR, "form-error", "alert"), (SUCCESS, "form-ok", "status")]
)
def test_account_feedback_distinguishes_refused_mail_from_success(level, css, role):
    markup = render_to_string(
        "accounts/_account_base.html",
        {
            "messages": [Message(level, "ارسال ایمیل فعلاً به سقف مجاز رسیده است.")],
            "csrf_token": "fixture",
        },
    )
    assert f'<div class="{css}" role="{role}">' in markup
