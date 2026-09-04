# PremShop — Notification & Telegram Contract (Phase 1) + Future API Sketch

Scope: this document specifies the phase‑1 Telegram bot (one‑way notifications + account linking), the complete outbound message catalog, and the outbox delivery mechanics. Section 4 is a deferred sketch only. Per D4 there is **no DRF and no /api/v1 in phase 1** — the bot is one webhook view plus Celery `sendMessage` tasks calling `services.py` in‑process.

---

## 1. Telegram Webhook Contract

### 1.1 Endpoint & authentication

| Item | Value |
|---|---|
| Path | `POST /telegram/webhook/<TELEGRAM_WEBHOOK_PATH>/` — `TELEGRAM_WEBHOOK_PATH` is a random URL‑safe slug from env (≥32 chars), registered via `setWebhook` |
| Header check | `X-Telegram-Bot-Api-Secret-Token` must equal env `TELEGRAM_WEBHOOK_SECRET` (set in the same `setWebhook` call). Mismatch → `403`, no body, no logging of payload |
| Allowed updates | `setWebhook(allowed_updates=["message"])` — Telegram drops everything else at the source |
| Response | Always `200` with empty body for any authenticated update, including unhandled ones (a non‑2xx makes Telegram retry) |
| CDN | Webhook path is in the CDN bypass / no‑store list (D19); Telegram reaches the origin directly — the foreign VPS needs no relay |
| Handler shape | View parses the update, does Redis/DB work synchronously via `accounts.services`, enqueues the *reply* `sendMessage` as a Celery task, returns. No business logic in the view |

Only `message` updates in **private chats** are processed. Group/channel messages, edited messages, media, stickers, callback queries, inline queries: ignored (200, no reply).

### 1.2 Handled inputs

**A. `/start lnk_<token>` — site‑initiated linking (D6, brief §11)**

- Site's "اتصال تلگرام" button creates Redis key `lnk:{token}` → `user_id`, TTL **300 s**, then opens `t.me/<bot>?start=lnk_<token>`.
- Handler consumes the token with `GETDEL` (**single‑use**, atomic).
- Token valid → `accounts.services.complete_telegram_link(user_id, telegram_id, telegram_username)`. Enforces one‑to‑one both ways: if this `telegram_id` is already linked to another user, or the user already has a different `telegram_id`, refuse with an explanatory message (unlink happens on the site, not in the bot). Success sets `telegram_linked_at` and replies:
  > «حساب تلگرام شما با موفقیت به پرم‌شاپ متصل شد. از این پس اعلان سفارش‌ها را همین‌جا دریافت می‌کنید.»
- Token missing/expired → «لینک اتصال منقضی شده است. لطفاً دوباره از صفحه حساب کاربری در سایت اقدام کنید.» + link to the customer account page on the site (never `/panel/*` — that is the staff app, R16).

**B. Bare `/start`**

- Already linked → «حساب شما متصل است.» + site link.
- Not linked → short welcome: what the bot does (order notifications), plus two options: link from the site (customer account page) or send `/link` to link right here.

**C. `/link` — bot‑initiated linking conversation (D6)**

Conversation state lives in Redis key `tgconv:{telegram_id}` → JSON `{"state": ..., "email": ..., "tries": n}`, TTL **600 s**, refreshed on every message. Timeout = key expiry; next message gets the unknown‑input fallback.

| State | Bot asks | On customer message |
|---|---|---|
| `awaiting_email` | «ایمیل حساب پرم‌شاپ خود را وارد کنید. اگر حساب ندارید، با همین ایمیل برایتان ساخته می‌شود.» | Validate format → generate 6‑digit OTP, store `otp:tg:{telegram_id}` (hashed code + email, TTL 300 s), email it, move to `awaiting_otp`. Rate limit: max 3 OTP sends per telegram_id per hour, same limiter class as site OTP |
| `awaiting_otp` | «کد ۶ رقمی ارسال‌شده به ایمیل را وارد کنید.» | Correct → link existing account, or **create** account (email as USERNAME_FIELD, this flow counts as email verification per D6) and link. Wrong → increment `tries`; after 5 wrong, delete state: «تعداد تلاش بیش از حد مجاز بود. با /link دوباره شروع کنید.» |

