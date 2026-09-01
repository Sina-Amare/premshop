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
- Token valid → `accounts.services.link_telegram(user_id, telegram_id, telegram_username)`. Enforces one‑to‑one both ways: if this `telegram_id` is already linked to another user, or the user already has a different `telegram_id`, refuse with an explanatory message (unlink happens on the site, not in the bot). Success sets `telegram_linked_at` and replies:
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

No browsing, ordering, or payment. No order‑status queries (`/orders` etc.). No credential delivery of any kind — ever (D13). No inline keyboards with callbacks, no inline mode, no mini‑app, no media/receipt intake, no support chat relay, no group behavior, no unlinking (site only). Conversational bot + mini‑app arrive in phase 3.

---

## 2. Outbound Message Catalog

Channels: exactly **two** — `email` and `telegram` (customer telegram only if linked). There is no separate operator channel: an operator message is simply a `Notification` row with `user = NULL`; the operator email address and Telegram chat_id come from settings (R9). D13 rule (as amended at owner review): messages never carry credential **values**, `customer_input`, or card numbers other than the customer's own last4 and the shop's destination card — the single-use `{delivery_link}` capability is the one sanctioned addition (ADR-0008).

Links (R16): customer messages link to the customer order pages `{site}/orders/{order_number}/` (behind login, placeholder `{order_url}`) or the tracking page `{site}/t/{tracking_token}/` — **never** to `/panel/*`; the word "panel" is reserved for the staff app. New at owner review (ADR-0008): `{delivery_link}` = `{site}/d/{token}/` — a **single-use, 72h** delivery link opening that item's *masked* credential view without login (reveal still logged); carried only by delivery/replacement messages, always alongside the `{order_url}` fallback; a bearer capability, never a credential value; scrubbed from logs and Sentry. Operator alerts link to `{site}/panel/queue/{order_item_id}` (placeholder `{op_panel_url}`); the overdue digest links to the queue list itself, `{site}/panel/queue/` (placeholder `{op_queue_url}`). Renewal = product page URL.

IN_APP is **cut for phase 1** — the customer order pages are the in‑app surface; the channel enum extends cleanly if a bell/inbox is ever wanted.

All numbers rendered with Persian digits + thousands separators; dates Jalali (core format helpers).

### 2.1 Customer notifications

