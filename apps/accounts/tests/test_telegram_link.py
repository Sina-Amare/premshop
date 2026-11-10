import logging
from datetime import timedelta

import pytest
import time_machine
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from huey.contrib.djhuey import HUEY

from apps.accounts import tasks as account_tasks
from apps.accounts import telegram as links
from apps.accounts.models import User
from apps.core import ratelimit
from apps.core import telegram as telegram_api

pytestmark = pytest.mark.django_db(transaction=True)

TOKEN = "123456789:AAEexampleSECRETexampleSECRETexample12"  # noqa: S105  # nosec B105
POLL_LOCK = f"{HUEY.name}.lock.telegram-updates"
# Collection imports this module before any test runs a poll, so this records what a
# freshly started worker sees: run_huey imports the tasks, then flushes the locks it knows.
REGISTERED_AT_IMPORT = POLL_LOCK in HUEY._locks


@pytest.fixture
def bot(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN = TOKEN
    settings.TELEGRAM_BOT_USERNAME = "premshop_test_bot"
    calls = []

    def call(method, payload, *, timeout=5):
        calls.append((method, payload))
        return {"message_id": 99} if method != "answerCallbackQuery" else True

    monkeypatch.setattr(telegram_api, "call", call)
    return calls


@pytest.fixture
def user():
    return User.objects.create_user("buyer@example.test")


def start(text, *, chat_type="private", telegram_id=555, username="buyer_tg"):
    sender = {"id": telegram_id, "username": username, "first_name": "B"}
    chat = {"id": telegram_id if chat_type == "private" else -100, "type": chat_type}
    return {
        "update_id": 1,
        "message": {"message_id": 10, "chat": chat, "from": sender, "text": text},
    }


def press(data, *, chat_type="private", telegram_id=555, username="buyer_tg"):
    sender = {"id": telegram_id, "username": username, "first_name": "B"}
    chat = {"id": telegram_id if chat_type == "private" else -100, "type": chat_type}
    return {
        "update_id": 2,
        "callback_query": {
            "id": "callback-1",
            "from": sender,
            "message": {"message_id": 11, "chat": chat},
            "data": data,
        },
    }


def linked(user, telegram_id=555, username="buyer_tg"):
    User.objects.filter(pk=user.pk).update(
        telegram_id=telegram_id, telegram_username=username, telegram_linked_at=timezone.now()
    )


def test_start_shows_the_masked_email_and_keeps_the_token(bot, user):
    token = links.start_link(user)
    links.handle_update(start(f"/start {token}"))
    method, payload = bot[-1]
    assert method == "sendMessage" and payload["chat_id"] == 555
    assert "b•••@example.test" in payload["text"] and user.email not in payload["text"]
    button = payload["reply_markup"]["inline_keyboard"][0][0]
    assert button["callback_data"] == f"link:{token}"
    assert links.link_owner(token) == user


def test_confirm_links_once_and_emails_the_holder(bot, user):
    token = links.start_link(user)
    links.handle_update(press(f"link:{token}"))
    user.refresh_from_db()
    assert (user.telegram_id, user.telegram_username) == (555, "buyer_tg")
    assert user.telegram_linked_at is not None
    assert [method for method, _ in bot] == ["answerCallbackQuery", "editMessageText"]
    assert len(mail.outbox) == 1 and mail.outbox[0].to == [user.email]
    assert "@buyer_tg" in mail.outbox[0].body and token not in mail.outbox[0].body
    assert links.link_owner(token) is None
    links.handle_update(press(f"link:{token}", telegram_id=777, username="someone_else"))
    assert User.objects.get(pk=user.pk).telegram_id == 555
    assert not User.objects.filter(telegram_id=777).exists()


def test_the_alert_never_names_a_telegram_by_its_display_name(bot, user):
    # A display name is free text: a scammer can call theirs «پشتیبانی رسمی PremShop».
    links.confirm_link(links.start_link(user), {"id": 555, "first_name": "پشتیبانی رسمی PremShop"})
    message = mail.outbox[0]
    for part in (message.body, message.alternatives[0][0]):
        assert "رسمی" not in part and "555" in part


def test_relinking_the_same_telegram_sends_no_second_email(bot, user):
    links.confirm_link(links.start_link(user), {"id": 555, "username": "buyer_tg"})
    links.confirm_link(links.start_link(user), {"id": 555, "username": "buyer_tg"})
    assert len(mail.outbox) == 1


def test_changes_from_the_account_page_have_a_daily_limit(bot, client, user):
    # Each change emails the account, and that mail draws on the sign-in codes' reserve.
    client.force_login(user)
    url = reverse("account-telegram")
    for _ in range(ratelimit.TELEGRAM_CHANGES_PER_ACCOUNT.times):
        client.post(url, {"action": "link"})
    issued = client.session["accounts.telegram_link"]
    refused = client.post(url, {"action": "link"}, follow=True)
    assert client.session["accounts.telegram_link"] == issued
    assert "برای امروز" in refused.content.decode()
    # Unlinking is never refused: it is what the link alert tells a customer to do.
    linked(user)
    client.post(url, {"action": "unlink"})
    assert User.objects.get(pk=user.pk).telegram_id is None
    assert [message.to for message in mail.outbox] == [[user.email]]


def test_one_telegram_can_confirm_only_a_few_links_a_day(bot, user):
    for _ in range(ratelimit.TELEGRAM_CONFIRMS_PER_TELEGRAM.times):
        links.handle_update(press(f"link:{links.start_link(user)}"))
    token = links.start_link(user)
    bot.clear()
    links.handle_update(press(f"link:{token}"))
    assert "برای امروز" in dict(bot)["editMessageText"]["text"]
    assert links.link_owner(token) == user


def test_a_second_press_after_linking_says_it_is_already_linked(bot, user):
    token = links.start_link(user)
    links.handle_update(press(f"link:{token}"))
    bot.clear()
    links.handle_update(press(f"link:{token}"))
    edited = dict(bot)["editMessageText"]
    assert "وصل است" in edited["text"]


def test_an_expired_or_made_up_token_links_nothing(bot, user):
    token = links.start_link(user)
    with time_machine.travel(timezone.now() + timedelta(minutes=11), tick=False):
        assert links.link_owner(token) is None
        links.handle_update(press(f"link:{token}"))
    links.handle_update(press("link:made-up-token"))
    links.handle_update(start("/start made-up-token"))
    assert User.objects.filter(telegram_id__isnull=False).count() == 0
    assert all("reply_markup" not in payload for _, payload in bot)


def test_a_newer_token_kills_the_older_one(bot, user):
    older = links.start_link(user)
    newer = links.start_link(user)
    assert links.link_owner(older) is None
    assert links.confirm_link(older, {"id": 555, "username": "buyer_tg"}) is None
    assert links.confirm_link(newer, {"id": 555, "username": "buyer_tg"}) == user


def test_after_a_double_click_only_the_latest_token_links(bot, user):
    first = links.start_link(user)
    second = links.start_link(user)
    # Two clicks at once: the second issue ran before the first was stored, so nothing
    # deleted the first token's record. Only the account's current token may count.
    cache.set(links._record_key(first), user.pk)
    assert links.link_owner(first) is None
    assert links.confirm_link(first, {"id": 555, "username": "buyer_tg"}) is None
    assert links.confirm_link(second, {"id": 555, "username": "buyer_tg"}) == user


def test_of_two_confirms_on_one_token_exactly_one_links(bot, user):
    token = links.start_link(user)
    first = links.confirm_link(token, {"id": 555, "username": "first"})
    second = links.confirm_link(token, {"id": 777, "username": "second"})
    assert first == user and second is None
    assert User.objects.get(pk=user.pk).telegram_id == 555


def test_a_telegram_linked_elsewhere_moves_and_its_old_account_is_told(bot, user):
    earlier = User.objects.create_user("earlier@example.test")
    linked(earlier)
    assert links.confirm_link(links.start_link(user), {"id": 555, "username": "buyer_tg"}) == user
    earlier.refresh_from_db()
    assert earlier.telegram_id is None and earlier.telegram_username == ""
    assert User.objects.get(pk=user.pk).telegram_id == 555
    assert sorted(message.to[0] for message in mail.outbox) == sorted([earlier.email, user.email])


@pytest.mark.parametrize("text", ["/start", "سلام، سفارشم کی می‌رسد؟"])
def test_anything_else_gets_a_pointer_to_the_account_page(bot, user, settings, text):
    links.handle_update(start(text))
    method, payload = bot[-1]
    assert method == "sendMessage"
    assert settings.SITE_URL + reverse("account") in payload["text"]
    assert "@premshop_support" in payload["text"]
    assert User.objects.get(pk=user.pk).telegram_id is None


def test_groups_and_channels_link_nothing(bot, user):
    token = links.start_link(user)
    links.handle_update(start(f"/start {token}", chat_type="group"))
    links.handle_update(press(f"link:{token}", chat_type="supergroup"))
    links.handle_update({"update_id": 3, "channel_post": {"chat": {"id": -5, "type": "channel"}}})
    assert bot == []
    assert links.link_owner(token) == user


def test_unlink_needs_a_post_from_the_signed_in_holder(bot, client, user):
    other = User.objects.create_user("other@example.test")
    linked(user)
    linked(other, telegram_id=777, username="other_tg")
    url = reverse("account-telegram")
    anonymous = client.post(url, {"action": "unlink"})
    assert anonymous.status_code == 302 and "/login/" in anonymous["Location"]
    client.force_login(user)
    assert client.get(url).status_code == 405
    assert client.post(url, {"action": "unlink"}).status_code == 302
    user.refresh_from_db()
    other.refresh_from_db()
    assert user.telegram_id is None and user.telegram_linked_at is None
    assert other.telegram_id == 777
    assert [message.to for message in mail.outbox] == [[user.email]]


def test_the_account_page_moves_through_its_three_states(bot, client, user, settings):
    client.force_login(user)
    page = client.get(reverse("account")).content.decode()
    assert "اتصال تلگرام" in page and "t.me/premshop_test_bot" not in page
    client.post(reverse("account-telegram"), {"action": "link"})
    token = client.session["accounts.telegram_link"]
    page = client.get(reverse("account")).content.decode()
    assert f"https://t.me/premshop_test_bot?start={token}" in page
    assert f"/start {token}" in page
    links.confirm_link(token, {"id": 555, "username": "buyer_tg"})
    page = client.get(reverse("account")).content.decode()
    assert "@buyer_tg" in page and "قطع اتصال" in page and token not in page
    settings.TELEGRAM_BOT_TOKEN = ""
    page = client.get(reverse("account")).content.decode()
    assert "اتصال تلگرام" not in page and "قطع اتصال" not in page


def test_the_poll_keeps_its_place_and_survives_a_bad_update(bot, monkeypatch, caplog):
    offsets = []
    batches = [[{"update_id": 5}, {"update_id": 6}], []]

    def get_updates(offset, timeout):
        offsets.append(offset)
        return batches.pop(0) if batches else []

    handled = []

    def handle(update):
        if update["update_id"] == 5:
            raise KeyError(f"broken update {TOKEN}")
        handled.append(update["update_id"])

    monkeypatch.setattr(telegram_api, "get_updates", get_updates)
    monkeypatch.setattr(links, "handle_update", handle)
    with caplog.at_level(logging.INFO):
        assert links.poll(seconds=0) == 2
        assert links.poll(seconds=0) == 0
    assert handled == [6] and offsets == [None, 7]
    assert "KeyError" in caplog.text and TOKEN not in caplog.text


def test_an_outage_logs_one_error_then_warnings_until_it_recovers(bot, monkeypatch, caplog):
    down = {"now": True}

    def get_updates(offset, timeout):
        if down["now"]:
            raise telegram_api.TelegramError(None)
        return []

    monkeypatch.setattr(telegram_api, "get_updates", get_updates)
    with caplog.at_level(logging.INFO, logger="apps.accounts.telegram"):
        links.poll(seconds=0)
        links.poll(seconds=0)
        down["now"] = False
        links.poll(seconds=0)
        down["now"] = True
        links.poll(seconds=0)
    assert [record.levelno for record in caplog.records] == [
        logging.ERROR,
        logging.WARNING,
        logging.INFO,
        logging.ERROR,
    ]
    assert TOKEN not in caplog.text


def test_the_cache_never_holds_a_live_token(bot, user):
    # Keys and values are readable in a Redis dump; the token is stored as an HMAC.
    token = links.start_link(user).encode()
    stored = cache._cache  # type: ignore[attr-defined]
    assert stored and all(token not in key.encode() + value for key, value in stored.items())


def test_a_reply_telegram_refuses_logs_a_warning_not_an_error(bot, monkeypatch, caplog):
    monkeypatch.setattr(telegram_api, "get_updates", lambda offset, timeout: [{"update_id": 9}])

    def refused(update):
        raise telegram_api.TelegramError(429, 3)

    monkeypatch.setattr(links, "handle_update", refused)
    with caplog.at_level(logging.INFO, logger="apps.accounts.telegram"):
        links.poll(seconds=0)
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_telegram_is_off_unless_a_test_turns_it_on(settings):
    # The local .env may hold the real bot token; a test must not depend on it.
    assert settings.TELEGRAM_BOT_TOKEN == ""


def test_without_a_token_the_poll_does_nothing(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN = ""
    monkeypatch.setattr(telegram_api, "get_updates", lambda offset, timeout: pytest.fail())
    assert links.poll(seconds=0) == 0


def queued_polls():
    return [task for task in HUEY.pending() if task.name.endswith("poll_telegram")]


@pytest.fixture
def polls(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN = TOKEN
    ran = []
    monkeypatch.setattr(links, "poll", lambda: ran.append(1) or 0)
    return ran


def test_a_killed_workers_lock_is_flushed_when_the_worker_starts(settings, polls):
    assert settings.HUEY["consumer"]["flush_locks"] is True
    assert REGISTERED_AT_IMPORT
    # A worker killed mid-poll never runs the lock's release.
    HUEY.put_if_empty(POLL_LOCK, "1")
    assert account_tasks.poll_telegram.call_local() == 0
    assert (polls, queued_polls()) == ([], [])
    HUEY.flush_locks()  # what run_huey's consumer runs as it starts
    account_tasks.poll_telegram.call_local()
    assert polls == [1]


def test_each_poll_queues_the_next_once_its_lock_is_free(polls, monkeypatch):
    held = []
    enqueue = HUEY.enqueue

    def watched(task):
        held.append(HUEY.storage.has_data_for_key(POLL_LOCK))
        return enqueue(task)

    monkeypatch.setattr(HUEY, "enqueue", watched)
    account_tasks.poll_telegram.call_local()
    assert len(queued_polls()) == 1
    # Queued while still held, the next poll could start, find the lock and give up.
    assert held == [False]


def test_a_failed_poll_leaves_the_retry_to_the_minute_run(settings, monkeypatch):
    # An outage must not turn the chain into a loop that fails as fast as it can.
    settings.TELEGRAM_BOT_TOKEN = TOKEN

    def down(offset, timeout):
        raise telegram_api.TelegramError(None)

    monkeypatch.setattr(telegram_api, "get_updates", down)
    account_tasks.poll_telegram.call_local()
    assert queued_polls() == []


def test_without_a_token_no_poll_is_queued():
    account_tasks.poll_telegram.call_local()
    assert queued_polls() == []
