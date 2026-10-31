# PremShop — Module Boundaries & Service Layer Contract (Phase 1)

> **At a glance.** Which app owns what, which app may import which, and the service contracts for the next two steps.
> **Built:** `accounts`, `catalog`, `cms`, `cart`, `payments.checkout` (the cart/order coordinator, without a Payment row), `orders`' unpaid creation/cancellation, customer selectors and read-only admin, and `core`'s email, rate-limit, display and encrypted-field helpers. For these the code is the source of truth; §2 keeps only what the code cannot say, plus a pointer to the module.
> **Contract, settled at the step that builds it:** remaining `orders` operations (its §2 table still carries full signatures for later operations such as `extend_due_at`, `request_cancellation`, `pause_all_slas` and `reveal_delivery_fields`), `core`'s `emit`/`register` and `compute_due_at`, the remaining task consumers (§3), §4 Thin Views, §5 Testing Seams.
> **Intent only, re-specified when their step is planned:** the rest of `payments` (S6b and S5), `notifications` (S6), Telegram linking (S8), the operator and renewal selectors (S7–S10), the other beat tasks (S5–S10). `panel` (S7) appears only in the App Map. The full earlier draft of all of it is `git show 027c5d1:docs/services-and-modules.md`.
> **Read for S4a:** §1 App Map, §2 `orders`, §5 Testing Seams.

## 1. App Map

Dependency direction is a strict order, with one exception in the accounts row. An app may import only from apps **above** it in this list (plus stdlib/Django/libs). Panel sits at the top and imports everyone; nothing imports panel. `cms` and `catalog` are domain-leaf apps.

```
core  →  accounts, catalog, cms  →  cart, orders  →  payments  →  notifications  →  panel
```

`cart` and `orders` are **siblings, not a chain**: neither imports the other. That is what keeps `orders` ignorant of how a cart is stored — the rule that lets `place_order` take a plain `Sequence[OrderLine]` — and `payments.checkout` is the single place the two meet (ADR-0018).

| App | Owns (models) | Owns (logic) | May import | Never |
|---|---|---|---|---|
| **core** | `SiteSetting` (a singleton whose columns arrive with the steps that use them — payment clocks S5, support hours S7, holiday switches S10; two clocks, never one field, ADR-0019; columns: `data-model.md`) | `EncryptedTextField` (Fernet/MultiFernet, ~30 lines), money/digit/Jalali format helpers, templated email sending (`core.email.send_templated_email(name, *, to, context) -> int` — multipart, three template files per message, ADR-0023), the rate limiter (`core.ratelimit`), `compute_due_at` pure function, `events.emit`/`events.register`, heartbeat task | nothing domain-side | importing any other app |
| **accounts** | `User` (email USERNAME_FIELD, is_verified, phone (CharField, blank=''), telegram_id/username/linked_at), OTP + telegram-link token state | auth services: OTP login, inline checkout OTP, telegram linking both directions | core; the account page's view reads `orders.selectors` | payments |
| **catalog** | `Category` (flat, no parent), `Product` (incl. delivery_template, delivery_hours, warranty, the page's facts — usage_notes, refund_note, sources, facts_checked_on, time_sensitive — and a search_text column with a trigram index whose use is discussed in `data-model.md` §4; status third value is "unavailable"), `ProductSpec`, `ProductSection` and `ProductQuestion` (the page's prose and its questions, ADR-0028), `Plan` (cost_price, sale_price, promo_price, promo_starts_at, promo_ends_at, requires_customer_input, duration_days, supplier_url — per-plan upstream listing, owner ruling — and price_confirmed_at) | `effective_price` — the single source of the pricing rule; the publishing rule (`Product.publish_problems`); selectors only (search, active listings); admin with inlines (promotional pricing is set per plan, no code required); Persian search normalisation (`catalog.search.normalize_for_search`, ADR-0025), applied on save | core | any domain app |
| **cms** | `Page` (built, ADR-0030) | admin-edited legal drafts, permission-protected preview, published pages and footer links. A product's questions remain `catalog.ProductQuestion` (ADR-0028) | core | any domain app |
| **cart** | `Cart`, `CartItem` (ADR-0018) | the **persistent cart** — resolution by user or session key, add/set/remove, the guest-to-account merge on login, the `cart_summary` selector, the stale-guest-cart purge | core, accounts, catalog | **orders**, payments, notifications, panel. Imported by payments (which resolves a cart into order lines at checkout) and panel |
| **orders** | `Order` (channel web\|bot\|legacy, tracking_token, order_number, subtotal, discount_code FK PROTECT, discount_amount, total_amount), `OrderItem` (7 statuses, price_snapshot, cost_snapshot, cancel_reason, encrypted customer_input), `DiscountCode` (ADR-0020 — lives here because `Order` FKs it PROTECT), `DiscountRedemption`, `OrderItemEvent`, `UnpaidExpirySweep`. Later steps add `actual_cost`, `paid_at`, `sla_paused_at`, `cancellation_requested_at`, `due_at` and `expires_at` on `OrderItem`, `DeliveryField` (encrypted value, is_current) and `CredentialAccessLog` | the state machine, place_order, all transitions, delivery, SLA pause/resume, reveal logging, delivery-link issue/redeem + the no-login masked view (ADR-0008), legacy backfill, discount validation/consumption/redemption records, customer order pages + public tracking page | core, accounts, catalog | cart, payments, notifications, panel. Where a transition is payment-guarded (revive, refunded), the **caller passes the Payment/Refund object in**; orders validates its attributes, never imports payments |
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
Pure, no DB, no exceptions. `confirmed_at + delivery_hours`, snapped **forward** to the next support window if outside one, hard-capped at `confirmed_at + 72h` (ADR-0009, amended 2026-10-18; the cap applies after snapping). The frozen-clock tests are specified for its implementation (D7). Callers pass the schedule from its one home (`data-model.md` §2 `core.SiteSetting`); today's support hours live in `apps/core/support.py`.

