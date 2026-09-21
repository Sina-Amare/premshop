# PremShop — Module Boundaries & Service Layer Contract (Phase 1)

> **At a glance.** Which app owns what, which app may import which, and the service contracts for the next two steps.
> **Built:** `accounts`, `catalog`, and `core`'s email, rate-limit and display helpers. For these the code is the source of truth; §2 keeps only what the code cannot say, plus a pointer to the module.
> **Contract, settled at the step that builds it:** `orders` (S4a — `place_order`, `validate_discount_code` and the customer selectors are used from S4b; its §2 table still carries full signatures for operations the build plan places at S7–S10, such as `extend_due_at`, `request_cancellation`, `pause_all_slas` and `reveal_delivery_fields`, and whether they stay contract is S4a's briefing's call), `cart` (S4b), `payments.checkout` (S4b's checkout entry — **disputed**: the build plan has S4b's view call `orders.place_order` directly and brings `payments.checkout` at S5; settled in S4a's briefing), `core`'s `emit`/`register` and `compute_due_at`, the guest-cart purge task (§3), §4 Thin Views, §5 Testing Seams.
> **Intent only, re-specified when their step is planned:** the rest of `payments` (S6b and S5), `notifications` (S6), Telegram linking (S8), the operator and renewal selectors (S7–S10), the other beat tasks (S5–S10). `panel` (S7) and `cms` (S11) appear only in the App Map. The full earlier draft of all of it is `git show 027c5d1:docs/services-and-modules.md`.
> **Read for S4a:** §1 App Map, §2 `orders`, §5 Testing Seams.

## 1. App Map

Dependency direction is a strict order. An app may import only from apps **above** it in this list (plus stdlib/Django/libs). Panel sits at the top and imports everyone; nothing imports panel. `cms` and `catalog` are domain-leaf apps.

```
core  →  accounts, catalog, cms  →  cart, orders  →  payments  →  notifications  →  panel
```

`cart` and `orders` are **siblings, not a chain**: neither imports the other. That is what keeps `orders` ignorant of how a cart is stored — the rule that lets `place_order` take a plain `Sequence[OrderLine]` — and `payments.checkout` is the single place the two meet (ADR-0018).

| App | Owns (models) | Owns (logic) | May import | Never |
|---|---|---|---|---|
| **core** | `SiteSetting` (a singleton whose columns arrive with the steps that use them — payment clocks S5, support hours S7, holiday switches S10; two clocks, never one field, ADR-0019; columns: `data-model.md`) | `EncryptedTextField` (Fernet/MultiFernet, ~30 lines), money/digit/Jalali format helpers, templated email sending (`core.email.send_templated_email(name, *, to, context) -> int` — multipart, three template files per message, ADR-0023), the rate limiter (`core.ratelimit`), `compute_due_at` pure function, `events.emit`/`events.register`, heartbeat task | nothing domain-side | importing any other app |
| **accounts** | `User` (email USERNAME_FIELD, is_verified, phone (CharField, blank=''), telegram_id/username/linked_at), OTP + telegram-link token state | auth services: OTP login, inline checkout OTP, telegram linking both directions | core | orders, payments |
| **catalog** | `Category` (flat, no parent), `Product` (incl. delivery_template, delivery_hours, region, warranty, search_text column — deliberately unindexed: icontains cannot use btree, seq scan is fine at this catalog size, pg_trgm is the named upgrade path; status third value is "unavailable"), `ProductSpec`, `Plan` (cost_price, sale_price, promo_price, promo_starts_at, promo_ends_at, requires_customer_input, duration_days, supplier_url — per-plan upstream listing, owner ruling) | `effective_price` — the single source of the pricing rule; selectors only (search, active listings); admin with inlines (promotional pricing is set per plan, no code required); Persian search normalisation (`catalog.search.normalize_for_search`, ADR-0025), applied on save | core | any domain app |
| **cms** | `Page`, `FAQ` | admin-edited content, public views | core | any domain app |
| **cart** | `Cart`, `CartItem` (ADR-0018) | the **persistent cart** — resolution by user or session key, add/set/remove, the guest-to-account merge on login, the `cart_summary` selector, the stale-guest-cart purge | core, accounts, catalog | **orders**, payments, notifications, panel. Imported by payments (which resolves a cart into order lines at checkout) and panel |
| **orders** | `Order` (channel web\|bot\|legacy, tracking_token, order_number, subtotal, discount_code FK PROTECT, discount_amount, total_amount), `OrderItem` (7 statuses, price_snapshot, cost_snapshot, actual_cost, paid_at, sla_paused_at, cancel_reason, cancellation_requested_at, encrypted customer_input, due_at, expires_at), `DiscountCode` (ADR-0020 — lives here because `Order` FKs it PROTECT), `DiscountRedemption`, `DeliveryField` (encrypted value, is_current), `OrderItemEvent`, `CredentialAccessLog` | the state machine, place_order, all transitions, delivery, SLA pause/resume, reveal logging, delivery-link issue/redeem + the no-login masked view (ADR-0008), legacy backfill, discount validation/consumption/redemption records, customer order pages + public tracking page | core, accounts, catalog | cart, payments, notifications, panel. Where a transition is payment-guarded (revive, refunded), the **caller passes the Payment/Refund object in**; orders validates its attributes, never imports payments |
| **payments** | `Payment`, `Refund` (S6b, completed at S5; columns: `data-model.md`) | payment machine (created→initiated→verified\|failed\|abandoned, plus created→verified for the manual fallback — ADR-0019), `confirm_payment` (the shared confirmation helper: a `select_for_update` row lock plus a re-read of the status under that lock, returning the payment unchanged when it is already `verified` — that is the idempotency guarantee, never an unlocked "if not verified" check), the gateway client (start/verify/inquire/refund), the stale-payment inquiry sweep, checkout/revive/cancel-with-refund orchestrators, refund ledger. Plain service functions — no PaymentProvider interface (ADR-0013: one implementation, no interface). Rial conversion lives ONLY here | core, accounts, catalog, cart (resolves a cart into order lines at checkout), orders (calls orders services on verify/refund-executed) | notifications, panel |
| **notifications** | `Notification` outbox (S6; columns: `data-model.md`) | `notify()`, email + telegram channel senders, telegram webhook view (secret path + `X-Telegram-Bot-Api-Secret-Token`), outbox worker, event handlers registered against `core.events` at AppConfig.ready() | core, accounts, orders, payments (selectors, to render message context) | panel |
| **panel** | **no models** | operator queue, delivery page, dashboard, the "record payment received outside the gateway" action (the operator-only manual fallback, ADR-0019), refund forms, discount-code admin, reconciliation view, holiday switch — all staff-only views calling other apps' services/selectors | everyone's services + selectors | defining business logic; touching models directly for writes |

Responsibility placement from the brief: SLA math → core (pure) + orders (applies it) · gateway verify + inquiry sweep → payments · the persistent cart → cart · discount validation → orders · promotional pricing (`effective_price`) → catalog, and every price shown or charged goes through it · masked reveal + access log → orders · outbox/retry → notifications · operator daily loop → panel · Enamad pages → cms · search → catalog (normalize + selector, ADR-0025) · Jalali/digits → core template tags · reviews → phase-2 app, absent from skeleton · **no DRF anywhere** (D4): the bot is one webhook view in notifications calling services in-process.

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

`SiteSetting.load()` — the cached singleton the schedule and the two payment clocks are read from — is intent until S5, when `core` gets its first model.

Built (`apps/core/`; the code is the reference): `email.send_templated_email(name, *, to, context) -> int` renders and sends one multipart message from three template files, so no Persian string lives in Python (ADR-0023); `ratelimit` is the fixed-window limiter over the cache, and every auth endpoint is limited per identity **and** per IP; `formatting` and the `fa` template filters are the Persian digit, toman and Jalali display helpers. Persian search normalisation is not in core: it is `catalog.search.normalize_for_search` (ADR-0025).

### accounts

Built — `apps/accounts/services.py` is the reference. Two services: `request_login_code(request, email) -> otp.Issued`, which raises `RateLimited`, and `complete_code_login(request, email, code) -> User | None`. Both take the `request` for the client IP (the per-IP rate limits); `complete_code_login` also needs it for the session, because signing in rotates the session key. Password login is not a service: it lives in the view, `login_password` in `apps/accounts/views.py`. The new-sign-in alert (C14) goes out when a code signs into an account that has a password; today `services._send_signin_alert` sends it directly and synchronously by email, and a mail failure is logged and never fails the login. There is no outbox and no Telegram yet (S6, S8). The checkout note below still uses the earlier draft's names `start_otp`/`verify_otp`: the built equivalents are the two functions above, and whether S4b's inline checkout OTP (ADR-0012) needs more than them — the draft gave `start_otp` a `purpose="checkout"` parameter — is S4b's decision.

Note: `place_order` does **not** require `is_verified` (D6 — no hard block); the checkout view runs `start_otp`/`verify_otp` inline for anonymous users before calling `payments.checkout`, which is where verification happens in practice. Catalog, product pages, search **and the cart** are fully anonymous — identity is collected inside checkout by this inline OTP, which doubles as signup and email verification; profile details come later from the dashboard. No sign-in wall in front of the shop (ADR-0018). The guest cart survives the OTP sign-in because `cart.services.merge_carts` runs on the login signal — see the cart app below.

Telegram linking (S8) is intent only here: a customer links their account to one Telegram account, either from the site (a short-lived, single-use token opened in the bot) or from the bot (email, then an emailed code, creating the account if there is none). The unique `User.telegram_id` column is the backstop for one-to-one in both directions, and the bot reaches these services in-process through the notifications webhook view — no API layer (ADR-0004). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§2 accounts). It is re-specified when S8 is planned.

