# PremShop — Module Boundaries & Service Layer Contract (Phase 1)

## 1. App Map

Dependency direction is a strict order. An app may import only from apps **above** it in this list (plus stdlib/Django/libs). Panel sits at the top and imports everyone; nothing imports panel. `cms` and `catalog` are domain-leaf apps.

```
core  →  accounts, catalog, cms  →  cart, orders  →  payments  →  notifications  →  panel
```

`cart` and `orders` are **siblings, not a chain**: neither imports the other. That is what keeps `orders` ignorant of how a cart is stored — the rule that lets `place_order` take a plain `Sequence[OrderLine]` — and `payments.checkout` is the single place the two meet (ADR-0018).

| App | Owns (models) | Owns (logic) | May import | Never |
|---|---|---|---|---|
| **core** | `SiteSetting` (singleton: holiday_stop_new_orders, holiday_message, holiday_pause_sla, support_start, support_end, off_weekdays, support_hours_display, **gateway_timeout_minutes=15** — the age at which an `initiated` payment becomes eligible for the inquiry sweep, i.e. the lost-callback net; **unpaid_order_ttl_hours=24** — how long an order may sit in PENDING_PAYMENT with no verified payment before the sweep cancels it. Two clocks, two names, never one field: 24h is deliberate, because the failed-payment message promises the customer their order is still there and hands them a retry link) | `EncryptedTextField` (Fernet/MultiFernet, ~30 lines), money/digit/Jalali format helpers, Persian search normalization (yeh/kaf/half-space/digit folding), `compute_due_at` pure function, `events.emit`/`events.register`, heartbeat task | nothing domain-side | importing any other app |
| **accounts** | `User` (email USERNAME_FIELD, is_verified, phone (CharField, blank=''), telegram_id/username/linked_at), OTP + telegram-link token state | auth services: OTP login, inline checkout OTP, telegram linking both directions | core | orders, payments |
| **catalog** | `Category` (flat, no parent), `Product` (incl. delivery_template, delivery_hours, region, warranty, search_text column — deliberately unindexed: icontains cannot use btree, seq scan is fine at this catalog size, pg_trgm is the named upgrade path; status third value is "unavailable"), `ProductSpec`, `Plan` (cost_price, sale_price, promo_price, promo_starts_at, promo_ends_at, requires_customer_input, duration_days, supplier_url — per-plan upstream listing, owner ruling) | `effective_price` — the single source of the pricing rule; selectors only (search, active listings); admin with inlines (promotional pricing is set per plan, no code required); normalization on save | core | any domain app |
| **cms** | `Page`, `FAQ` | admin-edited content, public views | core | any domain app |
| **cart** | `Cart`, `CartItem` (ADR-0018) | the **persistent cart** — resolution by user or session key, add/set/remove, the guest-to-account merge on login, the `cart_summary` selector, the stale-guest-cart purge | core, accounts, catalog | **orders**, payments, notifications, panel. Imported by payments (which resolves a cart into order lines at checkout) and panel |
| **orders** | `Order` (channel web\|bot\|legacy, tracking_token, order_number, subtotal, discount_code FK PROTECT, discount_amount, total_amount), `OrderItem` (7 statuses, price_snapshot, cost_snapshot, actual_cost, paid_at, sla_paused_at, cancel_reason, cancellation_requested_at, encrypted customer_input, due_at, expires_at), `DiscountCode` (ADR-0020 — lives here because `Order` FKs it PROTECT), `DiscountRedemption`, `DeliveryField` (encrypted value, is_current), `OrderItemEvent`, `CredentialAccessLog` | the state machine, place_order, all transitions, delivery, SLA pause/resume, reveal logging, delivery-link issue/redeem + the no-login masked view (ADR-0008), legacy backfill, discount validation/consumption/redemption records, customer order pages + public tracking page | core, accounts, catalog | cart, payments, notifications, panel. Where a transition is payment-guarded (revive, refunded), the **caller passes the Payment/Refund object in**; orders validates its attributes, never imports payments |
| **payments** | `Payment` (order, method enum `gateway\|manual`, status, amount, gateway_name, authority (UNIQUE, nullable), ref_id, idempotency_key (UNIQUE — the key sent to the **gateway** on the initiate call, so a retried initiate cannot create two payment requests upstream; it plays no part in confirmation), failure_reason, note, matched_by, initiated_at, verified_at, failed_at, created_at), `Refund` (+ gateway_refund_ref alongside bank_ref) | payment machine (created→initiated→verified\|failed\|abandoned, plus created→verified for the manual fallback — ADR-0019), `confirm_payment` (the shared confirmation helper: a `select_for_update` row lock plus a re-read of the status under that lock, returning the payment unchanged when it is already `verified` — that is the idempotency guarantee, never an unlocked "if not verified" check), the gateway client (start/verify/inquire/refund), the stale-payment inquiry sweep, checkout/revive/cancel-with-refund orchestrators, refund ledger. Plain service functions — no PaymentProvider interface (ADR-0013: one implementation, no interface). Rial conversion lives ONLY here | core, accounts, catalog, cart (resolves a cart into order lines at checkout), orders (calls orders services on verify/refund-executed) | notifications, panel |
| **notifications** | `Notification` outbox (status, attempts, next_attempt_at, unique dedupe_key, nullable order FK, nullable order_item FK, channel, event_type, payload) | `notify()`, email + telegram channel senders, telegram webhook view (secret path + `X-Telegram-Bot-Api-Secret-Token`), outbox worker, event handlers registered against `core.events` at AppConfig.ready() | core, accounts, orders, payments (selectors, to render message context) | panel |
| **panel** | **no models** | operator queue, delivery page, dashboard, the "record payment received outside the gateway" action (the operator-only manual fallback, ADR-0019), refund forms, discount-code admin, reconciliation view, holiday switch — all staff-only views calling other apps' services/selectors | everyone's services + selectors | defining business logic; touching models directly for writes |