```python
def emit(name: str, payload: dict[str, Any]) -> None: ...
def register(name: str, handler: Callable[[dict[str, Any]], None]) -> None: ...
```
`emit` schedules each registered handler via `transaction.on_commit` (D18). No framework, no Django signals. `notifications` registers handlers in `AppConfig.ready()`; handlers create outbox rows and kick the sender task.

`SiteSetting.load()` — the proposed cached singleton for the two payment clocks, and for the schedule if S7 moves it there — is intent until its model is built.

Built (`apps/core/`; the code is the reference): `email.send_templated_email(name, *, to, context) -> int` renders and sends one multipart message from three template files, so no Persian string lives in Python (ADR-0023); `ratelimit` is the fixed-window limiter over the cache, and every auth endpoint is limited per identity **and** per IP, the panel's sign-in included; `formatting` and the `fa` template filters are the Persian digit, toman and Jalali display helpers; `support.details()` is the one definition of where a customer reaches a person (the Telegram handle and its link, the hours, the support address), which the `context.shell` processor hands to every template rendered with a request and `email` to every message, both as `support`; `richtext.render_markdown` is the one path from operator-written text to a page (ADR-0027), and the only code besides the zero-price markup in `fa.py` allowed to mark a string safe; `errors` holds the 500 view, which renders without context processors, and the CSRF failure view; `middleware` holds the Persian 503 for a cache outage, `RejectNul`, and `PersianUnlessChosen`, which keeps the panel's language choice inside the panel. `admin_site` is the panel's admin site: Unfold's, installed in its place, with the Ctrl+K palette naming a found item's app as the panel does. Persian search normalisation is not in core: it is `catalog.search.normalize_for_search` (ADR-0025).

### accounts

Built — `apps/accounts/services.py` owns password and code authentication: `authenticate_password`, `login_with_password`, `request_login_code` and `complete_code_login`. The storefront and panel both verify and upgrade password hashes under an account lock; password-form validation never upgrades a hash. Password attempts retain exactly one hash check and share per-address/IP limits with panel authentication through `password_attempt_refusal`. Code sign-in rotates the session and merges the guest cart; safe return URLs remain in the views. A passworded account receives a sanitized sign-in alert; failure cannot undo sign-in.