`/cancel` at any state deletes the conversation key: «عملیات لغو شد.»

**D. Anything else (unknown input fallback)**

> «این ربات فقط اعلان سفارش‌های پرم‌شاپ را ارسال می‌کند. برای خرید و پیگیری سفارش به سایت مراجعه کنید: {site_url} — برای پشتیبانی: {support_contact}»

Sent at most once per `telegram_id` per 10 minutes (Redis key `tgfb:{telegram_id}`, TTL 600) so a confused user isn't spammed.

### 1.3 What the bot explicitly does NOT do in phase 1

No browsing, ordering, or payment. No order‑status queries (`/orders` etc.). No credential delivery of any kind — ever (D13). No inline keyboards with callbacks, no inline mode, no mini‑app, no media intake, no support chat relay, no group behavior, no unlinking (site only). Conversational bot + mini‑app arrive in phase 3.

---

## 2. Outbound Message Catalog

Channels: exactly **two** — `email` and `telegram` (customer telegram only if linked). There is no separate operator channel: an operator message is simply a `Notification` row with `user = NULL`; the operator email address and Telegram chat_id come from settings (R9). D13 rule (as amended at owner review): messages never carry credential **values**, `customer_input`, or card numbers of **any** kind — a gateway refund returns to the card the payment came from automatically and we never learn its digits, so no message has a destination to name (ADR-0019). The single-use `{delivery_link}` capability is the one sanctioned addition (ADR-0008).

Links (R16): customer messages link to the customer order pages `{site}/orders/{order_number}/` (behind login, placeholder `{order_url}`) or the tracking page `{site}/t/{tracking_token}/` — **never** to `/panel/*`; the word "panel" is reserved for the staff app. `{retry_url}` = the order's checkout retry entry point, which starts a **new** gateway attempt against the same order (ADR-0019); it is not a resumable token and carries nothing. New at owner review (ADR-0008): `{delivery_link}` = `{site}/d/{token}/` — a **single-use, 72h** delivery link opening that item's *masked* credential view without login (reveal still logged); carried only by delivery/replacement messages, always alongside the `{order_url}` fallback; a bearer capability, never a credential value; scrubbed from logs and Sentry. Operator alerts link to `{site}/panel/queue/{order_item_id}` (placeholder `{op_panel_url}`); the overdue digest links to the queue list itself, `{site}/panel/queue/` (placeholder `{op_queue_url}`). Renewal = product page URL.

IN_APP is **cut for phase 1** — the customer order pages are the in‑app surface; the channel enum extends cleanly if a bell/inbox is ever wanted.

Money and numbers follow the display rules in force (ADR-0016): **Persian digits, ASCII thousands separator (`،` is not a separator — use `,`), toman only**, the word «تومان» never abbreviated and never bolder than the numeral. Dates Jalali (core format helpers).

### 2.1 Customer notifications

**Where the email versions live.** Each message's email rendering is three files under `templates/email/` — `<name>.subject.txt`, `<name>.txt`, `<name>.html` — sent `multipart/alternative` by `apps.core.email.send_templated_email` (ADR-0023). Built so far: **C12 → `otp_code`**, **C5 → `item_delivered`**. The Persian wording below is the contract; a template that drifts from it is the template's bug. Preview any of them at `/dev/emails/` while `DEBUG` is on.

