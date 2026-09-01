# PremShop — Module Boundaries & Service Layer Contract (Phase 1)

## 1. App Map

Dependency direction is a strict order. An app may import only from apps **above** it in this list (plus stdlib/Django/libs). Panel sits at the top and imports everyone; nothing imports panel. `cms` and `catalog` are domain-leaf apps.

```
core  →  accounts, catalog, cms  →  orders  →  payments  →  notifications  →  panel
```

| App | Owns (models) | Owns (logic) | May import | Never |
|---|---|---|---|---|
| **core** | `SiteSetting` (singleton: holiday_stop_new_orders, holiday_message, holiday_pause_sla, support_start, support_end, off_weekdays, support_hours_display, two destination-card slots + active_card selector, payment_window_minutes=60) | `EncryptedTextField` (Fernet/MultiFernet, ~30 lines), money/digit/Jalali format helpers, Persian search normalization (yeh/kaf/half-space/digit folding), `compute_due_at` pure function, `events.emit`/`events.register`, heartbeat task | nothing domain-side | importing any other app |
| **accounts** | `User` (email USERNAME_FIELD, is_verified, phone (CharField, blank=''), telegram_id/username/linked_at), OTP + telegram-link token state | auth services: OTP login, inline checkout OTP, telegram linking both directions | core | orders, payments |
| **catalog** | `Category` (flat, no parent), `Product` (incl. delivery_template, delivery_hours, region, warranty, search_text column — deliberately unindexed: icontains cannot use btree, seq scan is fine at this catalog size, pg_trgm is the named upgrade path; status third value is "unavailable"), `ProductSpec`, `Plan` (cost_price, sale_price, requires_customer_input, duration_days, supplier_url — per-plan upstream listing, owner ruling) | selectors only (search, active listings); admin with inlines; normalization on save | core | any domain app |
| **cms** | `Page`, `FAQ` | admin-edited content, public views | core | any domain app |
| **orders** | `Order` (channel web\|bot\|legacy, tracking_token, order_number, total_amount — **no discount**), `OrderItem` (7 statuses, cost_snapshot, actual_cost, sla_paused_at, cancel_reason, cancellation_requested_at, encrypted customer_input, due_at, expires_at), `DeliveryField` (encrypted value, is_current), `OrderItemEvent`, `CredentialAccessLog` | the state machine, place_order, all transitions, delivery, SLA pause/resume, reveal logging, delivery-link issue/redeem + the no-login masked view (ADR-0008), legacy backfill, customer order pages + public tracking page | core, accounts, catalog | payments, notifications, panel. Where a transition is payment-guarded (revive, refunded), the **caller passes the Payment/Refund object in**; orders validates its attributes, never imports payments |
| **payments** | `Payment` (+ paid_amount, bank_ref, submitted_at, matched_by, matched_at, unique_amount, idempotency_key, expires_at, receipt_image), `Refund`, `UnmatchedTransfer` | payment machine (pending→submitted→confirmed\|rejected; →expired; expired→confirmed via manual match), unique-amount allocation, card-to-card as plain service functions (the PaymentProvider adapter interface is a deliberate deferral — extracted in phase 1.5 when Zibal, a second real implementation, defines its shape; `Payment.method`/`gateway_name` keep the door open), checkout/revive/cancel-with-refund orchestrators, refund ledger, receipt storage/purge (rial conversion lives ONLY here) | core, accounts, catalog, orders (calls orders services on confirm/refund-executed) | notifications, panel |
| **notifications** | `Notification` outbox (status, attempts, next_attempt_at, unique dedupe_key, nullable order FK, nullable order_item FK, channel, event_type, payload) | `notify()`, email + telegram channel senders, telegram webhook view (secret path + `X-Telegram-Bot-Api-Secret-Token`), outbox worker, event handlers registered against `core.events` at AppConfig.ready() | core, accounts, orders, payments (selectors, to render message context) | panel |
| **panel** | **no models** | operator queue, delivery page, dashboard, payment-confirm actions, manual match, refund forms, holiday switch — all staff-only views calling other apps' services/selectors | everyone's services + selectors | defining business logic; touching models directly for writes |