`request_password_recovery` sends a fresh, purpose-separated ten-minute code to the stored account email. Its keyed binding includes the requesting session and current password hash. `complete_password_recovery` validates the new password before consuming the code, locks the stored session then account, and rejects inactive or invalidated sessions. `change_password` uses the same locks and password replacement path, rechecking the current password under lock. A successful change rotates the current session and invalidates every other session. The sanitized change notice runs after commit; notification failure cannot undo the password change. Recovery shares the existing request/guess limits. No reset URL or code is placed in a URL.

Note: `place_order` does **not** require `is_verified` (D6 — no hard block); the checkout view uses `request_login_code`/`complete_code_login` inline for anonymous users before calling `payments.checkout`, which is where verification happens in practice. Catalog, product pages, search **and the cart** are fully anonymous — identity is collected inside checkout by this inline OTP, which doubles as signup and email verification; profile details come later from the dashboard. No sign-in wall in front of the shop (ADR-0018). The guest cart survives the OTP sign-in because `cart.services.merge_carts` runs on the login signal — see the cart app below.

Telegram linking (S8) is intent only here: a customer links their account to one Telegram account, either from the site (a short-lived, single-use token opened in the bot) or from the bot (email, then an emailed code, creating the account if there is none). The unique `User.telegram_id` column is the backstop for one-to-one in both directions, and the bot reaches these services in-process through the notifications webhook view — no API layer (ADR-0004). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§2 accounts). It is re-specified when S8 is planned.

### catalog

Built — `apps/catalog/pricing.py` (`effective_price`), the `Plan` checks in `apps/catalog/models.py` and their boundary tests in `apps/catalog/tests/test_models.py` are the reference. What the code cannot say is that the unbuilt apps are bound by it too: every cart line, the checkout summary and `OrderItem.price_snapshot` are priced through `effective_price`, and a price computed anywhere without it in the call stack is the defect (ADR-0021). The countdown's price after a discount ends is `effective_price` at the discount's end, through the `price_when_promo_ends` filter in `templatetags/prices.py`.

`promises.py` is the one source of what the shell promises on every page: each promise carries the condition it must meet for every public product, `promises.context` puts them in every template, and `tests/test_promises.py` checks every public product against them. A promise about payment, refunds or warranty is added there when the owner decides it, and one about where login details are shown waits for S9, which builds that.

Publishing is one rule, `Product.publish_problems()` (ADR-0028), and every way a product goes live asks it: the product form (its own fields in `Product.clean()`; each row's placeholders in that row's own `clean()`, which every form saving a row runs, the plan's own form included; what only the rows together can say, an available plan and no more than one «پیش از خرید», in the admin's `ProductRowsFormSet`, because Django validates the form before its inlines; and, through `ProductForm`, the stored rows of any inline the operator may not change), the list's «انتشار» bulk action, and the readiness list at the top of a draft's form. A new way to publish — a panel view at S7, an import — calls it too, or it is the hole in the rule.

The visitor-facing views read products through `Product.objects.public()`, with one exception: `views.product` also serves a draft to staff who hold `catalog.view_product`, as a preview under a «پیش‌نویس» band, `noindex`, `Cache-Control: private, no-store`. Anyone else gets the same 404 as for a slug that does not exist, and an unavailable product is a 404 for staff too. `tests/test_drafts.py` walks every road a product takes onto a page with a draft on it.

`management/commands/seed_catalog.py` fills a development database with the mock catalogue and, unless run with `--reset`, only adds: a category or product whose slug exists is left as it is, plans and specs included, because an order line holds its plan by PROTECT from S4a. `--reset` deletes the seeded products and their child rows first, including any operator edits to them.

### cart

Implemented in `apps.cart`, independent of `orders`. The request identifies the owner: account when signed in, current session key otherwise. Prices and public product facts are read fresh from the catalog; no price or customer input is stored in cart rows.

| Service | Behaviour |
|---|---|
| `get_cart(request)` | Returns the current owner's cart or None, without creating rows. |
| `add_to_cart(request, *, plan_id, quantity=1)` | Adds or increments a line; rejects unavailable plans and quantities outside the current Plan.max_quantity, with no partial mutation. |
| `set_quantity(request, *, plan_id, quantity)` | Sets an absolute quantity; zero removes the line. Same availability and quantity validation. |
| `remove_from_cart(request, *, plan_id)` | Removes only the current owner's line; repeating is harmless. |
| `cart_count(request)` | Total units for the header; no cart lookup for a guest without a session. |
| `cart_summary(cart)` | Returns fresh public facts, prices and quantities. Totals and the discount saving exclude unavailable or invalid-quantity selections; either blocks readiness to proceed. Hidden products expose no facts. |
| `merge_carts(*, user, guest_cart_id)` | Atomic login merge; claims or combines, preserving added_at, capping imported quantities to current limits. Returns whether a quantity was reduced. Guest mutations lock the current DB Session before their Cart and reject rotated/expired sessions. Account edits and merges lock the account row before their cart. |