### catalog

Built — `apps/catalog/pricing.py` (`effective_price`), the `Plan` checks in `apps/catalog/models.py` and their boundary tests in `apps/catalog/tests/test_models.py` are the reference. What the code cannot say is that the unbuilt apps are bound by it too: every cart line, the checkout summary and `OrderItem.price_snapshot` are priced through `effective_price`, and a price computed anywhere without it in the call stack is the defect (ADR-0021).

### cart

Its own app (ADR-0018), sibling to `orders`: it imports `core`, `accounts` and `catalog`, and **never `orders`** — which is the other half of why `place_order` takes a plain `Sequence[OrderLine]`. A cart is resolved by **user when signed in, `session_key` otherwise** — so a guest's cart survives closing the browser (`SESSION_EXPIRE_AT_BROWSER_CLOSE=False`, `SESSION_COOKIE_AGE=30 days`) and a customer's cart follows them across devices. `CartItem` stores **only** `plan` and `quantity` (1–10, DB CHECK) — never prices, never names; every amount is recomputed from the database on every render. These helpers take `request` because resolution needs both the user and the session key; they touch nothing else on it, and the only other services in the codebase that accept a request are `accounts`' two sign-in services, which need the session and the client IP.

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
| `customer_orders(user)` / `order_for_tracking(token)` | customer list; tracking page gets status timeline + product name ONLY, uniform not-found (D12). Order display status for n>1 derives from items by stage precedence — PENDING_PAYMENT < QUEUED < AWAITING_INPUT < REPLACEMENT_REQUESTED < DELIVERED, terminal label only when all items are terminal — multi-plan carts make this the normal case, not the exception (ADR-0018) |

