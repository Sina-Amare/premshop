# PremShop — Notification & Telegram Contract (Phase 1)

> **At a glance.** The words and rules of every message the shop sends. Contract for the emails already built; intent for the Telegram bot, the rest of the catalog and the outbox.
> **Built:** in `templates/email/`, the login-code and sign-in-alert emails (S2), each sent directly and synchronously with no outbox yet, and the delivery-notice template (S2a), which nothing sends until S7. Their templates are the source of truth for their wording (§2.1's C5, C12 and C14 rows summarise them); the rules above the catalog (§2) are contract.
> **Intent only:** the Telegram webhook and linking (§1, S6/S8), every other customer message and the operator alerts (§2.1–§2.2, S6–S10), the outbox (§3, S6). The phase-3 API sketch has left this file (§4).
> **Read for S4a:** nothing — S4a emits events with no consumer. **For S4b:** C1 (why the order page replaces an "order registered" message) and C14 (sent when checkout-inline OTP opens a session on a passworded account).

Scope: the wording of the messages already built, and the rules every message obeys, are contract here. The Telegram bot, the rest of the catalog and the outbox are one paragraph of intent each until their step is planned. Per D4 (ADR-0004) there is **no DRF and no /api/v1 in phase 1** — the bot is one webhook view plus Celery `sendMessage` tasks calling `services.py` in‑process.

---

## 1. Telegram Webhook Contract

One Telegram bot, one-way in phase 1. It carries order notifications to a customer who has linked their Telegram account and alerts to the operator, and it runs the linking itself — started from the site's account page, or from the bot with an email address and a one-time code. It does not sell, take payment or answer order queries. Rules already fixed: no DRF and no API — the bot's only HTTP surface is its webhook, which calls the services in-process (ADR-0004); no message carries a credential value (ADR-0008); a linked account may receive login codes and the sign-in alert by Telegram, and that Telegram copy is what an attacker holding the mailbox cannot suppress (ADR-0012); operator order and payment alerts go out on Telegram and email both (ADR-0010).

Full earlier draft: `git show 027c5d1:docs/notifications-and-bot.md` (§1). It is re-specified when S6 is planned.

### 1.1 Endpoint & authentication

Intent only — see §1 above. The earlier draft's §1.1 (secret path, secret header, allowed updates) is re-specified when S6 is planned.

### 1.2 Handled inputs

Intent only — see §1 above. The earlier draft's §1.2 (A site-initiated linking, B bare `/start`, C `/link` from the bot, D fallback) is re-specified when S8 is planned.

---

## 2. Outbound Message Catalog

Channels: exactly **two** — `email` and `telegram` (customer telegram only if linked). There is no separate operator channel: an operator message is simply a `Notification` row with `user = NULL`; the operator email address and Telegram chat_id come from settings (R9). D13 rule (as amended at owner review): messages never carry credential **values**, `customer_input`, or card numbers of **any** kind — a gateway refund returns to the card the payment came from automatically and we never learn its digits, so no message has a destination to name (ADR-0019). The single-use `{delivery_link}` capability is the one sanctioned addition (ADR-0008).

Links (R16): customer messages link to the customer order pages `{site}/orders/{order_number}/` (behind login, placeholder `{order_url}`) or the tracking page `{site}/t/{tracking_token}/` — **never** to `/panel/*`; the word "panel" is reserved for the staff app. `{retry_url}` = the order's checkout retry entry point, which starts a **new** gateway attempt against the same order (ADR-0019); it is not a resumable token and carries nothing. New at owner review (ADR-0008): `{delivery_link}` = `{site}/d/{token}/` — a **single-use, 72h** delivery link opening that item's *masked* credential view without login (reveal still logged); carried only by delivery/replacement messages, always alongside the `{order_url}` fallback; a bearer capability, never a credential value; scrubbed from logs and Sentry.

IN_APP is **cut for phase 1** — the customer order pages are the in‑app surface; the channel enum extends cleanly if a bell/inbox is ever wanted.

Money and numbers follow the display rules in force (ADR-0016): **Persian digits, ASCII thousands separator (`،` is not a separator — use `,`), toman only**, the word «تومان» never abbreviated and never bolder than the numeral. Dates Jalali (core format helpers).

### 2.1 Customer notifications

**Where the email versions live.** Each message's email rendering is three files under `templates/email/` — `<name>.subject.txt`, `<name>.txt`, `<name>.html` — sent `multipart/alternative` by `apps.core.email.send_templated_email` (ADR-0023). Built so far: **C12 → `otp_code`**, **C14 → `signin_alert`**, and **C5 → `item_delivered`** (the template exists; nothing sends it until S7). For a message that is already built, **the template is the source of truth for its wording**: it carries the copy the owner reviewed and approved (the S2a styling rounds and the 2026-09-05 copy round), and the row below summarises it — the C5 and C14 summaries had drifted from their templates, which is why this changed on 2026-09-21. For a message not yet built, the wording in its row is the draft its step starts from. Preview any of them at `/dev/emails/` while `DEBUG` is on.

| # | Event (trigger) | Channels | Persian template (placeholders in `{}`) |
|---|---|---|---|
| ~~C1~~ | ~~Order registered~~ — **CUT from phase 1 (R15)** | — | With a gateway the customer is redirected to pay within seconds of creating the order; a "your order is registered" message would arrive after the payment result did. The order-detail page carries the unpaid state and a retry entry point for as long as the order is `PENDING_PAYMENT` — that is `SiteSetting.unpaid_order_ttl_hours` (default 24) from order creation, after which the sweep cancels it with reason `expired_unpaid`. The operator is no longer alerted here either — O1 moved to `payment.verified` (ADR-0019). Numbering below is unchanged so cross‑references keep working |
| C5 | `item.delivered` (first delivery) / `item.replaced` (A10a redeliver — new DeliveryField generation written, previous set `is_current=False`). Same template both times: a new OrderItemEvent row = a new key = a legitimate re‑send | email + telegram | «سفارش {order_number} ({product_name}) تحویل شد. مشاهده اطلاعات تحویل (لینک یکبارمصرف، تا ۷۲ ساعت معتبر): {delivery_link} — یا از حساب کاربری: {order_url}» — no credential values (D13-amended); the link opens the masked view, each reveal is logged |
| C12 | Login OTP — auth flow (D6) | email, **or telegram if linked** | «کد ورود شما: {otp_code}. این کد تا ۱۰ دقیقه اعتبار دارد و فقط یک بار قابل استفاده است. اگر شما این کد را نخواسته‌اید، نگران نباشید؛ بدون این کد کسی نمی‌تواند وارد حساب شما شود. این کد را به کسی ندهید.» — **bypasses the outbox** (direct Celery task, single attempt + one immediate retry; a backed‑off OTP is a dead OTP) — **Built today (S2):** email only, sent inline in the request by `apps.accounts.otp.send_login_code` through `apps.core.email.send_templated_email`, with no Celery and no retry. It moves to the direct task when Celery arrives (S6) |
| C14 | `auth.otp_signin` — an OTP-created session on an account that has a usable password (login page or checkout-inline; owner-review guard, ADR-0012) | email + telegram if linked | «در تاریخ {ts_jalali} با کد ورود وارد حساب شما شدند. اگر خودتان بودید، این پیام فقط برای اطلاع شماست. اگر شما نبودید، همین حالا رمز عبور را عوض کنید: {account_url}» — **Built today (S2):** email only, a direct synchronous send from `apps/accounts/services.py`; a mail failure never fails the login and is logged at ERROR — an issue in error tracking — naming only the account id and the error's type, never the address (fixed 2026-09-21). Delivery through the outbox and the Telegram copy are S6/S8 intent |

**The rest of the customer catalog — intent only.** Names and purpose; the wording, triggers and channels are re-specified with their step, and every one obeys the rules above. Numbers are kept so cross-references keep working.

- **C2** `payment.verified` — the payment went through: the amount actually charged and the delivery deadline, with a tracking link.
- **C3** `payment.failed` — the payment did not go through; the order is still waiting, with a retry link (ADR-0019).
- **C4** `item.awaiting_input` — the operator needs information from the customer, and the delivery clock is paused; reused for the 48-hour reminder (ADR-0009).
- **C6** `item.supply_delayed` — the promised delivery date has moved.
- **C7** `item.cancelled` (money owed) — a paid item was cancelled and its money is coming back.
- **C8** `item.refunded` — the refund was executed, with its reference; it names no card (ADR-0019).
- **C9 / C10** `subscription.expiring_soon` / `subscription.expired` — renewal reminders seven days before expiry and on the day, legacy-backfilled items included (S10).
- **C11** `sla.holiday_paused` / `sla.holiday_resumed` — the delivery clock stopped, then restarted, for a holiday (S10).
- **C13** `item.replacement_rejected` — a replacement claim was reviewed and not accepted; it carries no delivery link (ADR-0008 constraint 6).
- **C15** `item.input_final_warning` — the last reminder, at 14 days awaiting input, before the 21-day cancel (ADR-0009).

Full earlier draft: `git show 027c5d1:docs/notifications-and-bot.md` (§2.1). It is re-specified when S8 is planned.

### 2.2 Operator alerts (D17: order/payment alerts on BOTH channels)

Operator alerts are `Notification` rows with `user = NULL` on the same two channels (ADR-0010). The one the business lives on is **O1**, the new-order alert: it fires on `payment.verified`, never on order creation, because an unpaid order is not work (ADR-0019), and it goes out on both channels so a dead Telegram still reaches the operator by email and vice versa (ADR-0010). The others: **O4** customer input received, **O5** cancellation requested, **O6** replacement requested, **O7** the hourly overdue digest. **O2**, **O3** (merged into O1) and **O8** are retired; their numbers are kept. Each alert deep-links into the operator panel's queue.

Full earlier draft: `git show 027c5d1:docs/notifications-and-bot.md` (§2.2). It is re-specified when S6 is planned.

---

## 3. Outbox Mechanics (D17, D18)

The outbox exists so that a notification can never roll back or block an order transition, never goes out twice, and can still legitimately re-send after a replacement. Rules fixed by ADR-0010: after a transition commits, `events.emit()` (registered via `transaction.on_commit`) writes one `Notification` row per (occurrence, recipient, channel); a globally unique `dedupe_key`, `{occurrence}:{recipient}:{channel}`, is the only duplicate guard, and a re-send is modelled as a new occurrence, never by relaxing that uniqueness; channels are exactly `email` and `telegram`, and `user = NULL` means the operator; the sender takes rows with `select_for_update(skip_locked=True)`; retries follow a fixed ladder (now, 1m, 5m, 15m, 1h, 6h, then `failed`) and non-retryable errors fail at once; a crash between commit and enqueue drops that occurrence, accepted at this volume; an external dead-man heartbeat alarms when the pipeline itself dies. A payload never carries a credential value, and a delivery-link token in one is scrubbed from logs and error reports (ADR-0008). The login code is the one deliberate carve-out: it bypasses the outbox (C12, Concern 3).

Full earlier draft: `git show 027c5d1:docs/notifications-and-bot.md` (§3). It is re-specified when S6 is planned.

### 3.1 Flow

Intent only — see §3 above.

### 3.2 Table: `notifications.Notification` (R9)

Intent only — see §3 above. The earlier draft's §3.2 is re-specified when S6 is planned.

### 3.3 dedupe_key construction rule

Intent only — see §3 above. The earlier draft's §3.3 (one key family per trigger) is re-specified when S6 is planned.

### 3.4 Retry / backoff / terminal failure

The ladder is fixed by ADR-0010 (§3 above). The earlier draft's §3.4 (which errors are non-retryable, the staff "retry now" button) is re-specified when S6 is planned.

### 3.5 Degradation

Intent only — see §3 above. The earlier draft's §3.5 (independent channels, where terminal failures show, the dead-man heartbeat) is re-specified when S6 is planned.

---

## 4. Phase‑3 API Sketch — moved out

Not phase-1 work (ADR-0004), so it no longer lives in the public docs; it moved to the private `docs-local/` notes. Earlier text: `git show 027c5d1:docs/notifications-and-bot.md` (§4).

---

## Concerns

Concerns 1–2 were about messages not built yet: the failed-payment message's promise that the order is still waiting, and the bound on that promise; and owner sign-off on the promise wording of C3, C7 and C13 as public policy text. Full earlier draft: `git show 027c5d1:docs/notifications-and-bot.md` (§Concerns, 1–2). It is re-specified when S8 is planned.

3. **Login OTP bypasses the outbox** (C12): dedupe/backoff semantics are wrong for a 10‑minute code (`CODE_TTL_SECONDS` in `apps/accounts/otp.py`). This is a deliberate carve‑out from D17's "outbox for notifications," flagged here so it isn't read as a violation.