Responsibility placement from the brief: SLA math → core (pure) + orders (applies it) · unique-amount matching → payments · masked reveal + access log → orders · outbox/retry → notifications · operator daily loop → panel · Enamad pages → cms · search → core (normalize) + catalog (selector) · Jalali/digits → core template tags · reviews → phase-2 app, absent from skeleton · **no DRF anywhere** (D4): the bot is one webhook view in notifications calling services in-process.

---

## 2. Service Signatures

Conventions: all writes are keyword-only. `Actor = Literal["operator", "customer", "system"]`. Every transition: `transaction.atomic()` + `select_for_update()` on the row, validates the from-status (illegal transition raises `InvalidTransition`), writes an `OrderItemEvent` row **inside** the transaction, and calls `events.emit()` which fires handlers via `transaction.on_commit`. Shared exceptions: `InvalidTransition`, `NotOwner`, `RateLimited`, `django.core.exceptions.ValidationError`.

### core

```python
def compute_due_at(
    confirmed_at: datetime, delivery_hours: int,
    *, support_start: time, support_end: time, off_weekdays: frozenset[int],
) -> datetime: ...
```
Pure, no DB, no exceptions. `confirmed_at + delivery_hours`, snapped **forward** to the next support window if outside one, hard-capped at `confirmed_at + 48h` (cap applies after snapping). Exhaustively tested (D7). Callers read schedule from `SiteSetting.load()`.

```python
def emit(name: str, payload: dict[str, Any]) -> None: ...
def register(name: str, handler: Callable[[dict[str, Any]], None]) -> None: ...
```
`emit` schedules each registered handler via `transaction.on_commit` (D18). No framework, no Django signals. `notifications` registers handlers in `AppConfig.ready()`; handlers create outbox rows and kick the sender task.

```python
def normalize_fa(text: str) -> str: ...        # yeh/kaf folding, half-space, digit folding
class SiteSetting(models.Model):
    @classmethod
    def load(cls) -> "SiteSetting": ...        # cached singleton
```

### accounts

| Signature | Behavior |
|---|---|
| `def start_otp(*, email: str, purpose: Literal["login","checkout"]) -> None` | Creates unverified `User` if none (checkout doubles as signup, D6). Sends OTP via email, or Telegram if linked. Raises `RateLimited`. Tx: single insert; no events. |
| `def verify_otp(*, email: str, code: str) -> User` | Sets `is_verified=True` on first success. If the account has a usable password, emits `auth.otp_signin` — the new-sign-in security alert (email + Telegram-if-linked, via the outbox), covering both the login page and checkout-inline OTP (ADR-0012). Raises `OTPInvalid`, `OTPExpired`, `RateLimited`. Tx: atomic, code single-use (delete-on-verify). |
| `def create_telegram_link_token(*, user: User) -> str` | Returns `lnk_<token>`, 5-min expiry, single-use. Raises `TelegramAlreadyLinked`. |
| `def complete_telegram_link(*, token: str, telegram_id: int, telegram_username: str) -> User` | Enforces one-to-one both directions. Raises `LinkTokenInvalid`, `TelegramAlreadyLinked`. Emits `telegram.linked`. Tx: atomic; unique constraint on `telegram_id` is the backstop. |
| `def start_link_from_bot(*, telegram_id: int, email: str) -> None` | Emails a code; creates account if none. Raises `RateLimited`. |
| `def complete_link_from_bot(*, telegram_id: int, email: str, code: str) -> User` | Verifies code, links, marks verified. Same exceptions/constraints as above. |

Note: `place_order` does **not** require `is_verified` (D6 — no hard block); the checkout view runs `start_otp`/`verify_otp` inline for anonymous users before calling `payments.checkout`, which is where verification happens in practice.

### orders