The operator-side selectors are intent only here: the operator queue and its stat bar, the dashboard aggregates and the overdue list (S7), stale awaiting-input items for the input-timeout ladder (S8), and the renewal scan `items_expiring` (S10). They are read-only queries in `selectors.py` that the panel and the beat tasks render from (ADR-0002). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§2 orders selectors). It is re-specified when each selector's step (S7–S10) is planned.

### payments

| Signature | Behavior |
|---|---|
| `def checkout(*, user: User, request, phone: str, customer_inputs: dict[int, str], discount_code: str \| None) -> tuple[Order, str]` | The checkout orchestrator — the checkout view calls **only this**, and gets back the order plus the **gateway redirect URL**. The checkout page has already shown the summary (subtotal, discount, total) computed server-side by `cart_summary` *before* submission; submitting is what creates the order and starts the payment. `payments` is the one place `cart` and `orders` meet — it imports both, and neither imports the other. Step 1, in ONE transaction: resolves the visitor's `Cart` (via `cart.services`) into `Sequence[OrderLine]` and re-validates availability **and the quantity-1 rule for input-requiring plans** — a cart already refuses the second unit, and checkout checks again rather than trusting it (inactive plan / unavailable product / illegal quantity → `ValidationError`, the page re-renders the cart with the offending line flagged); `customer_inputs` is therefore one string per plan id, never a list; recomputes every price from the database through `catalog.effective_price` (nothing the browser sent is priced); calls `orders.place_order`, which writes `subtotal`, `discount_amount`, `total_amount`, consumes the code and writes the redemption row; clears the cart. Step 2, **after that transaction commits**: `start_gateway_payment`. The gateway HTTP call is never inside the transaction — a slow gateway would hold database locks for the length of a network round trip. If the gateway call then fails, the order simply sits in `PENDING_PAYMENT` and the customer retries from the order page; that is the correct outcome, not an error to unwind. |