| # | Event (trigger) | Channels | Persian template (placeholders in `{}`) |
|---|---|---|---|
| ~~C1~~ | ~~Order registered~~ — **CUT from phase 1 (R15)** | — | With a gateway the customer is redirected to pay within seconds of creating the order; a "your order is registered" message would arrive after the payment result did. The order-detail page carries the unpaid state and a retry entry point for as long as the order is `PENDING_PAYMENT` — that is `SiteSetting.unpaid_order_ttl_hours` (default 24) from order creation, after which the sweep cancels it with reason `expired_unpaid`. The operator is no longer alerted here either — O1 moved to `payment.verified` (ADR-0019). Numbering below is unchanged so cross‑references keep working |
| C2 | `payment.verified` — server‑to‑server verify succeeded, payment → verified, items → QUEUED. **Sole emitter: `payments.confirm_payment(payment, ...)`** — the internal helper that both `verify_payment` (callback **and** lost‑callback inquiry) and `record_manual_payment` route through; `orders.mark_order_paid` emits nothing (R11). It takes a row lock on the Payment (`select_for_update`), re‑reads the status **under that lock**, and returns the payment unchanged if it is already `verified` — a lock‑and‑re‑read, never an unlocked `if not payment.verified` check. That is what makes a repeated callback re‑enter the same emitter and produce the same dedupe key — one message (ADR-0019). `Payment.idempotency_key` plays no part here: it is the key sent to the **gateway** on initiate, so a retried initiate cannot create two upstream payment requests. `{total_amount}` is `Order.total_amount` — the order total **after** discount, i.e. what was actually charged; the verified gateway amount is compared against it and discarded, never quoted | email + telegram | «پرداخت سفارش {order_number} تایید شد و سفارش شما ثبت نهایی شد. مبلغ پرداختی: {total_amount} تومان. سفارش حداکثر تا {due_at_jalali} تحویل می‌شود و در طول این مدت می‌توانید وضعیت آن را دنبال کنید: {tracking_url}» |
| C3 | `payment.failed` — the gateway reported an unsuccessful payment, or verify rejected the result (including an **amount mismatch**, which is treated as a failure and never as a partial payment — ADR-0019). `fail_payment` transitions the **payment only**: the order stays `PENDING_PAYMENT` and remains payable, no item is cancelled and nothing is stamped expired — which is precisely what `{retry_url}` in this message depends on. The promise has a stated bound: the order lives for `SiteSetting.unpaid_order_ttl_hours` (default 24) from creation before the sweep cancels it as `expired_unpaid`, and `{order_ttl_hours}` renders that setting rather than a hard‑coded number, so the message can never outlive the window it promises. Twenty‑four hours is why the promise is honest — a thirty‑minute window would make it a lie. `{total_amount}` is again the order total after discount. Not emitted for `abandoned`: `abandon_payment` transitions the payment only, cancels nothing and emits nothing — a silent beat‑task outcome on a customer who never reached the gateway | email + telegram | «پرداخت سفارش {order_number} به مبلغ {total_amount} تومان انجام نشد و مبلغی از حساب شما کسر نشده است. سفارش شما تا {order_ttl_hours} ساعت آینده محفوظ است و می‌توانید دوباره پرداخت کنید: {retry_url}» |
| C4 | `item.awaiting_input` — QUEUED → AWAITING_INPUT. The 48h re-reminder reuses this template; the 7-day flag is operator-side; at 14 days C15 (final warning) goes out; at 21 days the system cancels with a refund record (owner-review ladder, ADR-0009) | email + telegram | «برای انجام سفارش {order_number} به اطلاعات بیشتری نیاز داریم. لطفاً از پنل کاربری تکمیل کنید: {order_url} — تا دریافت اطلاعات، زمان تحویل متوقف است.» |
| C5 | `item.delivered` (first delivery) / `item.replaced` (A10a redeliver — new DeliveryField generation written, previous set `is_current=False`). Same template both times: a new OrderItemEvent row = a new key = a legitimate re‑send | email + telegram | «سفارش {order_number} ({product_name}) تحویل شد. مشاهده اطلاعات تحویل (لینک یکبارمصرف، تا ۷۲ ساعت معتبر): {delivery_link} — یا از حساب کاربری: {order_url}» — no credential values (D13-amended); the link opens the masked view, each reveal is logged |
| C6 | `item.supply_delayed` — emitted by `orders.extend_due_at(item, new_due_at, note, actor)`; the item **stays QUEUED**, the event row is an annotation (`from == to == QUEUED`) | email + telegram | «تحویل سفارش {order_number} با تاخیر مواجه شده است. زمان تحویل جدید: {new_due_at_jalali}. پوزش ما را بپذیرید.» |
| C7 | `item.cancelled` (money owed) — → CANCELLED on a paid item, via `payments.cancel_with_refund(...)` which writes the Refund row in the same transaction (R1). A delivered item can only reach here through REPLACEMENT_REQUESTED — DELIVERED → CANCELLED is forbidden (R3) | email + telegram | «سفارش {order_number} لغو شد. مبلغ {refund_amount} تومان طی حداکثر ۷۲ ساعت به حساب شما بازگردانده می‌شود.» *(the destination is the card the gateway payment came from; only when the refund must go out as a manual bank transfer and that card is unusable does the message ask for a card/SHEBA on {order_url}, and the ۷۲-hour clock then starts from that submission)* |
| C8 | `item.refunded` — Refund.executed_at set, item → REFUNDED. Covers both routes (ADR-0019): the gateway's refund API where the provider supports it, otherwise a manual bank transfer. The payload carries the **route‑agnostic** `refund_ref`, never `bank_ref` — it renders `Refund.gateway_refund_ref` or `Refund.bank_ref`, whichever the executed route filled. It names **no card**: a gateway refund returns to the original card automatically and we never learn its digits, so there is no `{destination_last4}` placeholder to fill, and the manual route's destination is not something to echo back at the customer | email + telegram | «مبلغ {refund_amount} تومان بابت سفارش {order_number} بازگردانده شد. شماره پیگیری: {refund_ref}» |
| C9 | `subscription.expiring_soon` — daily beat, `expires_at − 7d` (dedupe family `renew7:`) | email + telegram | «اشتراک {product_name} شما {expires_at_jalali} به پایان می‌رسد. برای تمدید: {renewal_url}» |
| C10 | `subscription.expired` — daily beat, day of expiry (dedupe family `renew0:`) | email + telegram | «اشتراک {product_name} شما امروز به پایان می‌رسد. تمدید: {renewal_url}» |
| C11 | `sla.holiday_paused` / `sla.holiday_resumed` — the mass action runs **per‑item** (one transaction, one annotation event, one notification per affected customer — R14) | email + telegram | Pause: «به دلیل تعطیلی، زمان تحویل سفارش {order_number} موقتاً متوقف شده است. {holiday_message}» / Resume: «رسیدگی به سفارش {order_number} از سر گرفته شد. زمان تحویل جدید: {due_at_jalali}» |
| C12 | Login OTP — auth flow (D6) | email, **or telegram if linked** | «کد ورود شما به پرم‌شاپ: {otp_code} — تا ۵ دقیقه معتبر است. اگر شما درخواست نداده‌اید، این پیام را نادیده بگیرید.» — **bypasses the outbox** (direct Celery task, single attempt + one immediate retry; a backed‑off OTP is a dead OTP) |
| C13 | `item.replacement_rejected` — A10b reject‑claim: REPLACEMENT_REQUESTED → DELIVERED with an operator actor and a **required** note, **no** new DeliveryField generation (R4). Carries no `{delivery_link}` — a rejection changes nothing about credential sensitivity (ADR-0008 constraint 6) | email + telegram | «بررسی درخواست تعویض سفارش {order_number} انجام شد و درخواست پذیرفته نشد. دلیل: {reason_note} — اطلاعات تحویل شما بدون تغییر است: {order_url}» |
| C14 | `auth.otp_signin` — an OTP-created session on an account that has a usable password (login page or checkout-inline; owner-review guard, ADR-0012) | email + telegram if linked | «ورود با کد یکبارمصرف به حساب پرم‌شاپ شما انجام شد ({ts_jalali}). اگر شما نبودید، همین حالا رمز عبور را تغییر دهید: {account_url}» |
| C15 | `item.input_final_warning` — 14 days in AWAITING_INPUT (owner-review ladder) | email + telegram | «یادآوری نهایی: سفارش {order_number} در انتظار اطلاعات شماست. در صورت عدم تکمیل تا {deadline_jalali}، سفارش لغو و مبلغ آن بازگردانده می‌شود: {order_url}» |