```python
def place_order(
    *, user: User, plan: Plan, quantity: int, phone: str,
    customer_input: str | None, channel: Literal["web", "bot"],
) -> Order: ...
```
Buy-now (D5): one Order + N OrderItems in `PENDING_PAYMENT`; snapshots price, product, `cost_snapshot=plan.cost_price`; `total_amount = sum(price_snapshot)` server-side; encrypts `customer_input`; generates `tracking_token` + sequential `order_number`; stores phone on user if empty. Raises `ValidationError` (plan unavailable, holiday_stop_new_orders, missing required input, quantity < 1). Emits `order.created` (operator alert on both channels, D17). Tx: one atomic block; no locks needed (inserts only). Not called by the checkout view directly — `payments.checkout` wraps it with `start_card_payment` in one transaction.

Transitions — all follow the convention block above; “Emits” lists the event name whose occurrence id seeds notification `dedupe_key`s:

| Signature | From → To | Notes |
|---|---|---|
| `def mark_order_paid(*, order: Order, payment: Payment) -> None` | PENDING_PAYMENT → QUEUED (all items) | Called **by payments** on confirm. Validates `payment.status == "confirmed"`, `payment.order_id == order.id` (duck-typed — no payments import). Sets `due_at = compute_due_at(payment.matched_at or confirmed_at, product.delivery_hours, …)` per item. Emits nothing (`item.queued` is removed from the registry — it had no consumer; `payment.confirmed` is emitted solely by `payments.confirm_payment`). Idempotent: items already QUEUED are skipped silently (powers idempotent confirm, D2). |
| `def request_input(*, item: OrderItem, message: str, actor: Actor) -> None` | QUEUED → AWAITING_INPUT | Sets `sla_paused_at=now`. Emits `item.awaiting_input`. |
| `def provide_input(*, item: OrderItem, value: str, actor: Actor) -> None` | AWAITING_INPUT → QUEUED | Encrypts into `customer_input`; `due_at += now - sla_paused_at`; clears `sla_paused_at`. Emits `item.input_received` (operator alert). |
| `def deliver(*, item: OrderItem, fields: Sequence[tuple[str, str]], delivery_note: str, expires_at: date \| None, actual_cost: Decimal, actor: Actor) -> None` | QUEUED → DELIVERED | Precondition: `sla_paused_at IS NULL` (a holiday-paused item must be resumed first — delivering while paused would corrupt resume arithmetic). Validates required fields non-empty; writes encrypted `DeliveryField` rows (`is_current=True`); sets `delivered_at`, `actual_cost`; issues the delivery-link token (hashed at rest, 72h, single-use — ADR-0008). Emits `item.delivered` (no credential values; message carries the single-use delivery link + order link, D13-amended). |
| `def request_replacement(*, item: OrderItem, reason: str, actor: Actor) -> None` | DELIVERED → REPLACEMENT_REQUESTED | Warranty gate checked against `product_snapshot` warranty. Emits `item.replacement_requested` (operator alert). |
| `def redeliver(*, item: OrderItem, fields: Sequence[tuple[str, str]], delivery_note: str, expires_at: date \| None, actual_cost: Decimal, actor: Actor) -> None` | REPLACEMENT_REQUESTED → DELIVERED | The A10a redeliver variant: guards on new delivery fields provided; new generation of DeliveryField rows; old rows `is_current=False`, kept (D1/D10). Updates `actual_cost` (adds replacement cost); regenerates the delivery-link token (old link dead). Emits `item.replaced` (customer notice) with a fresh event id → dedupe_key differs → notification legally sends (D17). Repeatable. |
| `def reject_replacement(*, item: OrderItem, note: str, actor: Actor) -> None` | REPLACEMENT_REQUESTED → DELIVERED | The A10b reject-claim variant: operator actor, note **required**, no new DeliveryField generation (current generation stays `is_current=True`). Emits `item.replacement_rejected` (customer notice). |
| `def request_cancellation(*, item: OrderItem, user: User) -> None` | *(not a transition)* | Ownership check; sets `cancellation_requested_at`; emits `item.cancellation_requested` (operator alert + queue badge, D1). Tx: atomic update, no status change. |
| `def cancel(*, item: OrderItem, reason: CancelReason, note: str, actor: Actor) -> None` | {PENDING_PAYMENT, QUEUED, AWAITING_INPUT, REPLACEMENT_REQUESTED} → CANCELLED | `CancelReason = Literal["expired_unpaid","customer_before_payment","customer_after_payment","supply_failure","input_timeout","warranty_refund","operator"]`. Customer as actor only from PENDING_PAYMENT; everything else operator/system. Emits `item.cancelled`. Never touches Refund — called directly only for **unpaid** items; for paid items the panel calls `payments.cancel_with_refund`, which wraps this + Refund-row creation in one transaction; the RR→CAN path also invalidates the item's delivery-link token. |
| `def revive(*, item: OrderItem, payment: Payment, actor: Actor) -> None` | CANCELLED → QUEUED | **Operator-only.** Validates passed payment is `confirmed` and belongs to the order (duck-typed). The revive guard — `cancel_reason` in {expired_unpaid, input_timeout, supply_failure, operator} AND no unexecuted Refund row for the item — is enforced by `payments.revive_order`, its sole caller (no caller-supplied booleans). Recomputes `due_at` from `payment.matched_at`. Emits nothing (`item.queued` removed — no consumer). |
| `def mark_refunded(*, item: OrderItem, refund: Refund) -> None` | CANCELLED → REFUNDED (terminal) | Called **by payments** after transfer execution. Validates `refund.executed_at is not None` and refund targets this item/order — no REFUNDED without an executed Refund row (D2, service-enforced). Emits `item.refunded`. |
| `def pause_all_slas() -> int` / `def resume_all_slas() -> int` | *(not transitions)* | Holiday mechanic (D7): sets/clears `sla_paused_at` on all QUEUED items not already paused; resume shifts each `due_at` by the pause span. Returns count. Emits `sla.holiday_paused` / `sla.holiday_resumed` (notification per affected customer). Tx: **per-item transactions** (no long table lock); one annotation OrderItemEvent per item (actor=system). |
| `def extend_due_at(*, item: OrderItem, new_due_at: datetime, note: str, actor: Actor = "operator") -> None` | *(annotation)* | Legal only in QUEUED; item stays QUEUED. Writes an annotation `OrderItemEvent` (from==to==QUEUED, note). Emits `item.supply_delayed` (customer delay notice C6), dedupe `delay:{item_id}:{new_due_at_iso}:{recipient}:{channel}`. This, `compute_due_at`, and the pause/resume arithmetic are the **only** writers of `due_at`. |