The rest of `payments` is intent only here: the gateway (S5), with the payment tables and the operator-only manual fallback pulled forward to S6b. It runs the payment machine `created → initiated → verified | failed | abandoned`, plus `created → verified` for the manual fallback, under the rules ADR-0019 already fixes: the server-to-server verify is the only source of truth and nothing on the browser redirect is trusted; the verified amount is compared with the order total and a mismatch is rejected; `confirm_payment` is the one confirmation path and the only emitter of `payment.verified`, made exactly-once by a `select_for_update` lock and a re-read of the status under it (the UNIQUE `idempotency_key` guards the initiate call, not confirmation); a mandatory inquiry sweep recovers lost callbacks and is the only thing that abandons a payment; a failed payment leaves the order waiting with a retry link, and the separate 24-hour unpaid sweep cancels it later but skips any order whose payment is still `initiated`; the manual fallback confirms through the same path; refunds prefer the gateway's refund API, the refund message names no card, and no item reaches REFUNDED without an executed `Refund` row (ADR-0003). No PaymentProvider interface until a second gateway exists (ADR-0013); rial exists only inside the gateway boundary (ADR-0005). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§2 payments, with its selectors and the rial and no-interface paragraphs). The payment functions this file names elsewhere — `start_gateway_payment`, `handle_callback`, `confirm_payment`, `cancel_with_refund`, `revive_order` — are specified then. It is re-specified when S5 is planned.

### notifications

Intent only here (S6, with Telegram in S8). It is the outbox of ADR-0010: `notify()` writes one row per occurrence, recipient and channel under a globally unique `dedupe_key` of the form `{occurrence}:{recipient}:{channel}` (recipient `op` for the operator; the canonical occurrence prefixes are state-machine §3); handlers are registered against `core.events` in `AppConfig.ready()` and run on commit; a worker sends with `select_for_update(skip_locked=True)` on a fixed backoff ladder; channels are exactly `email` and `telegram`, and operator alerts go out on both. The operator's new-order alert fires on `payment.verified`, never on order creation (ADR-0019), and no message carries a credential value or `customer_input` — a delivery notice carries the single-use link instead (ADR-0008). Email sending is already built: the notifications app will call `core.email.send_templated_email` (ADR-0023), not the draft's `send_email(to, subject, body)`. The bot is one webhook view calling services in-process (ADR-0004). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§2 notifications). It is re-specified when S6 is planned.

---

## 3. Celery Tasks & Beat Schedule

All tasks are state-guarded and safe to double-run (beat + manual). `acks_late=True`, bound, with `max_retries` where they touch the network.

| Task | Cadence | What it does | Idempotency |
|---|---|---|---|
| `cart.tasks.purge_stale_guest_carts` | daily 03:20 | deletes guest `Cart` rows (`session_key` set) untouched for 30 days, cascading their items. Carts owned by a user are never purged | date-bounded delete; re-run is a no-op |

The other scheduled work is intent only here, each task state-guarded and safe to re-run as above: the mandatory lost-callback inquiry sweep and the unpaid-order sweep (S5, on ADR-0019's two separate clocks), the outbox sender and the external heartbeat (S6, ADR-0010), the input-timeout ladder (S8, ADR-0009), renewal reminders (S10), the hourly overdue digest to the operator, and the credential and access-log purges that enforce ADR-0007's retention. Outbound bot messages are ordinary outbox sends, not a task of their own. Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§3). It is re-specified when each task's step (S5–S10) is planned.

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
- **The gateway callback view** (S5) is intent here: it looks up the payment from the return parameters and trusts nothing else in them, confirms only through the server-to-server verify, and is safe to hit repeatedly (ADR-0019). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§4). It is re-specified when S5 is planned.

---

## 5. Testing Seams

**Mocked (the only things mocked):**

| Seam | How |
|---|---|
| Email transport | Django `locmem` backend; assert on `mail.outbox` |
| Later transports (intent) | the Telegram sender (S6/S8), the gateway client's four calls (S5), the external heartbeat (S6) — each one module, monkeypatched; re-specified with its step |
| Clock | `time_machine`/`freezegun` for `compute_due_at`, SLA pause math, the stale-payment threshold, discount validity windows, renewal scans |
| Fernet key | fixed test key in test settings; one test decrypts a stored `DeliveryField` and round-trips key rotation via MultiFernet |
| Celery | `task_always_eager` off by default — services are called directly; task tests call task functions synchronously |