Finite-stock mutations also lock Plan before checking its current count. Merges acquire all affected plans in id order. Carts hold no stock and have no purchase deadline; unavailable selections remain until explicitly removed, with a restock-support action. `purchase_options` supplies stock-aware product controls; GET `/cart/availability/` refreshes at most fifty public plan ids for the current owner and is not cached. POST forms retain CSRF and HTML fallback; JSON addition updates the header and one toast without navigation. Checkout must atomically recheck and claim stock; that order/payment lifecycle remains pending.
| `purge_guest_carts()` | Deletes inactive guest carts older than 30 days under locks; returns the number of carts deleted. |

`core.sessions.login_session` locks and refreshes the original DB Session before public or admin login rotates it; rotation and login receivers share one transaction. Guest mutations persist their reference and expiry before releasing the Session lock, so a login request that cached older data still sees the committed cart. `user_logged_in` then merges, clears the reference and restores the prior session expiry. Failed merges roll back the original session as well. An already-started guest request using a rotated session is rejected without creating another cart. No auth app imports cart. Cart GET and CSRF-protected POST views render ordinary HTML; the plus/minus controls submit an absolute quantity adjustment. Empty, unavailable, invalid-quantity and error states use the same page. No checkout, discount application, clear-cart orchestrator or job scheduler exists yet.

### orders

**Built — 2026-10-29.** `apps/orders/services.py` owns `place_order`, `quote_order`, `replay_checkout`, `cancel_unpaid_order` and `expire_unpaid_orders`. `OrderLine(plan, quantity, customer_input=None)` contains resolved plans and non-secret customer input; orders never receives a cart or session.

`place_order(*, user, lines, phone="", channel="web", discount_code="", checkout_id=None, submission_digest="", expected_terms=None) -> Order` re-reads the active account, public products, plans, stock, quantities and prices. It captures one pricing time after locking and uses `effective_price` for every unit. It stores immutable product/category, sale/cost and discount facts, encrypted input and one event per unit. Whole-toman amounts must fit storage; a zero-total purchase is refused before resources are consumed. Optional phone input uses the account's Iranian-number normalizer and only fills an empty profile.

Lock order is account (`FOR NO KEY UPDATE`) → cart in the downstream coordinator → existing orders → products → plans → discount codes → items. Each group is sorted by primary key. Creation locks the union of resources from overdue orders and proposed lines before releasing old claims. Joined rows are excluded from plan/order lock scope. One live unpaid order per customer is enforced under the account lock; `UnpaidOrderExists` carries that owner's existing order. Overdue pending orders are released before deciding whether another purchase is allowed.

Successful checkout replay checks the stored owner and constant-time digest before inspecting a new cart or an expired quote. A different digest or another customer receives a validation error. The unique checkout identifier also protects simultaneous cross-customer claims. The downstream checkout service supplies the keyed digest and signed quote; `expected_terms` requires another explicit review when fresh purchase facts differ.

`quote_order(*, user, lines, discount_code="") -> Purchase` previews the same computation without consuming resources. Codes are uppercase ASCII letters/digits, four to eight characters. Activation/window, global capacity, unreleased per-user history, pre-code minimum and product eligibility are checked under the code lock during creation. Promotional prices come first; percentages use `ROUND_HALF_UP`; fixed and percentage results are capped at the eligible subtotal. Invalid and empty-eligibility codes produce specific Persian validation errors. Code changes never rewrite purchase snapshots.

`cancel_unpaid_order(*, order_id, actor, note="") -> Order` checks stored ownership or operator permission, locks the whole unpaid order and releases its resources atomically with every item event. A stored operator cancelling another customer's order supplies a note. Repetition creates no extra stock or history. `cancel_unpaid_item` remains a compatibility delegate to this whole-order operation. Paid items are refused.