Non-transition services:

```python
def reveal_delivery_fields(*, item: OrderItem, user: User | None, ip: str, via: Literal["panel", "magic_link"] = "panel") -> list[DecryptedField]
```
`via="panel"`: object-level ownership check (owner **or** staff) → `NotOwner`. `via="magic_link"`: the caller must have redeemed a valid token for exactly this item (`user` may be None — the log row carries `via`). Writes one `CredentialAccessLog` row per reveal (operator reveals included, D9/D13); returns decrypted current-generation fields. Tx: log insert atomic; read needs no lock.

```python
def redeem_delivery_link(*, raw_token: str, ip: str) -> OrderItem
```
Hashes the token, looks it up; under the item's `select_for_update` validates unexpired + `used_at IS NULL`, stamps `used_at`, returns the item for the masked view. Invalid/expired/used → uniform `Http404`; per-IP rate-limited (ADR-0008).

```python
def import_legacy_subscription(
    *, email: str, product_name: str, plan_title: str,
    delivered_at: datetime, expires_at: date, price: Decimal,
) -> OrderItem
```
Launch backfill (D12): creates user if needed, Order `channel="legacy"`, one DELIVERED item with `expires_at` so renewal reminders fire. The only legal creation→DELIVERED path (actor=system): writes exactly one `OrderItemEvent(from=NULL, to=DELIVERED, note='backfill')`; creates no payment rows and sends no notifications. Idempotent per (email, product, expires_at).

**orders selectors** (`selectors.py`):