C9/C10 apply to `legacy`‑channel backfilled items exactly like web/bot ones (D12) — that's the point of the backfill. The backfill run **itself** is silent: creation → DELIVERED with `actor=system`, no payment rows, **no notifications** (R7); the renewal reminders are the first thing a backfilled customer ever hears from us.

### 2.2 Operator alerts (D17: order/payment alerts on BOTH channels)

Operator rows are `Notification` rows with `user = NULL`; "telegram (op)" / "email (op)" below means exactly that — the same two-value `channel` enum, addresses from settings. **The new‑order alert fires on `payment.verified`, not on order creation** (ADR-0019): an unpaid order is not work, and with a gateway the gap between the two is seconds. O1 is the alert; it ships on **both channels**.

At‑a‑glance content per the queue's needs: order number, product/plan (+qty), amount actually charged, how it was paid (gateway + its reference, or the manual fallback), customer identity/history where it changes the action, delivery deadline, deep link into the operator panel.

| # | Event | Channels | Persian template |
|---|---|---|---|
| O1 | `payment.verified` — money is in, items are QUEUED — **action: fulfill**. Fires identically whether the confirmation came from the gateway callback, the lost‑callback inquiry beat, or the operator's manual fallback; `{payment_source}` renders as «درگاه {gateway_name}» or «ثبت دستی» and `{payment_ref}` as the gateway reference or the operator's reference note. Idempotent confirm ⇒ one alert per payment per channel | telegram (op) + email (op) | «✅ پرداخت تایید شد — سفارش {order_number} — {product_plan_list} ×{qty} — {total_amount} تومان — {payment_source} / {payment_ref} — {customer_email} ({prior_orders_count} خرید قبلی) — مهلت تحویل {due_at_jalali}. {op_panel_url}» |
| ~~O2~~ | ~~`payment.submitted` — customer claims paid / uploads receipt~~ — **RETIRED with card‑to‑card (ADR-0019 supersedes ADR-0006)**. There is no customer payment claim, no receipt and no amount matching left to alert about | — | — |
| ~~O3~~ | ~~Gateway payment confirmed~~ — **merged into O1**, which is now exactly this alert. Numbering kept so cross‑references keep working | — | — |
| O4 | `item.input_received` — customer supplied awaited input (AWAITING_INPUT → QUEUED, SLA resumed) | telegram (op) | «📥 اطلاعات سفارش {order_number} تکمیل شد — ساعت SLA دوباره فعال است — مهلت {due_at_jalali}. {op_panel_url}» |
| O5 | `item.cancellation_requested` (`cancellation_requested_at` set — not a status, D1) | telegram (op) | «⛔ درخواست لغو سفارش {order_number} — {product_plan_list} — پرداخت: {payment_status}. {op_panel_url}» |
| O6 | `item.replacement_requested` (→ REPLACEMENT_REQUESTED) | telegram (op) | «🔁 درخواست تعویض سفارش {order_number} — {product_name} — تحویل {delivered_at_jalali}، گارانتی {warranty}. {op_panel_url}» |
| O7 | `items.overdue_digest` — **the only overdue cadence**: an hourly beat during support hours, one message if any item is past `due_at` (R13). No per‑item-once and no per‑item‑per‑day variant exists; the AWAITING_INPUT 7‑day flag (R5) shows in the queue, not here | telegram (op) | «⚠️ {count} سفارش گذشته از مهلت: {order_numbers}. {op_queue_url}» |
| ~~O8~~ | ~~Unmatched transfer logged~~ — **RETIRED**: the UnmatchedTransfer ledger existed to catch card‑to‑card money that matched no order. A gateway payment always carries its order (ADR-0019) | — | — |