Finite stock is decremented at creation; `OrderItem.stock_debited` records the claim's origin. Cancellation returns only debited units, and only while the plan still has finite stock. Unlimited-origin claims never manufacture stock. Discount redemptions retain their amount/history and gain `released_at`; release decrements the code's active counter once. Standalone and inline stock forms use a signed original quantity: unrelated edits preserve the latest stock; conflicting intentional edits require review. Product/discount editors acquire product locks before plans/codes, including before M2M foreign-key checks. Single and bulk product deletion lock products/plans before protected-object collection; an order created first prevents deletion.

`expire_unpaid_orders(*, limit=100, user_id=None) -> int` processes database deadlines in bounded, separate transactions through the same cancellation implementation. Repeated/missed sweeps are harmless; a validation failure is logged with order id and exception type only. Unsuccessful attempts receive a timestamp and rotate behind unattempted rows, so a full failing batch cannot starve later orders; failed rows get another turn. Database failures propagate and do not update sweep freshness. No paid transitions, payment record or outbound order notification is introduced.

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

**orders selectors** (`selectors.py`) are built for customer pages:

| Selector | Returns |
|---|---|
| `customer_orders(user)` | Owner-filtered orders with prefetched frozen item facts; the view paginates 20 orders and groups units within their frozen category. Each order total appears once. |
| `current_order(user)` / `current_unpaid_order(user)` | Latest nonterminal customer order for the account summary; earliest unexpired unpaid order for checkout's existing-order check. |
| `customer_summary(order)` / `customer_timeline(order)` | Frozen purchase groups, stage status and safe status/time history. Encrypted input, supplier costs and operator notes are not read. A terminal aggregate appears only when all units are terminal; mixed terminal outcomes use «پایان‌یافته» with each line's own status. |
| `order_for_tracking(token)` | Explicit projection of frozen product name, safe status and creation/event times; no order model, plan title, quantity, price, account/contact/input, private note or invoice access reaches the public template. |

Customer list/detail/invoice queries constrain ownership in SQL, including for staff using the customer area. Whole-order cancellation requires a CSRF-protected confirmed POST and calls the shared cancellation service. GET never expires or cancels orders. Overdue pending rows show an ended deadline while the worker catches up.

Public `/track/<token>/` uses independent IP and keyed-token rate limits. Invalid tokens have one body; every response, including exceptions/method errors, receives no-store/noindex/no-referrer headers. Tracking error bodies contain no support/contact or request details. Application access/exception logging and error-report URL scrubbing redact raw and encoded tracking links. A deployment proxy must also redact private-link paths in its own access logs.

The owner-only invoice uses a standalone HTML document with frozen names, plans, unit quantities/prices and order totals. No account/contact, customer input, tracking token, supplier cost or support identifier appears in its HTML or export payload. Native browser Canvas loads the existing local Persian font before producing PNG, wraps mixed-language text and bounds canvas pixel memory; failure leaves the HTML readable. Unpaid/cancelled state and a payment-evidence disclaimer remain visible. There is no server browser, rendering dependency or payment evidence route.

The operator-side selectors are intent only here: the operator queue and its stat bar, the dashboard aggregates and the overdue list (S7), stale awaiting-input items for the input-timeout ladder (S8), and the renewal scan `items_expiring` (S10). They are read-only queries in `selectors.py` that the panel and the beat tasks render from (ADR-0002). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§2 orders selectors). It is re-specified when each selector's step (S7–S10) is planned.

### payments

Built — `apps/payments/services.py` coordinates cart and orders without Payment records or a gateway call. `quote_checkout(*, user, discount_code="") -> CheckoutQuote` returns the current `Purchase` and a signed 15-minute quote. The compact signed envelope binds the account, cart row/revision/item identities, purchase-terms digest, code and input declarations. Customer input and contact values never enter the quote. Read-only preview does not consume resources.

`checkout(*, user, quote, checkout_id, customer_inputs, phone="", discount_code=None) -> Order` locks the active account before resolving its cart. It normalizes the validated submission, computes a keyed SHA-256 digest and checks successful replay before quote age or current-cart checks. Signing-key fallbacks support previously accepted submissions. Another account or changed content cannot reuse the identifier. A changed cart, price, code or purchase term requires a new explicit submission. Displayed browser amounts have no authority.