Everything else (Postgres constraints, the state machine, ownership, `select_for_update` on the discount row) runs against the real test DB — row locking **is** a test subject, so no sqlite; the payment row's lock and the live-payment index join at S6b/S5. The concurrent-discount test needs real transactions: two threads racing one single-use code, exactly one wins.

**Factories** (`factory_boy`): `UserFactory` (traits: `verified`, `telegram_linked`, `staff`) · `CategoryFactory` · `ProductFactory` (trait: `with_template`) · `ProductSpecFactory` · `PlanFactory` (traits: `requires_input`, `unavailable`, `on_promo`, `promo_expired`) · `CartFactory` (traits: `guest`, `owned`) · `CartItemFactory` · `OrderFactory` · `OrderItemFactory` (state traits for all 7 statuses, `paused`, `cancellation_requested`, `legacy`) · `DeliveryFieldFactory` (trait: `superseded`) · `DiscountCodeFactory` (traits: `percent`, `fixed`, `single_use`, `expired`, `scoped`) · `DiscountRedemptionFactory`. Factories for later tables (Payment, Refund, Notification, SiteSetting, Page) arrive with their step. Whether to use factory_boy at all is open: the built tests use plain pytest fixtures (S4a's briefing).

Must-cover suites (per working agreement §11): full transition matrix (legal + every illegal pair raises, DELIVERED→CANCELLED forbidden, both REPLACEMENT_REQUESTED→DELIVERED variants A10a/A10b), `compute_due_at` boundary table, `extend_due_at`, from S5 the payment suites ADR-0019 names (idempotent confirmation, amount mismatch, untrusted redirect parameters, lost-callback recovery, the manual fallback), `revive` recomputing `due_at` from now (a payment verified weeks ago must not yield an instantly-overdue item), `effective_price` boundary table, discount validation matrix + scoped-code eligible-subtotal arithmetic + `ROUND_HALF_UP` rounding + concurrent single-use consumption + `total_amount >= 0` clamp, the order-total invariant `subtotal − discount_amount = total_amount`, **cart merge on login** (guest cart claimed when the account has none; quantities summed and clamped when it does), cart price recomputation (promotional price started or ended, plan gone unavailable between add and checkout), **the quantity-1 cap on input-requiring plans** (the cart refuses the second unit; `place_order` refuses it again if it ever arrives), ownership on order pages + reveal, encryption round-trip, Sentry scrubber leak test (D20), end-to-end cart→checkout→gateway verify→deliver→notify flow.

---

## Concerns

1. **Revive vs. pending Refund (D1×D2):** an item can be CANCELLED with a Refund recorded but not yet executed, and revive (CANCELLED→QUEUED) is legal. Reviving while a refund is in flight double-pays the obligation. **Resolved:** the guard (eligible `cancel_reason` + no unexecuted Refund) lives in `payments.revive_order`, the sole caller of `orders.revive` — no caller-supplied booleans.
2. **`due_at` base after revive (D7×D2):** computing from the original `verified_at` on an order revived weeks later yields an already-overdue item. `orders.revive` recomputes `due_at` from now via `compute_due_at`, not from the payment; `verified_at` stays the base only for the normal first pass.
3. **A verify that succeeds at the gateway but fails to commit locally** (S5) — covered by the mandatory inquiry sweep (ADR-0019). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (Concerns #3). It is re-specified when S5 is planned.
4. **Discount code consumed, then the gateway start fails.** `payments.checkout` commits the order (and the code consumption) *before* calling the gateway, because holding a row lock across an outbound HTTP call is how a slow gateway becomes a database outage. **Resolved by accepting the outcome instead of engineering around it:** the order sits in `PENDING_PAYMENT`, fully payable, and the customer retries from the order page — the code is spent on an order that still exists and still owes money, which is correct. No compensating decrement, no orphan cleanup.
5. **Guest cart merged into an account that already has one.** Summing quantities can exceed the per-line maximum. `merge_carts` clamps, does not reject — a customer told "your cart could not be merged" has lost trust over arithmetic. The clamp is tested.
6. **The brief's `unique_together(order_item, event_type, channel)` notification dedup** (S6) — overridden by the per-occurrence `dedupe_key` (ADR-0010). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (Concerns #6). It is re-specified when S6 is planned.