O1 is the alert the business lives on (brief §11: without it the 48h SLA burns silently). Going out on both channels means a dead Telegram still wakes the operator by email and vice versa.

---

## 3. Outbox Mechanics (D17, D18)

### 3.1 Flow

Services never send anything. Inside a transition transaction: state change + `OrderItemEvent` row. After commit (`transaction.on_commit`), `events.emit(name, payload)` runs, which writes one `Notification` row **per (occurrence, recipient, channel)** and enqueues the send task. Transitions that notify emit; transitions that don't, don't — `item.queued` has no consumer and is no longer emitted (R11). The legacy backfill (`Order.channel='legacy'`) writes its DELIVERED event and sends **nothing** (R7). A beat sweeper (every minute) also enqueues any `pending` row whose `next_attempt_at` is due — so a lost enqueue or dead worker at emit time self‑heals.

### 3.2 Table: `notifications.Notification` (R9)

| Field | Notes |
|---|---|
| `dedupe_key` | `CharField`, **unique** — the only duplicate guard |
| `event_type` | canonical event name (R11), e.g. `item.delivered`, `payment.verified` |
| `channel` | `email` \| `telegram` — exactly **two** values, CHECK‑constrained. No `op_*` channels: the recipient, not the channel, says who it is for. IN_APP is cut for phase 1 (see §2) |
| `user` FK | nullable — **NULL means the operator is the recipient**; operator addresses come from settings |
| `order` FK, `order_item` FK | both nullable (D17) — a payment‑level or digest message has no item |
| `payload` | JSON template context. **Never credential values, never customer_input**; may carry the delivery‑link token (a bearer capability) — excluded from Sentry and scrubbed from logs (D20, ADR-0008) |
| `status` | `pending` \| `sent` \| `failed` (terminal) — CHECK‑constrained, exactly **three** values. There is deliberately **no `sending` state** |
| `attempts`, `next_attempt_at`, `last_error`, `created_at`, `sent_at` | |