| Selector | Returns |
|---|---|
| `operator_queue(tab: Literal["ready","confirm_payment","awaiting_customer","delivered"])` | Ready tab = QUEUED ordered by `due_at` asc, cancellation-request badge, `<6h`/overdue flags; delivered tab = last 7 days |
| `queue_stats()` | counts for the four stat bar boxes |
| `dashboard_aggregates(period)` | revenue, profit (`price_snapshot − actual_cost`), top sellers, avg delivery time & SLA-breach rate from `OrderItemEvent`, items near due |
| `items_expiring(on: date)` | DELIVERED items with `expires_at` = target day (renewal scan, 7d/0d) |
| `overdue_items()` | QUEUED past `due_at`, not paused |
| `customer_orders(user)` / `order_for_tracking(token)` | customer list; tracking page gets status timeline + product name ONLY, uniform not-found (D12). Order display status for n>1 derives from items by stage precedence — PENDING_PAYMENT < QUEUED < AWAITING_INPUT < REPLACEMENT_REQUESTED < DELIVERED, terminal label only when all items are terminal (ADR-0011) |
| `awaiting_input_stale(older_than: timedelta)` | AWAITING_INPUT candidates for reminder/timeout surfacing |

### payments

| Signature | Behavior |
|---|---|
| `def checkout(*, user: User, plan: Plan, quantity: int, customer_input: str \| None, phone: str) -> tuple[Order, Payment]` | The checkout orchestrator — the checkout view calls **only this**. Calls `orders.place_order` + `start_card_payment` in ONE transaction. |
| `def revive_order(*, payment: Payment) -> Order` | Revive orchestrator (one panel screen, one service): manual match/confirm of the payment + per-item `orders.revive` in one transaction. Enforces the revive guard itself: an item is revivable only if `cancel_reason` in {expired_unpaid, input_timeout, supply_failure, operator} AND no unexecuted Refund row exists for it. |
| `def cancel_with_refund(*, item: OrderItem, reason: CancelReason, refund_destination: str, amount: Decimal, note: str, actor: Actor) -> Refund` | Wraps `orders.cancel(...)` + Refund-row creation **atomically** — the ONE service the panel calls to cancel a paid item (one-service-per-view). Unpaid items go straight to `orders.cancel`. |
| `def start_card_payment(*, order: Order) -> Payment` | Allocates `unique_amount = total + random small delta`; retry-on-`IntegrityError` against the partial unique index; service-level check that the amount wasn’t used in the last 72h (index is the backstop, D11). One active payment per order (partial unique on order_id) — returns the existing pending payment idempotently if one exists. Snapshots the active destination card (number + holder) onto the Payment — the instructions page renders from the snapshot, immune to a mid-window `active_card` flip. Sets `expires_at = now + SiteSetting.payment_window_minutes`, `idempotency_key`. No events. Tx: short atomic insert with bounded retries. |
| `def submit_receipt(*, payment: Payment, receipt_image: UploadedFile \| None, card_last4: str \| None) -> Payment` | pending → submitted; validates file type/size; stores outside public media root, randomized name (D14); sets `submitted_at`. Emits `payment.submitted` (operator alert, both channels). Tx: select_for_update(payment). |
| `def confirm_payment(*, payment: Payment, paid_amount: Decimal, bank_ref: str, operator: User) -> Payment` | {pending, submitted} → confirmed. **Idempotent**: already-confirmed returns unchanged, no duplicate events (D2). Records `paid_amount` (may differ — small shortfall accepted, delta visible), `bank_ref`, `matched_by`, `matched_at`. Then calls `orders.services.mark_order_paid(order=…, payment=…)`. Emits `payment.confirmed` (customer notice) — this service is the event's **only** emitter. Tx: one atomic block covering payment + item transitions. |
| `def reject_payment(*, payment: Payment, note: str, operator: User) -> Payment` | {pending, submitted} → rejected. Cascades: all of the order's PENDING_PAYMENT items → CANCELLED (`reason="operator"`, note carries the rejection) in the same transaction — same shape as the expiry cascade. Emits `payment.rejected` (customer notice). |
| `def expire_payment(*, payment: Payment) -> Payment` | pending → expired (system/beat) — **pending only**; a submitted payment never auto-expires, it waits for the operator's confirm/reject. Cancels the order's PENDING_PAYMENT items via `orders.cancel(reason="expired_unpaid", actor="system")`. Emits `payment.expired` (so the "expired" message only ever reaches silent non-payers who never claimed payment). State-guarded → sweep is idempotent. |
| `def manual_match(*, payment: Payment, paid_amount: Decimal, bank_ref: str, operator: User) -> Payment` | expired → confirmed (late transfer arrived / near-miss amount, D2/D11). Records paid_amount + match fields, then `orders.mark_order_paid` — items are CANCELLED by then; `mark_order_paid` transitions only PENDING_PAYMENT items and leaves CANCELLED ones for revive. The panel calls `revive_order`, which chains this + per-item `orders.revive` in one transaction. Idempotent like confirm. |
| `def record_refund(*, payment: Payment, order_item: OrderItem \| None, amount: Decimal, destination: str, note: str, created_by: User) -> Refund` | Creates the Refund row (nullable item FK for partials, D2). Validates amount ≤ paid_amount − prior refunds. No events; no status change. |
| `def mark_refund_executed(*, refund: Refund, bank_ref: str, operator: User) -> Refund` | Sets `executed_at`, `bank_ref`; then calls `orders.mark_refunded(item=…, refund=…)` for the linked item (order-level refunds: caller loops items). Emits nothing itself — the item transition emits `item.refunded`. Tx: atomic across refund + item. Idempotent on executed_at set. |
| `def record_unmatched_transfer(*, amount: Decimal, bank_ref: str, received_at: datetime, note: str, operator: User) -> UnmatchedTransfer` | Ledger insert (D9). |
| `def resolve_unmatched_transfer(*, transfer: UnmatchedTransfer, resolution: Literal["matched","refunded","kept"], payment: Payment \| None, operator: User) -> UnmatchedTransfer` | Links to a payment when matched. |