The coordinator locks account then cart, calls shared `place_order`, and consumes cart items in the same transaction as stock, redemption and audit writes. A failure preserves the whole cart and rolls back the purchase. `prepare_checkout(*, user)` is an explicit review POST that expires only this account's overdue unpaid claims before preparing a new quote. The GET remains read-only. Existing live unpaid orders return their owner-only detail link and preserve the cart.

The checkout page reuses account code endpoints inline, with their HTML forms as the no-JavaScript fallback. Sign-in reloads checkout after session rotation/guest merge, obtaining a new CSRF token and quote. Checkout has no activation acknowledgement or consent requirement. New orders leave historical consent columns blank. Quote envelope version 2 rejects earlier review tokens; customers must review again. Invalid discounts block purchase until the customer reviews a valid code or explicitly removes it.

The rest of `payments` is intent only here: the gateway (S5), with the payment tables and the operator-only manual fallback pulled forward to S6b. It runs the payment machine `created → initiated → verified | failed | abandoned`, plus `created → verified` for the manual fallback, under the rules ADR-0019 already fixes: the server-to-server verify is the only source of truth and nothing on the browser redirect is trusted; the verified amount is compared with the order total and a mismatch is rejected; `confirm_payment` is the one confirmation path and the only emitter of `payment.verified`, made exactly-once by a `select_for_update` lock and a re-read of the status under it (the UNIQUE `idempotency_key` guards the initiate call, not confirmation); a mandatory inquiry sweep recovers lost callbacks and is the only thing that abandons a payment; a failed payment leaves the order waiting with a retry link, and the separate fixed-deadline unpaid sweep remains authoritative; payment initiation/verification must coordinate with expiry before those routes are enabled; the manual fallback confirms through the same path; refunds prefer the gateway's refund API, the refund message names no card, and no item reaches REFUNDED without an executed `Refund` row (ADR-0003). No PaymentProvider interface until a second gateway exists (ADR-0013); rial exists only inside the gateway boundary (ADR-0005). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§2 payments, with its selectors and the rial and no-interface paragraphs). The payment functions this file names elsewhere — `start_gateway_payment`, `handle_callback`, `confirm_payment`, `cancel_with_refund`, `revive_order` — are specified then. It is re-specified when S5 is planned.

### notifications

Intent only here (S6, with Telegram in S8). It is the outbox of ADR-0010: `notify()` writes one row per occurrence, recipient and channel under a globally unique `dedupe_key` of the form `{occurrence}:{recipient}:{channel}` (recipient `op` for the operator; the canonical occurrence prefixes are state-machine §3); handlers are registered against `core.events` in `AppConfig.ready()` and run on commit; a worker sends with `select_for_update(skip_locked=True)` on a fixed backoff ladder; channels are exactly `email` and `telegram`, and operator alerts go out on both. The operator's new-order alert fires on `payment.verified`, never on order creation (ADR-0019), and no message carries a credential value or `customer_input` — a delivery notice carries the single-use link instead (ADR-0008). Email sending is already built: the notifications app will call `core.email.send_templated_email` (ADR-0023), not the draft's `send_email(to, subject, body)`. The bot is one webhook view calling services in-process (ADR-0004). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (§2 notifications). It is re-specified when S6 is planned.

---

## 3. Huey scheduled work

One Redis-backed Huey consumer runs with one worker, explicit asynchronous mode in development and production, and a queue name derived from database host/port/name. Credentials do not enter queue names. Tests retain queued execution using MemoryStorage; no Redis daemon is required in CI. All tasks use Django's database-aware decorators.

| Task | UTC cadence | Behavior |
|---|---|---|
| `orders.tasks.sweep_unpaid_orders` | every minute | releases up to 100 due unpaid orders; records successful sweep time, processed count and overdue backlog in `UnpaidExpirySweep` |
| `cart.tasks.clean_guest_carts` | daily 03:00 | existing guest-only 30-day purge; account carts remain |
| `core.tasks.clean_expired_sessions` | daily 03:10 | Django's expired database-session cleanup |