No `sending` state is needed because the row lock *is* the in‑flight marker: the send task selects with `select_for_update(skip_locked=True)`, so sweeper + on‑commit enqueue can never double‑send.

### 3.3 dedupe_key construction rule

Every key is **`{occurrence}:{recipient}:{channel}`** (R9), where `recipient` is the user id or the literal `op` for operator rows — e.g. `pay:1041:verified:op:telegram`, `renew7:5512:2026-10-01:8:email`. One `{occurrence}` rule per trigger family:

| Trigger family | Key | Why it's re‑send‑safe |
|---|---|---|
| OrderItem transition | `evt:{order_item_event_id}:{recipient}:{channel}` | A replacement redelivery (A10a) writes a **new** OrderItemEvent row → new key → legitimately re‑sends C5; the A10b reject‑claim event likewise gets its own key (C13). Exactly the failure of the brief's `unique_together(order_item, event_type, channel)` idea (also NULL‑hostile), which D17 overrides |
| Payment transition (C2, C3, **and operator O1**) | `pay:{payment_id}:{to_status}:{recipient}:{channel}` | This is the family that makes ADR-0019's idempotence visible: because `payments.confirm_payment` is the sole emitter of `payment.verified`, a duplicate callback, a browser refresh, a double submit, and the lost‑callback inquiry beat racing a late callback all confirm the *same* payment to the same `to_status`, so they all produce the same key — one customer message and one operator alert, whichever path got there first. `record_manual_payment` routes through the same helper on the same payment row and so lands on the same key too. O1 differs from C2 only in `recipient` (`op` vs the user id), so it needs no family of its own. A retried, genuinely new attempt is a **new Payment row** → new `payment_id` → its own key, which is why a customer who fails once and succeeds once correctly gets both C3 and C2 |
| Renewal reminders (`subscription.expiring_soon` / `subscription.expired`) | `renew7:{order_item_id}:{expires_at_iso}:{recipient}:{channel}` / `renew0:...` | The key **includes `expires_at`** on purpose: extending an item's `expires_at` later legitimately re‑arms the reminder, and a renewal is a new OrderItem anyway |
| Holiday pause/resume | `hpause:{order_item_id}:{date_iso}:{recipient}:{channel}` / `hresume:...` | Multiple holidays per item allowed; the mass action is per‑item (R14) |
| Supply delay (`orders.extend_due_at`) | `delay:{order_item_id}:{new_due_at_iso}:{recipient}:{channel}` | Each new promised date is its own occurrence (R12) |
| Awaiting‑input ladder (48h + 14d beats) | `remind:{order_item_id}:48h:{recipient}:{channel}` / `remind:{order_item_id}:14d:{recipient}:{channel}` | One send per stage per item; the 7‑day step only flags the operator queue; the 21‑day stage is the cancel event itself (`evt:` family) |
| New‑sign‑in alert (C14) | `signin:{user_id}:{ts_iso}:{recipient}:{channel}` | One per OTP session creation on a passworded account |
| Cancellation request (O5, `item.cancellation_requested`) | `cancelreq:{item_id}:{requested_at}:op:{channel}` | A field‑set action, not a transition (D1), so there is no event row to key off. Including `requested_at` means a withdrawn‑then‑re‑requested cancellation is a new occurrence and alerts again |
| Overdue digest (`items.overdue_digest`) | `overdue:{date_iso}T{hour}:op:{channel}` | At most one per hour, operator‑only |
| Refund executed (`item.refunded`) | `refund:{refund_id}:{recipient}:{channel}` | Refund rows are append‑only; the key is the same whether the money went back through the gateway's refund API (`gateway_refund_ref`) or a manual transfer (`bank_ref`) |

