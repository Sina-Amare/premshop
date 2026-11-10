import re

import pytest
from django.urls import reverse

from apps.accounts import telegram as links
from apps.accounts.models import User

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.browser]


@pytest.mark.parametrize("width", [390, 1440])
def test_linking_shows_the_bot_link_and_the_typed_fallback(
    browser, live_server, client, settings, width, tmp_path
):
    from playwright.sync_api import expect

    settings.TELEGRAM_BOT_TOKEN = (
        "123456789:AAEexampleSECRETexampleSECRETexample12"  # noqa: S105  # nosec B105
    )
    settings.TELEGRAM_BOT_USERNAME = "premshop_test_bot"
    client.force_login(User.objects.create_user("telegram-ui@example.test"))
    context = browser.new_context(viewport={"width": width, "height": 1000})
    context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page = context.new_page()
    account = live_server.url + reverse("account")
    try:
        page.goto(account)
        page.get_by_role("button", name="اتصال تلگرام").click()
        expect(page).to_have_url(account + "#telegram")
        opener = page.get_by_role("link", name="باز کردن تلگرام")
        expect(opener).to_have_attribute(
            "href", re.compile(r"^https://t\.me/premshop_test_bot\?start=[\w-]{22}$")
        )
        token = opener.get_attribute("href").rsplit("=", 1)[1]
        command = page.get_by_text(f"/start {token}")
        expect(command).to_be_visible()
        # Split over two lines, the command is easy to copy half of.
        assert command.evaluate("el => el.getClientRects().length") == 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(tmp_path / f"telegram-{width}.png"), full_page=True)
    finally:
        context.close()


def test_the_page_shows_the_link_once_the_bot_confirms_it(browser, live_server, client, settings):
    from playwright.sync_api import expect

    settings.TELEGRAM_BOT_TOKEN = (
        "123456789:AAEexampleSECRETexampleSECRETexample12"  # noqa: S105  # nosec B105
    )
    settings.TELEGRAM_BOT_USERNAME = "premshop_test_bot"
    client.force_login(User.objects.create_user("telegram-wait@example.test"))
    context = browser.new_context(viewport={"width": 390, "height": 1000})
    context.add_cookies(
        [{"name": "sessionid", "value": client.cookies["sessionid"].value, "url": live_server.url}]
    )
    page = context.new_page()
    try:
        page.goto(live_server.url + reverse("account"))
        page.get_by_role("button", name="اتصال تلگرام").click()
        opener = page.get_by_role("link", name="باز کردن تلگرام")
        token = opener.get_attribute("href").rsplit("=", 1)[1]
        links.confirm_link(token, {"id": 555, "username": "buyer_tg"})
        # Nobody reloads: the waiting page notices the confirm on its own.
        expect(page.get_by_text("@buyer_tg")).to_be_visible(timeout=10_000)
        expect(opener).to_have_count(0)
    finally:
        context.close()
