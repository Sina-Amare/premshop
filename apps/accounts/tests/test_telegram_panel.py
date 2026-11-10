from types import SimpleNamespace

import pytest
from django.urls import reverse

from apps.accounts import telegram as links
from apps.accounts.models import User
from apps.accounts.tests.test_telegram_link import TOKEN, press, start
from apps.core import ratelimit
from apps.core import telegram as telegram_api

pytestmark = pytest.mark.django_db(transaction=True)

QUIET = {"answerCallbackQuery", "deleteMessage", "editMessageReplyMarkup", "setMyCommands"}


@pytest.fixture
def bot(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN = TOKEN
    settings.TELEGRAM_BOT_USERNAME = "premshop_test_bot"
    fake = SimpleNamespace(calls=[], failing=set(), ids=iter(range(100, 200)))

    def call(method, payload, *, timeout=5):
        fake.calls.append((method, payload))
        if method in fake.failing:
            raise telegram_api.TelegramError(400)
        return True if method in QUIET else {"message_id": next(fake.ids)}

    monkeypatch.setattr(telegram_api, "call", call)
    return fake


@pytest.fixture
def linked():
    user = User.objects.create_user("panel@example.test")
    User.objects.filter(pk=user.pk).update(telegram_id=555)
    return user


def methods(bot):
    return [method for method, _ in bot.calls]


def test_start_from_a_linked_telegram_opens_the_panel_and_clears_the_command(bot, linked):
    links.handle_update(start("/start"))
    (send, sent), (delete, deleted) = bot.calls
    assert send == "sendMessage" and "اتصال حساب در سایت" not in sent["text"]
    assert sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "p:orders"
    # The customer's own command goes too: the chat keeps the panel and the notices.
    assert (delete, deleted) == ("deleteMessage", {"chat_id": 555, "message_id": 10})


def test_typed_text_opens_the_panel_and_is_left_alone(bot, linked):
    links.handle_update(start("سلام، سفارشم کی می‌رسد؟"))
    assert methods(bot) == ["sendMessage"]


def test_a_new_panel_retires_the_old_one(bot, linked):
    links.handle_update(start("/start"))
    links.handle_update(start("/start"))
    assert ("deleteMessage", {"chat_id": 555, "message_id": 100}) in bot.calls


def test_an_old_panel_past_the_deletion_window_loses_its_buttons(bot, linked):
    links.handle_update(start("/start"))
    bot.failing.add("deleteMessage")
    links.handle_update(start("/start"))
    assert (
        "editMessageReplyMarkup",
        {"chat_id": 555, "message_id": 100, "reply_markup": {"inline_keyboard": []}},
    ) in bot.calls


def test_a_press_is_answered_first_then_redraws_the_panel(bot, linked):
    links.handle_update(press("p:orders"))
    (answer, _), (edit, edited) = bot.calls
    assert (answer, edit) == ("answerCallbackQuery", "editMessageText")
    assert edited["message_id"] == 11 and "سفارش‌های من" in edited["text"]
    assert edited["reply_markup"]["inline_keyboard"][-1][0]["callback_data"] == "p:home"


def test_a_press_from_a_telegram_no_longer_linked_shows_no_account(bot, linked, settings):
    User.objects.filter(pk=linked.pk).update(telegram_id=None)
    links.handle_update(press("p:orders"))
    edited = bot.calls[-1][1]
    assert "reply_markup" not in edited and "سفارش‌های من" not in edited["text"]
    assert settings.SITE_URL + reverse("account") in edited["text"]


def test_panel_presses_are_limited_per_telegram(bot, linked):
    for _ in range(ratelimit.TELEGRAM_PANEL_PRESSES.times):
        links.handle_update(press("p:home"))
    bot.calls.clear()
    links.handle_update(press("p:orders"))
    assert methods(bot) == ["answerCallbackQuery"] and bot.calls[0][1]["text"]


def test_a_spent_limit_stops_messages_opening_panels(bot, linked):
    # Each panel costs queries and Telegram calls: a scripted /start loop must not keep the
    # one poll loop busy while other customers wait.
    for _ in range(ratelimit.TELEGRAM_PANEL_PRESSES.times):
        links.handle_update(press("p:home"))
    bot.calls.clear()
    links.handle_update(start("/start"))
    assert bot.calls == []


def test_a_late_answer_still_redraws_the_panel(bot, linked):
    # After an outage a press is answered past Telegram's window: the answer fails, the edit
    # must not.
    bot.failing.add("answerCallbackQuery")
    links.handle_update(press("p:orders"))
    assert methods(bot) == ["answerCallbackQuery", "editMessageText"]


def test_a_late_answer_still_links_and_says_so(bot):
    user = User.objects.create_user("late-confirm@example.test")
    token = links.start_link(user)
    bot.failing.add("answerCallbackQuery")
    links.handle_update(press(f"link:{token}"))
    assert methods(bot)[-1] == "editMessageText"
    assert User.objects.get(pk=user.pk).telegram_id == 555


def test_panel_presses_in_a_group_do_nothing(bot, linked):
    links.handle_update(press("p:orders", chat_type="group"))
    assert bot.calls == []


def test_the_bot_lists_its_command_once_a_day_and_retries_a_refusal(bot, monkeypatch):
    monkeypatch.setattr(telegram_api, "get_updates", lambda offset, timeout: [])
    bot.failing.add("setMyCommands")
    links.poll(seconds=0)
    bot.failing.clear()
    links.poll(seconds=0)
    links.poll(seconds=0)
    sent = [payload for method, payload in bot.calls if method == "setMyCommands"]
    assert len(sent) == 2
    assert sent[-1] == {
        "commands": [{"command": "start", "description": links.COMMAND}],
        "scope": {"type": "all_private_chats"},
    }