Insert uses `INSERT ... ON CONFLICT DO NOTHING` (Django: catch `IntegrityError` / `bulk_create(ignore_conflicts=True)`) — duplicate emission is silent and cheap.

### 3.4 Retry / backoff / terminal failure

- Schedule (canonical, R10): attempt 1 immediately, then **1 m → 5 m → 15 m → 1 h → 6 h** (6 attempts total, ~7¼ h span — inside the 24–48 h promise window, beyond which a notification is stale anyway).
- After the last attempt: `status = failed` (terminal). No automatic resurrection; the staff panel has a "retry now" button that resets `attempts`/`next_attempt_at`.
- **Non‑retryable errors fail immediately**: Telegram `403 Forbidden` (user blocked the bot — also flag the user row so the staff panel shows the broken channel), `400 chat not found`; email hard bounces. Retry only on timeouts, 429 (honor `retry_after`), 5xx.

### 3.5 Degradation

- Channels are **independent rows**: email provider down → the telegram row for the same event sends normally, and vice versa. No cross‑channel fallback logic in phase 1 — the customer order pages are always the source of truth and every message links to them.
- **Both customer channels terminally failed** → visible in: (a) staff dashboard "اعلان‌های ناموفق" counter + filtered list (admin over `Notification`, `status=failed`), (b) one Sentry event per terminal failure (event name + row id only — no payload, D20).
- **Operator alert (O1) terminally failed on both channels** → Sentry alert at error level; because it is dual‑channel, a single dead channel loses nothing. This is the one alert where a total loss means paid, queued work nobody knows about — the SLA clock is already running.
- **Everything dead (worker/beat down)** → nothing in‑app can alert, by definition. That is what the **dead‑man heartbeat** is for (D17/D22): a beat task pings a healthchecks.io‑class URL every 5 minutes; the external service alarms when pings stop. A dead beat also stops the lost‑callback inquiry task (ADR-0019), which is money taken with the order left dead — so the heartbeat ships with the minimal operator‑alert step, alongside the gateway integration (D22).