| # | Event (trigger) | Channels | Persian template (placeholders in `{}`) |
|---|---|---|---|
| ~~C1~~ | ~~Order registered~~ — **CUT from phase 1 (R15)** | — | The payment-instructions page already carries this, and the customer order-detail page re‑displays the payment instructions + unique amount + deadline for as long as the order is `PENDING_PAYMENT`. `order.created` notifies the **operator only** (O1). Numbering below is unchanged so cross‑references keep working |
| C2 | `payment.confirmed` — payment → confirmed (items → QUEUED). Single emitter: `payments.confirm_payment`; `orders.mark_order_paid` emits nothing (R11) | email + telegram | «پرداخت سفارش {order_number} تایید شد. سفارش شما حداکثر تا {due_at_jalali} تحویل می‌شود. پیگیری: {tracking_url}» |
| C3 | `payment.expired` — the sweep expires **`pending` payments only** (a `submitted` payment never auto‑expires; it waits for the operator's confirm/reject — R6). So this message only ever reaches a silent non‑payer: someone who never pressed the "I have paid" button. The revive reassurance below is for the customer who *did* transfer the money and simply never claimed it | email + telegram | «مهلت پرداخت سفارش {order_number} به پایان رسید و سفارش لغو شد. اگر پرداخت را انجام داده‌اید، نگران نباشید — پس از بررسی، سفارش فعال می‌شود.» *(revive path, D2)* |
| C4 | `item.awaiting_input` — QUEUED → AWAITING_INPUT. The 48h re-reminder reuses this template; the 7-day flag is operator-side; at 14 days C15 (final warning) goes out; at 21 days the system cancels with a refund record (owner-review ladder, ADR-0009) | email + telegram | «برای انجام سفارش {order_number} به اطلاعات بیشتری نیاز داریم. لطفاً از پنل کاربری تکمیل کنید: {order_url} — تا دریافت اطلاعات، زمان تحویل متوقف است.» |
| C5 | `item.delivered` (first delivery) / `item.replaced` (A10a redeliver — new DeliveryField generation written, previous set `is_current=False`). Same template both times: a new OrderItemEvent row = a new key = a legitimate re‑send | email + telegram | «سفارش {order_number} ({product_name}) تحویل شد. مشاهده اطلاعات تحویل (لینک یکبارمصرف، تا ۷۲ ساعت معتبر): {delivery_link} — یا از حساب کاربری: {order_url}» — no credential values (D13-amended); the link opens the masked view, each reveal is logged |
| C6 | `item.supply_delayed` — emitted by `orders.extend_due_at(item, new_due_at, note, actor)`; the item **stays QUEUED**, the event row is an annotation (`from == to == QUEUED`) | email + telegram | «تحویل سفارش {order_number} با تاخیر مواجه شده است. زمان تحویل جدید: {new_due_at_jalali}. پوزش ما را بپذیرید.» |
| C7 | `item.cancelled` (money owed) — → CANCELLED on a paid item, via `payments.cancel_with_refund(...)` which writes the Refund row in the same transaction (R1). A delivered item can only reach here through REPLACEMENT_REQUESTED — DELIVERED → CANCELLED is forbidden (R3) | email + telegram | «سفارش {order_number} لغو شد. مبلغ {refund_amount} تومان طی حداکثر ۷۲ ساعت به حساب شما بازگردانده می‌شود.» *(input_timeout auto-cancel variant: no destination is known yet — the message instead asks the customer to submit their card/SHEBA on {order_url}; the ۷۲-hour clock starts from that submission)* |
| C8 | Refund executed — Refund.executed_at set, item → REFUNDED | email + telegram | «مبلغ {refund_amount} تومان بابت سفارش {order_number} به کارت {destination_last4}**** بازگردانده شد. شماره پیگیری: {bank_ref}» |
| C9 | Renewal, 7 days — daily beat, `expires_at − 7d` | email + telegram | «اشتراک {product_name} شما {expires_at_jalali} به پایان می‌رسد. برای تمدید: {renewal_url}» |
| C10 | Renewal, expiry day — daily beat | email + telegram | «اشتراک {product_name} شما امروز به پایان می‌رسد. تمدید: {renewal_url}» |
| C11 | `sla.holiday_paused` / `sla.holiday_resumed` — the mass action runs **per‑item** (one transaction, one annotation event, one notification per affected customer — R14) | email + telegram | Pause: «به دلیل تعطیلی، زمان تحویل سفارش {order_number} موقتاً متوقف شده است. {holiday_message}» / Resume: «رسیدگی به سفارش {order_number} از سر گرفته شد. زمان تحویل جدید: {due_at_jalali}» |
| C12 | Login OTP — auth flow (D6) | email, **or telegram if linked** | «کد ورود شما به پرم‌شاپ: {otp_code} — تا ۵ دقیقه معتبر است. اگر شما درخواست نداده‌اید، این پیام را نادیده بگیرید.» — **bypasses the outbox** (direct Celery task, single attempt + one immediate retry; a backed‑off OTP is a dead OTP) |
| C13 | `item.replacement_rejected` — A10b reject‑claim: REPLACEMENT_REQUESTED → DELIVERED with an operator actor and a **required** note, **no** new DeliveryField generation (R4). Carries no `{delivery_link}` — a rejection changes nothing about credential sensitivity (ADR-0008 constraint 6) | email + telegram | «بررسی درخواست تعویض سفارش {order_number} انجام شد و درخواست پذیرفته نشد. دلیل: {reason_note} — اطلاعات تحویل شما بدون تغییر است: {order_url}» |
| C14 | `auth.otp_signin` — an OTP-created session on an account that has a usable password (login page or checkout-inline; owner-review guard, ADR-0012) | email + telegram if linked | «ورود با کد یکبارمصرف به حساب پرم‌شاپ شما انجام شد ({ts_jalali}). اگر شما نبودید، همین حالا رمز عبور را تغییر دهید: {account_url}» |
| C15 | `item.input_final_warning` — 14 days in AWAITING_INPUT (owner-review ladder) | email + telegram | «یادآوری نهایی: سفارش {order_number} در انتظار اطلاعات شماست. در صورت عدم تکمیل تا {deadline_jalali}، سفارش لغو و مبلغ آن بازگردانده می‌شود: {order_url}» |

C9/C10 apply to `legacy`‑channel backfilled items exactly like web/bot ones (D12) — that's the point of the backfill. The backfill run **itself** is silent: creation → DELIVERED with `actor=system`, no payment rows, **no notifications** (R7); the renewal reminders are the first thing a backfilled customer ever hears from us.

### 2.2 Operator alerts (D17: order/payment alerts on BOTH channels)

Operator rows are `Notification` rows with `user = NULL`; "telegram (op)" / "email (op)" below means exactly that — the same two-value `channel` enum, addresses from settings. **Phase 1 ships O1 (`order.created`) + O2 (`payment.submitted`), both channels** (R20b); O3 arrives with the gateway.

At‑a‑glance content per the queue's needs: order number, product/plan (+qty), amount, payment method, customer identity/history where it changes the action, deep link into the operator panel.

| # | Event | Channels | Persian template |
|---|---|---|---|
| O1 | `order.created` — new order registered (**phase 1**) | telegram (op) + email (op) | «🆕 سفارش {order_number} — {product_plan_list} ×{qty} — {total_amount} تومان — {payment_method} — در انتظار پرداخت. {op_panel_url}» |
| O2 | `payment.submitted` — customer claims paid / uploads receipt (**phase 1**) — **action: match** | telegram (op) + email (op) | «💳 ادعای پرداخت سفارش {order_number} — مبلغ یکتا {unique_amount} تومان — کارت مقصد …{destination_last4} — {customer_email} ({prior_orders_count} خرید قبلی) — رسید: {has_receipt}. تطبیق: {op_panel_url}» *(destination from the Payment's card snapshot — tells the operator which bank app to check)* |
| O3 | **Gateway‑phase only, not built in phase 1 (R20b)** — payment confirmed by a gateway — **action: fulfill**. In phase 1 the operator confirms the payment themselves, so alerting them about it is noise; `payment.confirmed` notifies the customer only (C2) | telegram (op) + email (op) | «✅ پرداخت تایید شد — سفارش {order_number} — {product_plan_list} — {amount} تومان — درگاه {gateway_name} — مهلت تحویل {due_at_jalali}. {op_panel_url}» |
| O4 | `item.input_received` — customer supplied awaited input (AWAITING_INPUT → QUEUED, SLA resumed) | telegram (op) | «📥 اطلاعات سفارش {order_number} تکمیل شد — ساعت SLA دوباره فعال است — مهلت {due_at_jalali}. {op_panel_url}» |
| O5 | `item.cancellation_requested` (`cancellation_requested_at` set — not a status, D1) | telegram (op) | «⛔ درخواست لغو سفارش {order_number} — {product_plan_list} — پرداخت: {payment_status}. {op_panel_url}» |
| O6 | `item.replacement_requested` (→ REPLACEMENT_REQUESTED) | telegram (op) | «🔁 درخواست تعویض سفارش {order_number} — {product_name} — تحویل {delivered_at_jalali}، گارانتی {warranty}. {op_panel_url}» |
| O7 | `item.overdue` digest — **the only overdue cadence**: an hourly beat during support hours, one message if any item is past `due_at` (R13). No per‑item-once and no per‑item‑per‑day variant exists; the AWAITING_INPUT 7‑day flag (R5) shows in the queue, not here | telegram (op) | «⚠️ {count} سفارش گذشته از مهلت: {order_numbers}. {op_queue_url}» |
| O8 | Unmatched transfer logged (operator records money matching nothing — self‑created, email only for the paper trail) | email (op) | «ثبت واریز نامشخص: {amount} تومان — {bank_ref} — {received_at}» |

O2 is the alert the business lives on (brief §11: without it the 48h SLA burns silently). O1 and O2 going out on both channels means a dead Telegram still wakes the operator by email and vice versa.

---

## 3. Outbox Mechanics (D17, D18)

### 3.1 Flow

Services never send anything. Inside a transition transaction: state change + `OrderItemEvent` row. After commit (`transaction.on_commit`), `events.emit(name, payload)` runs, which writes one `Notification` row **per (occurrence, recipient, channel)** and enqueues the send task. Transitions that notify emit; transitions that don't, don't — `item.queued` has no consumer and is no longer emitted (R11). The legacy backfill (`Order.channel='legacy'`) writes its DELIVERED event and sends **nothing** (R7). A beat sweeper (every minute) also enqueues any `pending` row whose `next_attempt_at` is due — so a lost enqueue or dead worker at emit time self‑heals.

### 3.2 Table: `notifications.Notification` (R9)

| Field | Notes |
|---|---|
| `dedupe_key` | `CharField`, **unique** — the only duplicate guard |
| `event_type` | canonical event name (R11), e.g. `item.delivered`, `payment.submitted` |
| `channel` | `email` \| `telegram` — exactly **two** values, CHECK‑constrained. No `op_*` channels: the recipient, not the channel, says who it is for. IN_APP is cut for phase 1 (see §2) |
| `user` FK | nullable — **NULL means the operator is the recipient**; operator addresses come from settings |
| `order` FK, `order_item` FK | both nullable (D17) — a payment‑level or digest message has no item |
| `payload` | JSON template context. **Never credential values, never customer_input**; may carry the delivery‑link token (a bearer capability) — excluded from Sentry and scrubbed from logs (D20, ADR-0008) |
| `status` | `pending` \| `sent` \| `failed` (terminal) — CHECK‑constrained, exactly **three** values. There is deliberately **no `sending` state** |
| `attempts`, `next_attempt_at`, `last_error`, `created_at`, `sent_at` | |

No `sending` state is needed because the row lock *is* the in‑flight marker: the send task selects with `select_for_update(skip_locked=True)`, so sweeper + on‑commit enqueue can never double‑send.

### 3.3 dedupe_key construction rule

Every key is **`{occurrence}:{recipient}:{channel}`** (R9), where `recipient` is the user id or the literal `op` for operator rows — e.g. `order:1041:created:op:telegram`, `renew7:5512:2026-10-01:8:email`. One `{occurrence}` rule per trigger family:

| Trigger family | Key | Why it's re‑send‑safe |
|---|---|---|
| OrderItem transition | `evt:{order_item_event_id}:{recipient}:{channel}` | A replacement redelivery (A10a) writes a **new** OrderItemEvent row → new key → legitimately re‑sends C5; the A10b reject‑claim event likewise gets its own key (C13). Exactly the failure of the brief's `unique_together(order_item, event_type, channel)` idea (also NULL‑hostile), which D17 overrides |
| Payment transition | `pay:{payment_id}:{to_status}:{recipient}:{channel}` | Operator confirm is idempotent (D2) — the second confirm produces the same key, insert is a no‑op |
| Renewal reminders | `renew7:{order_item_id}:{expires_at_date_iso}:{recipient}:{channel}` / `renew0:...` | The key **includes `expires_at`** on purpose: extending an item's `expires_at` later legitimately re‑arms the reminder, and a renewal is a new OrderItem anyway |
| Holiday pause/resume | `hpause:{order_item_id}:{date_iso}:{recipient}:{channel}` / `hresume:...` | Multiple holidays per item allowed; the mass action is per‑item (R14) |
| Supply delay (`orders.extend_due_at`) | `delay:{order_item_id}:{new_due_at_iso}:{recipient}:{channel}` | Each new promised date is its own occurrence (R12) |
| Awaiting‑input ladder (48h + 14d beats) | `remind:{order_item_id}:48h:{recipient}:{channel}` / `remind:{order_item_id}:14d:{recipient}:{channel}` | One send per stage per item; the 7‑day step only flags the operator queue; the 21‑day stage is the cancel event itself (`evt:` family) |
| New‑sign‑in alert (C14) | `signin:{user_id}:{ts_iso}:{recipient}:{channel}` | One per OTP session creation on a passworded account |
| Overdue digest | `overdue:{date_iso}T{hour}:op:{channel}` | At most one per hour, operator‑only |
| Refund executed | `refund:{refund_id}:{recipient}:{channel}` | Refund rows are append‑only |
| Order created (operator alert O1) | `order:{order_id}:created:op:{channel}` | Customer‑side C1 is cut (R15), so this key only ever has `op` as recipient |

Insert uses `INSERT ... ON CONFLICT DO NOTHING` (Django: catch `IntegrityError` / `bulk_create(ignore_conflicts=True)`) — duplicate emission is silent and cheap.

### 3.4 Retry / backoff / terminal failure

- Schedule (canonical, R10): attempt 1 immediately, then **1 m → 5 m → 15 m → 1 h → 6 h** (6 attempts total, ~7¼ h span — inside the 24–48 h promise window, beyond which a notification is stale anyway).
- After the last attempt: `status = failed` (terminal). No automatic resurrection; the staff panel has a "retry now" button that resets `attempts`/`next_attempt_at`.
- **Non‑retryable errors fail immediately**: Telegram `403 Forbidden` (user blocked the bot — also flag the user row so the staff panel shows the broken channel), `400 chat not found`; email hard bounces. Retry only on timeouts, 429 (honor `retry_after`), 5xx.

### 3.5 Degradation

- Channels are **independent rows**: email provider down → the telegram row for the same event sends normally, and vice versa. No cross‑channel fallback logic in phase 1 — the customer order pages are always the source of truth and every message links to them.
- **Both customer channels terminally failed** → visible in: (a) staff dashboard "اعلان‌های ناموفق" counter + filtered list (admin over `Notification`, `status=failed`), (b) one Sentry event per terminal failure (event name + row id only — no payload, D20).
- **Operator alert (O1 / O2) terminally failed on both channels** → Sentry alert at error level; and because both are dual‑channel, a single dead channel loses nothing.
- **Everything dead (worker/beat down)** → nothing in‑app can alert, by definition. That is what the **dead‑man heartbeat** is for (D17/D22): a beat task pings a healthchecks.io‑class URL every 5 minutes; the external service alarms when pings stop. This ships with the minimal operator‑alert step, immediately after card‑to‑card (D22).

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
| `POST /api/v1/payments/{id}/init/` | `payments` provider adapter — **deferred, not phase 1** (R19): phase 1 ships card‑to‑card as plain `payments.services` functions, and the adapter interface gets extracted in phase 1.5 when Zibal (a second, real implementation) defines its shape. `Payment.method` / `gateway_name` already keep the door open |
| `GET /api/v1/track/{tracking_token}/` | same selector as the public tracking page (D12 redactions apply) |

**Why this is cheap later**: every endpoint is serializer + one existing service/selector call. State machine, ownership checks, money math, snapshots, notifications all live below the view layer (D18/D21), so the API adds transport, not behavior — exactly the brief's "two displays for one service layer" principle.

---

## Concerns

1. **`rejected` recovery is now modeled, not open.** A rejected payment cascades its order's PENDING_PAYMENT items to CANCELLED with `reason=operator` (R8), and `payments.revive_order(payment)` accepts exactly that cancel reason, so a rejection‑in‑error is recovered by the same manual‑match/confirm path as an expiry (R2). Remaining nit: there is no customer‑facing "payment rejected" template — the customer sees the cancellation only on their order page. Add one if rejections turn out to be common.
2. **C3/C7/C13 promise wording** ("۷۲ ساعت" refund window, revive reassurance, replacement‑rejection phrasing) is public policy text — per the working agreement §14 these strings need owner sign‑off, not just code review.
3. **Login OTP bypasses the outbox** (C12): dedupe/backoff semantics are wrong for a 5‑minute code. This is a deliberate carve‑out from D17's "outbox for notifications," flagged here so it isn't read as a violation.
