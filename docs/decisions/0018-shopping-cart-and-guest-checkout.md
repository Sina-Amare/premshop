# ADR-0018 — Persistent cross-device cart and fully anonymous browsing

**Status:** Accepted · 2026-09-01 · supersedes [ADR-0011](0011-buy-now-checkout-no-cart.md) · **revised 2026-09-01** (the session-only cart decided earlier the same day is superseded by the design below)

**Context:** ADR-0011 cut the cart because nobody had asked to combine products. With a per-order card-to-card transfer nobody *could* — two products meant two transfers and two operator matches, so the absence of demand measured the payment rail, not the customer. A gateway ([ADR-0019](0019-payment-gateway.md)) makes one payment for N products cost nothing extra. The first version of this ADR then put the cart in the session and refused a table. That was too weak: a cart that lives in one browser's session is gone the moment the customer opens the shop on their phone, and every platform this shop is judged against — Digikala, the app stores, any account-based shop — carries a cart across devices. A customer who fills a cart on a laptop and finds it empty on a phone concludes the site is broken, which is the exact doubt this shop cannot afford. Separately, a shop that demands a login before it will show a price loses the browser who is still deciding whether this site is real.

**Decision:**

**Two tables, in their own `cart` app.** `Cart` and `CartItem` do not live in `orders`. The `cart` app imports `core`, `accounts` and `catalog`; it is imported by `payments` (which resolves a cart into order lines at checkout) and by `panel`. It does **not** import `orders`, and `orders` never imports it — that one-way edge is what keeps `orders` ignorant of how a cart is stored, and it is the reason `orders.place_order` can take a plain `Sequence[OrderLine]`. The tables are created by `cart/0001`, alongside the discount tables, not by `orders/0001`.

- **`Cart`:** `user` (FK → `User`, nullable, **UNIQUE** when set) · `session_key` (varchar(40), nullable, indexed) · `created_at` · `updated_at`. `CHECK`: **exactly one** of `user` and `session_key` is set. A cart is therefore either a guest cart or an account cart, never both and never neither.
- **`CartItem`:** `cart` (FK → `Cart`, `CASCADE`) · `plan` (FK → `Plan`, `PROTECT`) · `quantity` (smallint, `CHECK BETWEEN 1 AND 10`) · `added_at`. **UNIQUE (`cart`, `plan`)** — adding a plan already in the cart raises its quantity, it does not add a second line.

**A plan that requires customer input is limited to quantity 1.** Each `OrderItem` is a separate credential with its own lifecycle, so three units of such a plan would need three separate inputs, and a checkout form collecting three account passwords for one line is a UI nobody has asked for. The cart refuses the second unit with a clear message, and checkout re-validates the same rule before the payment is initiated. The consequence is the shape the services already assume: `customer_inputs` is a mapping of plan id to a **single string**, not to a list. *Flips when* a customer genuinely needs several units of an input-requiring plan — and the answer then is per-item inputs on the checkout form, never one value silently reused across items.

**The cart stores only plan and quantity.** No price, no product name, no image URL, no discount. Every amount shown or charged is recomputed from the database through `effective_price` ([ADR-0021](0021-promotional-pricing.md)) at render, again at checkout, and only then snapshotted onto the `OrderItem`. Nothing the browser sends is ever a money input; the cart is a list of *intentions*, and the server prices them. This rule is unchanged from the session version and is the reason a persistent cart is safe to keep for weeks.

**A signed-out visitor's cart is keyed by `session_key`, and the session is configured to outlive the browser window:** `SESSION_EXPIRE_AT_BROWSER_CLOSE = False`, `SESSION_COOKIE_AGE = 30 days`. A guest who closes the tab and comes back tomorrow finds their cart.

**A signed-in customer's cart is keyed by `user`,** so it follows them to any device they sign in on. This is the whole point of the revision.

**On login the guest cart merges into the account's cart.** One rule, stated once:

- If the account has **no** cart, the guest cart is **claimed** — set `user`, clear `session_key`. `added_at` on every line is preserved, so the merge is invisible.
- If the account **has** a cart, quantities are **summed per plan** and clamped to the per-line maximum of 10. Plans only the guest cart had are moved over. The guest cart row is then deleted.
- The merge is the part customers actually notice going wrong, so it gets **its own test** covering all three shapes: empty account cart, overlapping plans, and a sum that exceeds the clamp.

**A daily beat task deletes guest carts untouched for 30 days** (`session_key` set and `updated_at` older than 30 days). `CartItem` rows go with them by cascade. Account carts are never swept — a cart is a stated intention and the customer's to keep.

**Checkout consumes the cart and clears it inside the order-creation transaction.** The cart rows are deleted in the same transaction that creates the `Order` and its items, so a customer never ends up with both an order and the cart that produced it. `payments.checkout(...)` resolves the cart into `OrderLine` values first and hands those to `orders.place_order` — the orders app never learns how a cart is stored.

**Checkout re-validates** availability and current price before the payment is initiated, and shows the customer the line items, subtotal, discount and total they are actually about to pay — a price that moved between adding and paying is surfaced, not silently charged.

The cart page allows quantity change and removal; the header shows an item count. Money on both follows [ADR-0016](0016-brand-and-type.md) and `../design-language.md`: Persian digits, ASCII thousands separator, toman only, the currency word lighter than the numeral, tabular figures so a totals column aligns, and subtotal / discount / total as three distinct ledger lines.

**Browsing is anonymous end to end** — catalog, product pages, plan pages, search. Identity is collected *inside* checkout by the inline email+OTP flow of [ADR-0012](0012-auth-email-otp.md), which already doubles as account creation and email verification. Profile details (name, phone beyond the checkout field, Telegram link) are completed later from the customer dashboard. There is no sign-in wall in front of the shop.

**Alternatives considered:**

- **Session cart, no table** — this was the previous decision in this same round, and it lost on one fact: session data is bound to one browser, so the cart cannot follow a customer to a second device. That is table stakes on every platform this shop is compared to, and its absence reads as a broken site rather than a missing feature. Its stated savings were real but small — no purge job, no merge code — and both turned out to be one scheduled delete and one tested function, not a background system to own forever. Its two genuinely good rules survive intact here: the cart stores no prices, and identity is collected inside checkout.
- **Cookie cart** — the same shape as the session cart with the state moved client-side, which makes it forgeable and size-capped while still being per-device. Strictly worse than the session cart it was meant to improve on.
- **Buy-now only** (keep ADR-0011) — the reason it held is gone with the rail, and an absent cart now reads to customers as an unfinished site.

**Consequences:** Two new tables, one merge function, one daily purge task, and a cart that behaves the way customers expect. Because the cart carries no prices, a price change or a promotion starting mid-cart is not a discrepancy to reconcile but simply the new price, shown before payment — and a 30-day-old cart is as safe as a 30-second-old one. The `CHECK` on `Cart` means "one cart per identity" is enforced by the database, not by careful code: a duplicate guest-to-user claim fails loudly instead of silently splitting a customer's cart in two.