**payments selectors**: `payments_awaiting_confirmation()` (confirm-payment tab, sorted by submitted_at), `reconciliation_day_list(date)` (all payments + refunds + unmatched transfers touching that day, with expected/actual deltas and each payment's snapshotted destination card — daily reconciliation), `refunds_pending_execution()`.

Rial appears only in the card-to-card instruction template context (`amount_rial = amount_toman * 10`, one tested helper in payments) and the future gateway adapter (D3).

**PaymentProvider deferral (ADR-worthy, deliberate):** phase 1 ships card-to-card as plain `payments.services` functions — no adapter interface. The interface is extracted in phase 1.5 when Zibal, a second real implementation, defines its shape; `Payment.method`/`gateway_name` already keep the door open.

### notifications

```python
def notify(
    *, event_type: str, dedupe_key: str, user: User | None,   # None = operator recipient (addresses from settings)
    order: Order | None = None, order_item: OrderItem | None = None,
    context: dict[str, Any] | None = None,
    channels: Sequence[Channel] | None = None,   # default: telegram if linked + email
) -> list[Notification]
```
Creates one outbox row per channel, `status=pending`. `dedupe_key` rule: `"{occurrence}:{recipient}:{channel}"` where recipient is the user id or the literal `op` — e.g. `f"item.delivered:{event_id}:{user_id}:telegram"`, `"order:1041:created:op:telegram"` (D17) — `IntegrityError` on it is swallowed (duplicate handler fire). Kicks `send_outbox` task on commit. Content-free for delivery events (D13). Channels are exactly `email` and `telegram`: IN_APP cut for phase 1 — the customer order pages are the in-app surface; the channel enum extends cleanly if a bell/inbox is ever wanted.

Channel senders — the only code that talks to the outside; both mocked in tests:

```python
def send_telegram(chat_id: int, text: str) -> None      # raises TelegramSendError
def send_email(to: str, subject: str, body: str) -> None  # raises EmailSendError
```

Event → notification wiring (registered in `ready()`): `order.created`/`payment.submitted` → operator on **both** Telegram and email (D17) · `payment.confirmed`, `item.awaiting_input`, `item.delivered`, `item.replaced`, `item.replacement_rejected`, `item.cancelled`, `item.refunded`, `item.supply_delayed` (C6), `sla.holiday_paused`/`sla.holiday_resumed`, renewal 7d/0d → customer · `item.replacement_requested`, `item.cancellation_requested`, `item.input_received` → operator.

Webhook: `telegram_webhook(request)` view at a secret path, verifies `X-Telegram-Bot-Api-Secret-Token`, handles `/start lnk_<token>` → `accounts.complete_telegram_link`, and the link-from-bot email/code exchange → `accounts.start_link_from_bot` / `complete_link_from_bot` (D4: in-process service calls, no DRF).

---

## 3. Celery Tasks & Beat Schedule

All tasks are state-guarded and safe to double-run (beat + manual). `acks_late=True`, bound, with `max_retries` where they touch the network.

| Task | Cadence | What it does | Idempotency |
|---|---|---|---|
| `payments.tasks.expire_stale_payments` | every 5 min | `expire_payment` for **pending-only** payments past `expires_at` (submitted never auto-expires — it waits for operator confirm/reject); cascades item cancel (`expired_unpaid`) | status-guarded transition; re-run is a no-op |
| `notifications.tasks.send_outbox` | every 1 min **and** kicked on-commit by `notify()` | picks `status=pending, next_attempt_at<=now` with `select_for_update(skip_locked=True)`, sends, marks sent/failed. Backoff per the notification contract's fixed ladder: attempt immediately, then 1m, 5m, 15m, 1h, 6h (6 attempts total), then `status=failed`; non-retryable classes fast-fail to `failed` (Telegram 403/400 — bot blocked / invalid chat — and hard email bounces) | at-least-once send; row status + dedupe_key prevent duplicates at enqueue; per-row lock prevents concurrent double-send |
| `orders.tasks.renewal_reminders` | daily 09:00 | `items_expiring(today+7)` and `items_expiring(today)` → `notify` with direct repurchase link (channel="legacy" rows included) | dedupe_key `renew7:{item_id}:{expires_at_iso}:{recipient}:{channel}` / `renew0:…` — keys include `expires_at`, so a later extension of `expires_at` legitimately re-arms the reminder |
| `orders.tasks.awaiting_input_followup` | daily 10:00 | the input-timeout ladder (ADR-0009): 48h re-reminder → 7d operator queue flag → 14d automated final warning naming the deadline (`item.input_final_warning`) → **21d system auto-cancel** via `payments.cancel_with_refund(reason="input_timeout")` with destination blank (collected from the customer before execution). Anchor = the AI-entry OrderItemEvent; holiday mode does not pause the ladder | `remind:{item}:48h:…` / `remind:{item}:14d:…`; the cancel is state-guarded |
| `orders.tasks.overdue_alert` | hourly, during support hours only | ONE operator digest per hour ("N سفارش گذشته از مهلت") for `overdue_items()` (D20 alarm) — no per-item alerts | dedupe_key `overdue:{date_iso}T{hour}:op:{channel}` |
| `payments.tasks.purge_receipts` | daily 03:00 | deletes receipt images **90 days** after payment confirmation (owner-accepted retention), logs count | delete is naturally idempotent |
| `orders.tasks.purge_expired_credentials` | daily 03:10 | hard-deletes DeliveryField rows (all generations) and encrypted `customer_input` at `expires_at + warranty window + 30 days` (owner-accepted); writes an annotation OrderItemEvent noting the purge | date-guarded; re-run is a no-op |
| `orders.tasks.purge_old_access_logs` | monthly | deletes CredentialAccessLog rows older than 1 year (owner-accepted); OrderItemEvent is NOT purged — it is the order audit trail | date-bounded delete |
| `core.tasks.heartbeat` | every 5 min (beat) | GET to healthchecks.io-class URL — dead beat/worker alerts from **outside** the app (D17) | trivially |

Outbound `sendMessage` calls for the bot are just `send_outbox` doing its job — no separate bot task.

---

## 4. Thin Views, Concretely

A view (HTML or HTMX partial) may do exactly five things:

1. **Auth/permission**: `login_required` / staff check; object fetch **via a selector** that already scopes ownership (`customer_orders(user)` — never `Order.objects.get(pk)`).
2. **Parse + validate input** with a Django `Form` (format-level validation only: required, max_length, file type; *business* validation lives in the service and its `ValidationError` is re-rendered on the form).
3. **Call at most one service function** (GET pages call selectors only).
4. **Render** a template / return an HTMX fragment / redirect, with `messages` for feedback.
5. Set response headers the page needs (`Cache-Control: private, no-store` on panel/tracking/delivery-link (`/d/<token>/`)/HTMX-per-user endpoints, D19 — via middleware/decorator in core; the delivery-link page also sends `Referrer-Policy: no-referrer`, ADR-0008).

A view may **not**: assign model fields, open transactions, branch on business state (beyond choosing a template), call two services, enqueue tasks, or send anything. Templates: presentation logic only — status→color/text mapping via a template tag fed from one dict in orders, Jalali/digit formatting via core template tags. The delivery page POST is the canonical example: one `DeliveryForm`, one call to `orders.services.deliver(...)`, redirect to queue.

---

## 5. Testing Seams

**Mocked (the only things mocked):**

| Seam | How |
|---|---|
| Telegram transport | `notifications.senders.send_telegram` — single function, monkeypatched/`unittest.mock`; webhook tests post signed fake updates |
| Email transport | Django `locmem` backend; assert on `mail.outbox` |
| Clock | `time_machine`/`freezegun` for `compute_due_at`, SLA pause math, payment expiry, 72h amount reuse, renewal scans |
| Heartbeat HTTP | mock `requests` in `core.tasks.heartbeat` |
| Fernet key | fixed test key in test settings; one test decrypts a stored `DeliveryField` and round-trips key rotation via MultiFernet |
| Celery | `task_always_eager` off by default — services are called directly; task tests call task functions synchronously |

Everything else (Postgres constraints, unique-amount IntegrityError retry, state machine, ownership) runs against the real test DB — the partial unique indexes **are** the test subject, so no sqlite.

**Factories** (`factory_boy`): `UserFactory` (traits: `verified`, `telegram_linked`, `staff`) · `CategoryFactory` · `ProductFactory` (trait: `with_template`) · `ProductSpecFactory` · `PlanFactory` (traits: `requires_input`, `unavailable`) · `OrderFactory` · `OrderItemFactory` (state traits for all 7 statuses, `paused`, `cancellation_requested`, `legacy`) · `DeliveryFieldFactory` (trait: `superseded`) · `PaymentFactory` (state traits, `with_receipt`) · `RefundFactory` (trait: `executed`) · `UnmatchedTransferFactory` · `NotificationFactory` · `SiteSettingFactory` · `PageFactory`.

Must-cover suites (per working agreement §11): full transition matrix (legal + every illegal pair raises, DELIVERED→CANCELLED forbidden, both REPLACEMENT_REQUESTED→DELIVERED variants A10a/A10b), `compute_due_at` boundary table, `extend_due_at`, confirm/manual-match idempotency, unique-amount collision + 72h reuse, ownership on order pages + reveal, encryption round-trip, Sentry scrubber leak test (D20), end-to-end place→pay→confirm→deliver→notify flow.

---

## Concerns

1. **Revive vs. pending Refund (D1×D2):** an item can be CANCELLED with a Refund recorded but not yet executed, and revive (CANCELLED→QUEUED) is legal. Reviving while a refund is in flight double-pays the obligation. **Resolved:** the guard (eligible `cancel_reason` + no unexecuted Refund) lives in `payments.revive_order`, the sole caller of `orders.revive` — no caller-supplied booleans.
2. **`due_at` base after revive/manual match (D7×D2):** computing from the original `payment_confirmed_at` on a revived order yields an already-overdue item. Specified `matched_at` as the base for manual-match confirmations; needs a nod.
3. **`manual_match` on an expired payment confirms the payment but items are already CANCELLED** (expiry cascade). `mark_order_paid` is specified to skip non-PENDING_PAYMENT items, so revive stays an explicit per-item operator action — matches D1's "operator-only revive". **Resolved:** `payments.revive_order` chains manual match/confirm + per-item revive in one transaction, and the panel calls that one service.
4. **Brief's `unique_together(order_item, event_type, channel)` dedup is overridden by D17's dedupe_key** — followed D17; noting only because the brief text survives in `docs/brief.md`.