Responsibility placement from the brief: SLA math → core (pure) + orders (applies it) · gateway verify + inquiry sweep → payments · the persistent cart → cart · discount validation → orders · promotional pricing (`effective_price`) → catalog, and every price shown or charged goes through it · masked reveal + access log → orders · outbox/retry → notifications · operator daily loop → panel · Enamad pages → cms · search → core (normalize) + catalog (selector) · Jalali/digits → core template tags · reviews → phase-2 app, absent from skeleton · **no DRF anywhere** (D4): the bot is one webhook view in notifications calling services in-process.

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
| `def complete_telegram_link(*, token: str, telegram_id: int, telegram_username: str) -> User` | Enforces one-to-one both directions. Raises `LinkTokenInvalid`, `TelegramAlreadyLinked`. Emits nothing — the bot replies in line, so `telegram.linked` had no consumer and is gone. Tx: atomic; unique constraint on `telegram_id` is the backstop. |
| `def start_link_from_bot(*, telegram_id: int, email: str) -> None` | Emails a code; creates account if none. Raises `RateLimited`. |
| `def complete_link_from_bot(*, telegram_id: int, email: str, code: str) -> User` | Verifies code, links, marks verified. Same exceptions/constraints as above. |

Note: `place_order` does **not** require `is_verified` (D6 — no hard block); the checkout view runs `start_otp`/`verify_otp` inline for anonymous users before calling `payments.checkout`, which is where verification happens in practice. Catalog, product pages, search **and the cart** are fully anonymous — identity is collected inside checkout by this inline OTP, which doubles as signup and email verification; profile details come later from the dashboard. No sign-in wall in front of the shop (ADR-0018). The guest cart survives the OTP sign-in because `cart.services.merge_carts` runs on the login signal — see the cart app below.

### catalog

```python
def effective_price(plan: Plan, at: datetime | None = None) -> Decimal: ...
```
Returns `plan.promo_price` when it is set and `at` (default `now()`) falls inside `[promo_starts_at, promo_ends_at]` — an absent bound means open-ended — otherwise `plan.sale_price`. **This function is the only place the promotional-pricing rule lives**, and every price shown or charged goes through it: catalog cards, product pages, cart lines, the checkout summary, and `OrderItem.price_snapshot` at order time. Pure apart from reading the plan row; no DB writes, no exceptions. Its boundary table (before/inside/after the window, each bound null, promo unset) is a named test.

The DB backs the rule up: `promo_price` is null or `> 0 AND < sale_price`, and `promo_starts_at` is null or earlier than `promo_ends_at`. Where a promotion is active the template renders the original price struck through beside the promotional one (the design language defines that component).

### cart

Its own app (ADR-0018), sibling to `orders`: it imports `core`, `accounts` and `catalog`, and **never `orders`** — which is the other half of why `place_order` takes a plain `Sequence[OrderLine]`. A cart is resolved by **user when signed in, `session_key` otherwise** — so a guest's cart survives closing the browser (`SESSION_EXPIRE_AT_BROWSER_CLOSE=False`, `SESSION_COOKIE_AGE=30 days`) and a customer's cart follows them across devices. `CartItem` stores **only** `plan` and `quantity` (1–10, DB CHECK) — never prices, never names; every amount is recomputed from the database on every render. These helpers take `request` because resolution needs both the user and the session key; they touch nothing else on it, and no other service in the codebase accepts a request.

| Signature | Behavior |
|---|---|
| `def get_cart(request, *, create: bool = False) -> Cart \| None` | Resolves the cart for this visitor: by `user` when authenticated, else by `session_key` (forcing session creation only when `create=True`). Returns `None` when there is nothing yet and `create=False` — a browsing visitor writes no rows. |
| `def add_to_cart(request, *, plan_id: int, quantity: int = 1) -> CartItem` | Creates the cart if needed; adds or increments the line, clamped to the per-line maximum. Unique `(cart, plan)` makes a double-submit an update, not a second line. **A plan with `requires_customer_input` is capped at quantity 1**: a second unit is refused with a clear Persian message, because each `OrderItem` is a separate credential with its own lifecycle and three of them would need three separate inputs — a form nobody asked for. Raises `ValidationError`. |
| `def set_quantity(request, *, plan_id: int, quantity: int) -> None` | Sets absolute quantity, clamped; `0` deletes the line. Same quantity-1 rule for input-requiring plans, same refusal. |
| `def remove_from_cart(request, *, plan_id: int) -> None` / `def clear_cart(cart: Cart) -> None` | Removal; `clear_cart` runs inside the order-creation transaction. |
| `def cart_count(request) -> int` | Total units — the header badge; one aggregate query. |
| `def merge_carts(*, user: User, session_key: str) -> Cart` | **Runs on the `user_logged_in` signal.** If the account has no cart, the guest cart is **claimed** (set `user`, clear `session_key`) — which preserves each line's `added_at`. Otherwise quantities are **summed per plan** into the account's cart and clamped to the per-line maximum (and to 1 for input-requiring plans), and the guest cart is deleted. Idempotent, atomic, and a no-op when there is no guest cart. This is the part customers notice, so it has its own test. |