---

## 4. Phase‑3 API Sketch — **NOT BUILT IN PHASE 1** (D4)

For the record only; nothing below exists in the phase‑1 skeleton (no `/api/v1`, no `serializers.py`).

**Auth**: the mini‑app posts Telegram WebApp `initData` to `POST /api/v1/auth/telegram/`. Server validates the HMAC (secret key = `HMAC_SHA256("WebAppData", bot_token)`, verify `hash` over the sorted data‑check string, reject `auth_date` older than ~5 min), resolves `telegram_id` → linked `User` (the phase‑1 linking flow is the enabler), returns a short‑lived token. Unlinked users get a 409 pointing at the link flow.

| Endpoint | Backs onto (already exists from phase 1) |
|---|---|
| `POST /api/v1/auth/telegram/` | `accounts.services.link/lookup` |
| `GET /api/v1/catalog/products/`, `GET .../products/{slug}/` | `catalog.selectors` |
| `POST /api/v1/orders/` (plan, qty, customer_input) | `payments.services.checkout(...)` — the one orchestrating service the web checkout view calls too (R2) |
| `GET /api/v1/orders/`, `GET /api/v1/orders/{id}/` (masked delivery, ownership‑checked) | `orders.selectors` |
| `POST /api/v1/orders/{id}/cancel-request/` | `orders.services.request_cancellation` |
| `POST /api/v1/payments/{id}/init/` | plain `payments.services` functions against the one gateway — a `PaymentProvider` interface stays **deferred** (ADR-0013, reaffirmed): there is still exactly one implementation, so the abstraction would have one caller. Extract it if and when a second provider is added. `Payment.method` / `gateway_name` already keep the door open |
| `GET /api/v1/track/{tracking_token}/` | same selector as the public tracking page (D12 redactions apply) |

**Why this is cheap later**: every endpoint is serializer + one existing service/selector call. State machine, ownership checks, money math, snapshots, notifications all live below the view layer (D18/D21), so the API adds transport, not behavior — exactly the brief's "two displays for one service layer" principle.

---

## Concerns

1. **A failed payment now has a customer‑facing template (C3), which the card‑to‑card design lacked.** The gap this closes is real: under the old flow the customer learned of a rejected payment only from their order page. What C3 must never do is imply the order is gone — `fail_payment` touches the payment only, leaving the order `PENDING_PAYMENT` and payable, and the message says so; if that ever changed, the retry link in C3 would be a lie. The promise is bounded and the bound is the reason `unpaid_order_ttl_hours` defaults to **24** rather than something tidier: the message hands the customer a retry link, so the window has to be long enough for a person to come back to it. Shortening that setting shortens the truth of C3 — the message renders `{order_ttl_hours}` from the setting so the two can never drift, but a thirty‑minute value would still make the reassurance worthless. The sweep also **skips** any order whose payment is still `initiated`: never cancel while money may be moving. Open nit: `abandoned` (the inquiry beat's verdict on a customer who never reached the gateway) cancels nothing and sends nothing at all. Silence is right for a customer who changed their mind; revisit if abandoned orders turn out to be recoverable with a nudge.
2. **C3/C7/C13 promise wording** ("مبلغی کسر نشده" on a failed payment, the "۷۲ ساعت" refund window, replacement‑rejection phrasing) is public policy text — per the working agreement §14 these strings need owner sign‑off, not just code review. The "no money was deducted" claim in C3 is the sharpest of the three: it is true for a failed or rejected gateway result, and it is exactly the claim the lost‑callback inquiry beat (ADR-0019) exists to keep honest.
3. **Login OTP bypasses the outbox** (C12): dedupe/backoff semantics are wrong for a 5‑minute code. This is a deliberate carve‑out from D17's "outbox for notifications," flagged here so it isn't read as a violation.