The order list shows live overdue backlog and warns when the last successful sweep is over three minutes old. A stopped worker delays release; checkout still locks and validates available stock and database deadlines. Restart catches up without extending deadlines or deleting orders. Run/stop instructions are in [development.md](development.md#background-worker).

Gateway inquiry, outbox/heartbeat, input-timeout, renewal and credential-retention tasks remain future consumers at their owning steps. Gateway initiation/verification must coordinate with expiry under the shared lock order before enabling a payment route.

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
| Clock | `time_machine` for `compute_due_at`, SLA pause math, the stale-payment threshold, discount validity windows, renewal scans |
| Fernet key | fixed test key in test settings; one test decrypts a stored `DeliveryField` and round-trips key rotation via MultiFernet |
| Huey | asynchronous queue with in-memory storage; task tests explicitly dequeue and execute |

Everything else (Postgres constraints, the state machine, ownership, `select_for_update` on the discount row) runs against the real test DB — row locking **is** a test subject, so no sqlite; the payment row's lock and the live-payment index join at S6b/S5. The concurrent-discount test needs real transactions: two threads racing one single-use code, exactly one wins.

**Fixtures:** plain pytest fixtures build the rows each test needs; unused factory-boy was removed. Future payment/notification fixtures arrive with their real services.

Must-cover suites: full transition matrix (legal + every illegal pair raises, DELIVERED→CANCELLED forbidden, both REPLACEMENT_REQUESTED→DELIVERED variants A10a/A10b), `compute_due_at` boundary table, `extend_due_at`, from S5 the payment suites ADR-0019 names (idempotent confirmation, amount mismatch, untrusted redirect parameters, lost-callback recovery, the manual fallback), `revive` recomputing `due_at` from now (a payment verified weeks ago must not yield an instantly-overdue item), `effective_price` boundary table, discount validation matrix + scoped-code eligible-subtotal arithmetic + `ROUND_HALF_UP` rounding + concurrent single-use consumption + `total_amount >= 0` clamp, the order-total invariant `subtotal − discount_amount = total_amount`, **cart merge on login** (guest cart claimed when the account has none; quantities summed and clamped when it does), cart price recomputation (promotional price started or ended, plan gone unavailable between add and checkout), **the quantity-1 cap on input-requiring plans** (the cart refuses the second unit; `place_order` refuses it again if it ever arrives), ownership on order pages + reveal, encryption round-trip, Sentry scrubber leak test (D20), end-to-end cart→checkout→gateway verify→deliver→notify flow.

---

## Concerns

1. **Revive vs. pending Refund (D1×D2):** an item can be CANCELLED with a Refund recorded but not yet executed, and revive (CANCELLED→QUEUED) is legal. Reviving while a refund is in flight double-pays the obligation. **Resolved:** the guard (eligible `cancel_reason` + no unexecuted Refund) lives in `payments.revive_order`, the sole caller of `orders.revive` — no caller-supplied booleans.
2. **`due_at` base after revive (D7×D2):** computing from the original `verified_at` on an order revived weeks later yields an already-overdue item. `orders.revive` recomputes `due_at` from now via `compute_due_at`, not from the payment; `verified_at` stays the base only for the normal first pass.
3. **A verify that succeeds at the gateway but fails to commit locally** (S5) — covered by the mandatory inquiry sweep (ADR-0019). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (Concerns #3). It is re-specified when S5 is planned.
4. **Discount code consumed, then the gateway start fails.** `payments.checkout` commits the order (and the code consumption) *before* calling the gateway, because holding a row lock across an outbound HTTP call is how a slow gateway becomes a database outage. **Resolved by accepting the outcome instead of engineering around it:** the order sits in `PENDING_PAYMENT`, fully payable, and the customer retries from the order page — the code is spent on an order that still exists and still owes money, which is correct. No compensating decrement, no orphan cleanup.
5. **Guest cart merged into an account that already has one.** Summing quantities can exceed the per-line maximum. `merge_carts` clamps, does not reject — a customer told "your cart could not be merged" has lost trust over arithmetic. The clamp is tested.
6. **The brief's `unique_together(order_item, event_type, channel)` notification dedup** (S6) — overridden by the per-occurrence `dedupe_key` (ADR-0010). Full earlier draft: `git show 027c5d1:docs/services-and-modules.md` (Concerns #6). It is re-specified when S6 is planned.