```python
def cart_summary(
    cart: Cart | None, *,
    discount_amount: Decimal = Decimal(0), discount_error: str | None = None,
) -> CartSummary: ...
```
The one selector the cart and checkout pages render from: re-reads every plan from the DB, recomputes each `unit_price` through `catalog.effective_price`, and returns per-line `plan`, `quantity`, `unit_price`, `original_price` (set only while a promotion is active, for the struck-through display), `line_total`, plus `unavailable` flags for plans that went inactive or whose product is unavailable. Returns `subtotal`, `discount_amount`, `total` as distinct numbers so the summary renders as three ledger lines (ADR-0020). The discount itself is **computed by `orders.validate_discount_code(lock=False)`** — read-only, selector-grade — and handed in by the caller (`payments.checkout`, or the cart page's view for a live preview); a code that fails validation arrives as `discount_error` and renders as a rejection on the summary, never as an exception. Cart does not call the validator itself, because cart does not import orders.

Because quantity is capped at 1 for input-requiring plans, `customer_inputs` is a mapping of **plan id → one string** — the shape the checkout form and `payments.checkout` already assume. *Flip condition:* if a customer genuinely needs several units of an input-requiring plan, the answer is **per-item inputs on the checkout form**, never silently reusing one value across units.

### orders

```python
@dataclass(frozen=True)
class OrderLine:
    plan: Plan
    quantity: int
    customer_input: str | None = None

def place_order(
    *, user: User, lines: Sequence[OrderLine], phone: str,
    discount_code: str | None, channel: Literal["web", "bot"],
) -> Order: ...
```
Takes **resolved lines, never a session and never a cart object** — `orders` does not know how a cart is stored; `payments.checkout` resolves the cart into `OrderLine`s first. Writes one Order + one OrderItem per unit in `PENDING_PAYMENT`; snapshots `price_snapshot = catalog.effective_price(plan)` and `cost_snapshot = plan.cost_price`, both **read fresh from the DB** — nothing priced by the browser is trusted; `subtotal = Σ price_snapshot`; applies `discount_code` via `validate_discount_code(lock=True)`, consumes it (`used_count` incremented under `select_for_update` on the `DiscountCode` row, inside this transaction — a single-use code cannot be spent twice concurrently, ADR-0020) and writes the `DiscountRedemption` row; `discount_amount` clamped so `total_amount = subtotal − discount_amount` is never negative; encrypts each line's `customer_input`; generates `tracking_token` + sequential `order_number`; stores phone on user if empty. Invariant asserted here and tested: `subtotal = Σ price_snapshot`, `total_amount = subtotal − discount_amount`, `total_amount >= 0`. Raises `ValidationError` (plan unavailable, holiday_stop_new_orders, missing required input, **quantity > 1 on a plan with `requires_customer_input`** — one input, one item, re-validated here after the cart already refused it, quantity < 1, no lines) and the discount exceptions below. **Emits nothing** — there is no `order.created` event: the operator alert fires on `payment.verified`, so an unpaid order never pages anyone (ADR-0019). Tx: one atomic block; the only lock is on the discount row. Not called by the checkout view directly — `payments.checkout` wraps it.

```python
def validate_discount_code(
    *, code: str, user: User, lines: Sequence[OrderLine], lock: bool = False,
) -> tuple[DiscountCode, Decimal]: ...
```
Normalises the typed code to uppercase before lookup (stored uppercase, `^[A-Z0-9]{4,8}$`); checks `is_active`, `valid_from`/`valid_until` window, `min_order_amount` (compared against the order **subtotal**, before any discount), `max_uses` vs `used_count`, and `per_user_limit` against this user's `DiscountRedemption` rows. Returns the code and the **server-computed** discount amount. The computation, stated once and tested once:

- `eligible_subtotal` = the sum of the line totals **in scope** — every line when `scope="all"`, only lines whose product is in `code.products` when `scope="selected"`. A scoped code discounts the eligible subtotal, never the order subtotal.
- `percent`: `round(eligible_subtotal × value / 100)` to whole toman, `ROUND_HALF_UP`.
- `fixed`: `min(value, eligible_subtotal)`.
- The result is clamped so `total_amount` can never fall below zero.

Every input is read from the database — line totals come from `catalog.effective_price`, so a code applies **on top of** promotional pricing: it discounts what the customer would actually pay. (Excluding promo items from codes is a named future option, not a flag; it flips only if a code is ever seen to sell a promo item below cost.) `lock=True` takes `select_for_update` on the row; used only from inside `place_order`. Raises `DiscountInvalid`, `DiscountExpired`, `DiscountExhausted`, `DiscountMinimumNotMet` (all `ValidationError` subclasses, each with its own Persian message). Read-only when `lock=False`, so the cart page can preview a code without consuming it.

`DiscountRedemption` (one row per order, `order` UNIQUE) is written by `place_order` inside the same transaction. It exists because `per_user_limit` cannot be enforced without it, and it doubles as the audit trail of what each code actually cost.

Transitions — all follow the convention block above; “Emits” lists the event name whose occurrence id seeds notification `dedupe_key`s:

| Signature | From → To | Notes |
|---|---|---|
| `def mark_order_paid(*, order: Order, payment: Payment) -> None` | PENDING_PAYMENT → QUEUED (all items) | Called **by payments**, from inside `confirm_payment`. Validates `payment.status == "verified"`, `payment.order_id == order.id` (duck-typed — no payments import). Sets `paid_at = payment.verified_at` and `due_at = compute_due_at(payment.verified_at, product.delivery_hours, …)` per item. Emits nothing (`item.queued` is removed from the registry — it had no consumer; `payment.verified` is emitted solely by `payments.confirm_payment`). Idempotent: items already QUEUED are skipped silently — this is what makes a replayed callback harmless (ADR-0019). |
| `def request_input(*, item: OrderItem, message: str, actor: Actor) -> None` | QUEUED → AWAITING_INPUT | Sets `sla_paused_at=now`. Emits `item.awaiting_input`. |
| `def provide_input(*, item: OrderItem, value: str, actor: Actor) -> None` | AWAITING_INPUT → QUEUED | Encrypts into `customer_input`; `due_at += now - sla_paused_at`; clears `sla_paused_at`. Emits `item.input_received` (operator alert). |
| `def deliver(*, item: OrderItem, fields: Sequence[tuple[str, str]], delivery_note: str, expires_at: date \| None, actual_cost: Decimal, actor: Actor) -> None` | QUEUED → DELIVERED | Precondition: `sla_paused_at IS NULL` (a holiday-paused item must be resumed first — delivering while paused would corrupt resume arithmetic). Validates required fields non-empty; writes encrypted `DeliveryField` rows (`is_current=True`); sets `delivered_at`, `actual_cost`; issues the delivery-link token (hashed at rest, 72h, single-use — ADR-0008). Emits `item.delivered` (no credential values; message carries the single-use delivery link + order link, D13-amended). |
| `def request_replacement(*, item: OrderItem, reason: str, actor: Actor) -> None` | DELIVERED → REPLACEMENT_REQUESTED | Warranty gate checked against `product_snapshot` warranty. Emits `item.replacement_requested` (operator alert). |
| `def redeliver(*, item: OrderItem, fields: Sequence[tuple[str, str]], delivery_note: str, expires_at: date \| None, actual_cost: Decimal, actor: Actor) -> None` | REPLACEMENT_REQUESTED → DELIVERED | The A10a redeliver variant: guards on new delivery fields provided; new generation of DeliveryField rows; old rows `is_current=False`, kept (D1/D10). Updates `actual_cost` (adds replacement cost); regenerates the delivery-link token (old link dead). Emits `item.replaced` (customer notice) with a fresh event id → dedupe_key differs → notification legally sends (D17). Repeatable. |
| `def reject_replacement(*, item: OrderItem, note: str, actor: Actor) -> None` | REPLACEMENT_REQUESTED → DELIVERED | The A10b reject-claim variant: operator actor, note **required**, no new DeliveryField generation (current generation stays `is_current=True`). Emits `item.replacement_rejected` (customer notice). |
| `def request_cancellation(*, item: OrderItem, user: User) -> None` | *(not a transition)* | Ownership check; sets `cancellation_requested_at`; emits `item.cancellation_requested` (operator alert + queue badge, D1). Tx: atomic update, no status change. |
| `def cancel(*, item: OrderItem, reason: CancelReason, note: str, actor: Actor) -> None` | {PENDING_PAYMENT, QUEUED, AWAITING_INPUT, REPLACEMENT_REQUESTED} → CANCELLED | `CancelReason = Literal["expired_unpaid","customer_before_payment","customer_after_payment","supply_failure","input_timeout","warranty_refund","operator"]`. Customer as actor only from PENDING_PAYMENT; everything else operator/system. Emits `item.cancelled`. Never touches Refund — called directly only for **unpaid** items; for paid items the panel calls `payments.cancel_with_refund`, which wraps this + Refund-row creation in one transaction; the RR→CAN path also invalidates the item's delivery-link token. |
| `def revive(*, item: OrderItem, payment: Payment, actor: Actor) -> None` | CANCELLED → QUEUED | **Operator-only.** Validates passed payment is `verified` and belongs to the order (duck-typed). The revive guard — `cancel_reason` in {input_timeout, supply_failure, operator} AND no unexecuted Refund row for the item — is enforced by `payments.revive_order`, its sole caller (no caller-supplied booleans). Revive-from-expired is gone with card-to-card (ADR-0019): a payment that was never verified has nothing to revive. Recomputes `due_at` from **now** (a payment verified weeks ago would make the revived item instantly overdue). Emits nothing (`item.queued` removed — no consumer). |
| `def mark_refunded(*, item: OrderItem, refund: Refund) -> None` | CANCELLED → REFUNDED (terminal) | Called **by payments** after transfer execution. Validates `refund.executed_at is not None` and refund targets this item/order — no REFUNDED without an executed Refund row (ADR-0003 and the state machine's A12, service-enforced). Emits `item.refunded`. |
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
| `operator_queue(tab: Literal["ready","unpaid","awaiting_customer","delivered"])` | Ready tab = QUEUED ordered by `due_at` asc, cancellation-request badge, `<6h`/overdue flags; unpaid tab = orders still in PENDING_PAYMENT, where the operator can run the manual-payment action (the manual fallback, ADR-0019); delivered tab = last 7 days |
| `queue_stats()` | counts for the four stat bar boxes |
| `dashboard_aggregates(period)` | revenue, profit (`price_snapshot − actual_cost`), top sellers, avg delivery time & SLA-breach rate from `OrderItemEvent`, items near due |
| `items_expiring(on: date)` | DELIVERED items with `expires_at` = target day (renewal scan, 7d/0d) |
| `overdue_items()` | QUEUED past `due_at`, not paused |
| `customer_orders(user)` / `order_for_tracking(token)` | customer list; tracking page gets status timeline + product name ONLY, uniform not-found (D12). Order display status for n>1 derives from items by stage precedence — PENDING_PAYMENT < QUEUED < AWAITING_INPUT < REPLACEMENT_REQUESTED < DELIVERED, terminal label only when all items are terminal — multi-plan carts make this the normal case, not the exception (ADR-0018) |
| `awaiting_input_stale(older_than: timedelta)` | AWAITING_INPUT candidates for reminder/timeout surfacing |

### payments

| Signature | Behavior |
|---|---|
| `def checkout(*, user: User, request, phone: str, customer_inputs: dict[int, str], discount_code: str \| None) -> tuple[Order, str]` | The checkout orchestrator — the checkout view calls **only this**, and gets back the order plus the **gateway redirect URL**. The checkout page has already shown the summary (subtotal, discount, total) computed server-side by `cart_summary` *before* submission; submitting is what creates the order and starts the payment. `payments` is the one place `cart` and `orders` meet — it imports both, and neither imports the other. Step 1, in ONE transaction: resolves the visitor's `Cart` (via `cart.services`) into `Sequence[OrderLine]` and re-validates availability **and the quantity-1 rule for input-requiring plans** — a cart already refuses the second unit, and checkout checks again rather than trusting it (inactive plan / unavailable product / illegal quantity → `ValidationError`, the page re-renders the cart with the offending line flagged); `customer_inputs` is therefore one string per plan id, never a list; recomputes every price from the database through `catalog.effective_price` (nothing the browser sent is priced); calls `orders.place_order`, which writes `subtotal`, `discount_amount`, `total_amount`, consumes the code and writes the redemption row; clears the cart. Step 2, **after that transaction commits**: `start_gateway_payment`. The gateway HTTP call is never inside the transaction — a slow gateway would hold database locks for the length of a network round trip. If the gateway call then fails, the order simply sits in `PENDING_PAYMENT` and the customer retries from the order page; that is the correct outcome, not an error to unwind. |
| `def start_gateway_payment(*, order: Order) -> str` | created → initiated. Creates (or reuses) the order's live Payment with `method="gateway"`, `amount=order.total_amount`, `idempotency_key`; calls the gateway's request API with the **server-computed** total and the callback URL, stores `authority` + `initiated_at`, returns the redirect URL. One live payment per order (partial unique on `order_id` `WHERE status IN ('created','initiated')`) — an unfinished payment is reused rather than duplicated, and a retry that obtains a fresh token overwrites `authority` on the same row (`authority` is UNIQUE, so a token collision fails loudly instead of mismatching silently). Raises `GatewayUnavailable` on transport/HTTP failure. No events. |
| `def confirm_payment(payment: Payment, *, ref_id: str \| None = None, actor: Actor = "customer") -> Payment` | **The shared confirmation helper and the SOLE emitter of `payment.verified`.** Internal to `payments`; never called from a view. Takes `select_for_update` on the Payment row and **re-reads the status under that lock**; if it is already `verified` it returns the payment unchanged. That lock-and-re-read — never an unlocked `if not payment.verified` check — is what makes confirmation exactly-once no matter how many callbacks, inquiry sweeps, refreshes or double submits arrive, including a callback racing the inquiry task. (`idempotency_key` plays no part here: it is the key sent to the gateway on *initiate*, so a retried initiate cannot create two upstream payment requests.) Otherwise stamps `status="verified"`, `ref_id`, `verified_at`, then calls `orders.services.mark_order_paid(order=…, payment=…)`. Emits `payment.verified` once (customer receipt notice **and** the operator new-order alert on both channels — the alert fires on payment, not on order creation, ADR-0019). Both `verify_payment` (callback and inquiry) and `record_manual_payment` route through it — one confirmation path, one set of item transitions, one event. Tx: one atomic block covering payment + item transitions. |
| `def verify_payment(*, authority: str, actor: Actor = "customer") -> Payment` | initiated → verified \| failed. **The only source of truth for a gateway payment.** Looks the payment up by `authority`; calls the gateway's server-to-server verify; **nothing returned on the browser redirect is trusted** — status, amount and reference all come from the verify response. Compares the gateway-reported amount against `payment.amount` and **rejects a mismatch** via `fail_payment(reason="amount_mismatch")` plus an operator alert — an order is never confirmed for less than it costs; the verified amount is compared and discarded, never stored. On success delegates to `confirm_payment(payment, ref_id=…)`, which owns the confirmation and the event. |
| `def handle_callback(*, params: Mapping[str, str]) -> Payment` | The callback view's single call. Extracts the authority from the return parameters — the only thing the redirect is trusted for — and delegates to `verify_payment`. A callback for an unknown authority is a uniform 404; a callback claiming success for a payment the gateway says is unpaid ends `failed`, because only the verify response decides. |
| `def fail_payment(*, payment: Payment, reason: FailureReason) -> Payment` | initiated → failed. `FailureReason = Literal["gateway_failed","cancelled_by_customer","amount_mismatch","abandoned"]`; stored in `failure_reason`, with `failed_at` stamped. **Cancels nothing.** A failed attempt leaves the order in `PENDING_PAYMENT` and still payable, which is exactly what the retry link in the failure message depends on; cancelling the items would break retry, and stamping `expired_unpaid` on an order that did not expire would be a lie in the audit trail. The unpaid-order sweep, not this service, is what eventually cancels an order nobody paid. Emits `payment.failed` (customer notice carrying a **retry link**, ADR-0019) — and the promise that link makes is why the unpaid window is `unpaid_order_ttl_hours` = 24h, not minutes. State-guarded → replay is a no-op. |
| `def abandon_payment(*, payment: Payment) -> Payment` | initiated → abandoned; sets `failure_reason="abandoned"` and `failed_at`. **Reachable only from `inquire_stale_payments`**, and only after the gateway's inquiry confirms the payment was never completed — never on a timer alone, because "we heard nothing" is not "the customer did not pay". It transitions the payment and **nothing else**: it cancels no items and emits no event (a beat-task outcome with no consumer — the customer never asked for anything to happen). The order stays payable. |
| `def record_manual_payment(*, order: Order, note: str, operator: User) -> Payment` | created → verified, `method="manual"`, actor operator (the operator-only manual fallback, ADR-0019). Free-text `note` is **required** non-empty (bank slip, transfer id, what the operator was told); stores `matched_by=operator`. Routes through `confirm_payment`, so it produces exactly the same confirmed Payment, the same item transitions and the same single `payment.verified` as a gateway verify. Not customer-facing: no receipt, no unique amount, no customer submission. Listed separately in `reconciliation_day_list` by `method`. |
| `def revive_order(*, payment: Payment) -> Order` | Revive orchestrator (one panel screen, one service): per-item `orders.revive` for a **verified** payment whose items were cancelled after the fact. Enforces the revive guard itself: an item is revivable only if `cancel_reason` in {input_timeout, supply_failure, operator} AND no unexecuted Refund row exists for it. |
| `def cancel_with_refund(*, item: OrderItem, reason: CancelReason, refund_destination: str, amount: Decimal, note: str, actor: Actor) -> Refund` | Wraps `orders.cancel(...)` + Refund-row creation **atomically** — the ONE service the panel calls to cancel a paid item (one-service-per-view). Unpaid items go straight to `orders.cancel`. |
| `def record_refund(*, payment: Payment, order_item: OrderItem \| None, amount: Decimal, destination: str, note: str, created_by: User) -> Refund` | Creates the Refund row (nullable item FK for partials). Validates amount ≤ `payment.amount` − prior refunds. No events; no status change. |
| `def execute_gateway_refund(*, refund: Refund, operator: User) -> Refund` | Preferred path where the provider exposes a refund API (ADR-0019): calls it, stores `gateway_refund_ref`, then delegates to `mark_refund_executed`. A gateway refund returns to the **original card automatically** — we never learn its digits, and the customer message never names a card. Raises `GatewayRefundUnsupported` when the provider has no refund API — the operator falls back to a bank transfer and the manual path below. |
| `def mark_refund_executed(*, refund: Refund, bank_ref: str \| None, operator: User) -> Refund` | Sets `executed_at` plus whichever reference exists (`bank_ref` for a manual transfer, `gateway_refund_ref` already set by the call above); then calls `orders.mark_refunded(item=…, refund=…)` for the linked item (order-level refunds: caller loops items). Emits nothing itself — the item transition emits `item.refunded`. Tx: atomic across refund + item. Idempotent on executed_at set. **Either way the Refund row is still the only gate on CANCELLED → REFUNDED** (ADR-0003, state machine A12), and either way the customer message carries only the route-agnostic `refund_ref` — no card digits, on either rail. |

**payments selectors**: `stale_initiated_payments(older_than)` (the sweep's input — `initiated` payments older than `SiteSetting.gateway_timeout_minutes`; the unpaid-order sweep's clock, `unpaid_order_ttl_hours`, is a separate setting read by a separate task), `reconciliation_day_list(date)` (all payments and refunds touching that day, gateway and manual listed separately with the manual rows carrying their `note` and `matched_by` — daily reconciliation), `refunds_pending_execution()`.

Rial appears only at the gateway boundary — the request and verify amounts are sent/compared in rial (`amount_rial = amount_toman * 10`, one tested helper in payments). Everything stored and displayed is toman.

**No PaymentProvider interface (ADR-0013):** the gateway ships as plain `payments.services` functions plus one small client module — no adapter interface, and no scheduled extraction. There is exactly one payment implementation, so an interface would be an abstraction with one caller. Extract it only if a genuine second gateway is ever added; `Payment.method`/`gateway_name` keep the door open.

### notifications

```python
def notify(
    *, event_type: str, dedupe_key: str, user: User | None,   # None = operator recipient (addresses from settings)
    order: Order | None = None, order_item: OrderItem | None = None,
    context: dict[str, Any] | None = None,
    channels: Sequence[Channel] | None = None,   # default: telegram if linked + email
) -> list[Notification]
```
Creates one outbox row per channel, `status=pending`. `dedupe_key` rule: `"{occurrence}:{recipient}:{channel}"` where recipient is the user id or the literal `op` (D17); `IntegrityError` on it is swallowed (duplicate handler fire). The occurrence prefixes are fixed — the canonical registry is **state-machine §3**, and this is the whole list:

| Family | `dedupe_key` |
|---|---|
| item transitions | `evt:{order_item_event_id}:{recipient}:{channel}` |
| payments | `pay:{payment_id}:{to_status}:{recipient}:{channel}` |
| refunds | `refund:{refund_id}:{recipient}:{channel}` |
| renewals — `subscription.expiring_soon` / `subscription.expired` | `renew7:{order_item_id}:{expires_at_iso}:{recipient}:{channel}` / `renew0:…` |
| supply delay | `delay:{item_id}:{new_due_at_iso}:{recipient}:{channel}` |
| input-timeout ladder | `remind:{item_id}:48h\|14d:{recipient}:{channel}` |
| holiday pause/resume | `hpause:{item_id}:{date_iso}:{recipient}:{channel}` / `hresume:…` |
| new-sign-in alert | `signin:{user_id}:{ts_iso}:{recipient}:{channel}` |
| cancellation request | `cancelreq:{item_id}:{requested_at}:op:{channel}` |
| overdue digest | `overdue:{date_iso}T{hour}:op:{channel}` |

So the operator's new-order alert is `pay:1041:verified:op:telegram`, and a delivery notice is `evt:{event_id}:{user_id}:telegram`. Kicks `send_outbox` task on commit. Content-free for delivery events (D13). Channels are exactly `email` and `telegram`: IN_APP cut for phase 1 — the customer order pages are the in-app surface; the channel enum extends cleanly if a bell/inbox is ever wanted.

Channel senders — the only code that talks to the outside; both mocked in tests:

```python
def send_telegram(chat_id: int, text: str) -> None      # raises TelegramSendError
def send_email(to: str, subject: str, body: str) -> None  # raises EmailSendError
```

Event → notification wiring (registered in `ready()`): `payment.verified` → the operator new-order alert on **both** Telegram and email (D17) — it fires on payment, not on order creation, so an unpaid order never pages the operator (ADR-0019) · `payment.verified` (customer receipt notice), `payment.failed` (customer, carries the retry link), `item.awaiting_input`, `item.delivered`, `item.replaced`, `item.replacement_rejected`, `item.cancelled`, `item.refunded` (payload carries `refund_ref` — route-agnostic: `gateway_refund_ref` or `bank_ref`, whichever applies, so the customer is shown a reference without the message caring how the money went back — and **no card digits**: a gateway refund returns to the original card on its own and we never learn them, so there is no `destination_last4` placeholder in any refund template), `item.supply_delayed` (C6), `sla.holiday_paused`/`sla.holiday_resumed`, `subscription.expiring_soon` (7d) / `subscription.expired` (0d) → customer · `item.replacement_requested`, `item.cancellation_requested`, `item.input_received`, `items.overdue_digest` → operator. There is no `order.created` and no `telegram.linked`: the first would page the operator for an order nobody paid for, and the second had no consumer because the bot already replies in line.

Webhook: `telegram_webhook(request)` view at a secret path, verifies `X-Telegram-Bot-Api-Secret-Token`, handles `/start lnk_<token>` → `accounts.complete_telegram_link`, and the link-from-bot email/code exchange → `accounts.start_link_from_bot` / `complete_link_from_bot` (D4: in-process service calls, no DRF).

---

## 3. Celery Tasks & Beat Schedule

All tasks are state-guarded and safe to double-run (beat + manual). `acks_late=True`, bound, with `max_retries` where they touch the network.

| Task | Cadence | What it does | Idempotency |
|---|---|---|---|
| `payments.tasks.inquire_stale_payments` | every 5 min | **Lost-callback recovery (mandatory, ADR-0019).** For each `initiated` payment older than `SiteSetting.gateway_timeout_minutes` (15), calls the gateway's inquiry/status API. Reported **paid** → confirmed by this task on exactly the same path a callback takes (`verify_payment` → `confirm_payment`, same amount comparison, same item transitions, same single `payment.verified`) — this is what saves "customer paid, network died before the redirect": money taken, order otherwise dead. Where the paid payment's items were already cancelled, the task confirms and **alerts the operator**; it never revives the items itself — the operator does that from the panel. Reported **not paid** → `abandon_payment`; inquiry unreachable → left alone for the next sweep, never abandoned on a timer. | status-guarded transitions + `confirm_payment`'s row lock and early return; a payment confirmed by callback and by sweep still confirms once |
| `notifications.tasks.send_outbox` | every 1 min **and** kicked on-commit by `notify()` | picks `status=pending, next_attempt_at<=now` with `select_for_update(skip_locked=True)`, sends, marks sent/failed. Backoff per the notification contract's fixed ladder: attempt immediately, then 1m, 5m, 15m, 1h, 6h (6 attempts total), then `status=failed`; non-retryable classes fast-fail to `failed` (Telegram 403/400 — bot blocked / invalid chat — and hard email bounces) | at-least-once send; row status + dedupe_key prevent duplicates at enqueue; per-row lock prevents concurrent double-send |
| `orders.tasks.renewal_reminders` | daily 09:00 | `items_expiring(today+7)` and `items_expiring(today)` → `notify` with direct repurchase link (channel="legacy" rows included) | dedupe_key `renew7:{order_item_id}:{expires_at_iso}:{recipient}:{channel}` / `renew0:…` — keys include `expires_at`, so a later extension of `expires_at` legitimately re-arms the reminder |
| `orders.tasks.awaiting_input_followup` | daily 10:00 | the input-timeout ladder (ADR-0009): 48h re-reminder → 7d operator queue flag → 14d automated final warning naming the deadline (`item.input_final_warning`) → **21d system auto-cancel** via `payments.cancel_with_refund(reason="input_timeout")` with destination blank (collected from the customer before execution). Anchor = the AI-entry OrderItemEvent; holiday mode does not pause the ladder | `remind:{item}:48h:…` / `remind:{item}:14d:…`; the cancel is state-guarded |
| `orders.tasks.overdue_digest` | hourly, during support hours only | ONE operator digest per hour ("N سفارش گذشته از مهلت") for `overdue_items()` (D20 alarm) — no per-item alerts. Emits `items.overdue_digest` | dedupe_key `overdue:{date_iso}T{hour}:op:{channel}` |
| `orders.tasks.cancel_unpaid_orders` | daily | cancels orders left in `PENDING_PAYMENT` with no verified payment for longer than `SiteSetting.unpaid_order_ttl_hours` (24) via `orders.cancel(reason="expired_unpaid", actor="system")` — a different clock from `gateway_timeout_minutes`, and never the same field — and **skips any order whose payment is still `initiated`**: money may be moving, and the inquiry sweep has not finished deciding | status-guarded; the `initiated` skip is a named test |
| `cart.tasks.purge_stale_guest_carts` | daily 03:20 | deletes guest `Cart` rows (`session_key` set) untouched for 30 days, cascading their items. Carts owned by a user are never purged | date-bounded delete; re-run is a no-op |
| `orders.tasks.purge_expired_credentials` | daily 03:10 | hard-deletes DeliveryField rows (all generations) and encrypted `customer_input` at `expires_at + warranty window + 30 days` (owner-accepted); writes an annotation OrderItemEvent noting the purge | date-guarded; re-run is a no-op |
| `orders.tasks.purge_old_access_logs` | monthly | deletes CredentialAccessLog rows older than 1 year (owner-accepted); OrderItemEvent is NOT purged — it is the order audit trail | date-bounded delete |
| `core.tasks.heartbeat` | every 5 min (beat) | GET to healthchecks.io-class URL — dead beat/worker alerts from **outside** the app (D17) | trivially |

Outbound `sendMessage` calls for the bot are just `send_outbox` doing its job — no separate bot task.

---

## 4. Thin Views, Concretely

A view (HTML or HTMX partial) may do exactly five things:

1. **Auth/permission**: `login_required` / staff check; object fetch **via a selector** that already scopes ownership (`customer_orders(user)` — never `Order.objects.get(pk)`).
2. **Parse + validate input** with a Django `Form` (format-level validation only: required, max_length, choices; *business* validation lives in the service and its `ValidationError` is re-rendered on the form). A **price, a discount amount or an order total submitted by the browser is never read** — the form carries plan ids, quantities and a discount *code*, nothing more; every number is recomputed server-side.
3. **Call at most one service function** (GET pages call selectors only).
4. **Render** a template / return an HTMX fragment / redirect, with `messages` for feedback.
5. Set response headers the page needs (`Cache-Control: private, no-store` on panel/tracking/delivery-link (`/d/<token>/`)/HTMX-per-user endpoints, D19 — via middleware/decorator in core; the delivery-link page also sends `Referrer-Policy: no-referrer`, ADR-0008).

A view may **not**: assign model fields, open transactions, branch on business state (beyond choosing a template), call two services, enqueue tasks, or send anything. Templates: presentation logic only — status→color/text mapping via a template tag fed from one dict in orders, Jalali/digit formatting via core template tags. Money in a template is rendered by the core money tag alone (Persian digits, ASCII thousands separator, toman only, the currency word lighter than the numeral, tabular figures so a column of totals aligns); the cart and checkout summaries render subtotal, discount and total as three separate **ledger lines**, never one collapsed figure. The delivery page POST is the canonical example: one `DeliveryForm`, one call to `orders.services.deliver(...)`, redirect to queue.

Two views carry extra constraints:

- **Cart views** (add / set quantity / remove) call exactly one `cart.services` helper and return the re-rendered summary fragment. They are POST-only + CSRF-protected and work anonymously — the shop has no sign-in wall (ADR-0018). A refusal — a second unit of an input-requiring plan, say — comes back as the service's `ValidationError` message on the same fragment. They write `Cart`/`CartItem` rows, not the session; the session only ever supplies the key a guest cart is filed under. The guest-to-account merge is not a view at all: `merge_carts` runs on the `user_logged_in` signal, so every sign-in path — the login page and the inline checkout OTP alike — gets it without remembering to ask.
- **The gateway callback view** calls only `payments.handle_callback(params=request.GET)`. It is CSRF-exempt (the gateway posts/redirects from outside), reads the return parameters for the authority and nothing else, and is safe to hit repeatedly — confirmation is idempotent and decided by the server-to-server verify, not by anything in the URL. It renders the paid-or-failed result page; it never renders credentials.

---

## 5. Testing Seams

**Mocked (the only things mocked):**

| Seam | How |
|---|---|
| Telegram transport | `notifications.senders.send_telegram` — single function, monkeypatched/`unittest.mock`; webhook tests post signed fake updates |
| Email transport | Django `locmem` backend; assert on `mail.outbox` |
| Gateway HTTP | the payments client's four calls (`request`, `verify`, `inquire`, `refund`) — one module, monkeypatched; fixtures for paid, unpaid, amount-mismatch, already-verified and transport-error responses |
| Clock | `time_machine`/`freezegun` for `compute_due_at`, SLA pause math, the stale-payment threshold, discount validity windows, renewal scans |
| Heartbeat HTTP | mock `requests` in `core.tasks.heartbeat` |
| Fernet key | fixed test key in test settings; one test decrypts a stored `DeliveryField` and round-trips key rotation via MultiFernet |
| Celery | `task_always_eager` off by default — services are called directly; task tests call task functions synchronously |

Everything else (Postgres constraints, the state machine, ownership, `select_for_update` on the discount row and on the payment row) runs against the real test DB — row locking and the partial unique index on live payments (`WHERE status IN ('created','initiated')`) **are** test subjects, so no sqlite. The concurrent-discount test needs real transactions: two threads racing one single-use code, exactly one wins.

**Factories** (`factory_boy`): `UserFactory` (traits: `verified`, `telegram_linked`, `staff`) · `CategoryFactory` · `ProductFactory` (trait: `with_template`) · `ProductSpecFactory` · `PlanFactory` (traits: `requires_input`, `unavailable`, `on_promo`, `promo_expired`) · `CartFactory` (traits: `guest`, `owned`) · `CartItemFactory` · `OrderFactory` · `OrderItemFactory` (state traits for all 7 statuses, `paused`, `cancellation_requested`, `legacy`) · `DeliveryFieldFactory` (trait: `superseded`) · `PaymentFactory` (state traits for created/initiated/verified/failed/abandoned, trait `manual`) · `RefundFactory` (traits: `executed`, `gateway`) · `DiscountCodeFactory` (traits: `percent`, `fixed`, `single_use`, `expired`, `scoped`) · `DiscountRedemptionFactory` · `NotificationFactory` · `SiteSettingFactory` · `PageFactory`.

Must-cover suites (per working agreement §11): full transition matrix (legal + every illegal pair raises, DELIVERED→CANCELLED forbidden, both REPLACEMENT_REQUESTED→DELIVERED variants A10a/A10b), `compute_due_at` boundary table, `extend_due_at`, **confirm_payment idempotency** (callback replayed, callback + inquiry sweep racing → one confirmation, one event, one set of transitions), **amount-mismatch rejection** (gateway reports less than the total → never verified), **redirect parameters are not trusted** (a forged success callback for an unpaid authority ends `failed`), **a failed payment leaves the order PENDING_PAYMENT and payable** (`fail_payment` cancels nothing; the retry link works), **`abandon_payment` cancels nothing and emits nothing**, **the unpaid-order sweep skips an order whose payment is still `initiated`**, lost-callback recovery via the inquiry sweep, manual payment producing the identical post-verify state through the same `confirm_payment`, `revive` recomputing `due_at` from now (a payment verified weeks ago must not yield an instantly-overdue item), `effective_price` boundary table, discount validation matrix + scoped-code eligible-subtotal arithmetic + `ROUND_HALF_UP` rounding + concurrent single-use consumption + `total_amount >= 0` clamp, the order-total invariant `subtotal − discount_amount = total_amount`, **cart merge on login** (guest cart claimed when the account has none; quantities summed and clamped when it does), cart price recomputation (promotional price started or ended, plan gone unavailable between add and checkout), **the quantity-1 cap on input-requiring plans** (the cart refuses the second unit; `place_order` refuses it again if it ever arrives), ownership on order pages + reveal, encryption round-trip, Sentry scrubber leak test (D20), end-to-end cart→checkout→gateway verify→deliver→notify flow.

---

## Concerns

1. **Revive vs. pending Refund (D1×D2):** an item can be CANCELLED with a Refund recorded but not yet executed, and revive (CANCELLED→QUEUED) is legal. Reviving while a refund is in flight double-pays the obligation. **Resolved:** the guard (eligible `cancel_reason` + no unexecuted Refund) lives in `payments.revive_order`, the sole caller of `orders.revive` — no caller-supplied booleans.
2. **`due_at` base after revive (D7×D2):** computing from the original `verified_at` on an order revived weeks later yields an already-overdue item. `orders.revive` recomputes `due_at` from now via `compute_due_at`, not from the payment; `verified_at` stays the base only for the normal first pass.
3. **A verify that succeeds at the gateway but fails to commit locally** (worker killed between the gateway's OK and the transaction commit) leaves money taken and the payment `initiated`. **Resolved by the mandatory inquiry sweep (ADR-0019):** it re-asks the gateway and confirms it on the next run — which is exactly why the sweep is mandatory rather than a nicety.
4. **Discount code consumed, then the gateway start fails.** `payments.checkout` commits the order (and the code consumption) *before* calling the gateway, because holding a row lock across an outbound HTTP call is how a slow gateway becomes a database outage. **Resolved by accepting the outcome instead of engineering around it:** the order sits in `PENDING_PAYMENT`, fully payable, and the customer retries from the order page — the code is spent on an order that still exists and still owes money, which is correct. No compensating decrement, no orphan cleanup.
5. **Guest cart merged into an account that already has one.** Summing quantities can exceed the per-line maximum. `merge_carts` clamps, does not reject — a customer told "your cart could not be merged" has lost trust over arithmetic. The clamp is tested.
6. **Brief's `unique_together(order_item, event_type, channel)` dedup is overridden by D17's dedupe_key** — followed D17; noting only because the original brief text still says otherwise.
