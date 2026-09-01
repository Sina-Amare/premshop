# ADR-0005 — All stored amounts are toman, integer-valued Decimal

**Status:** Accepted · 2026-09-01 · amended 2026-09-01

**Amendment:** The **no-discount-field** ruling below is superseded by [ADR-0020](0020-discount-codes.md). The objection was that a money field with no mechanism behind it is a bug factory; `DiscountCode` is that mechanism, so `Order` now carries `subtotal`, `discount_amount` and `discount_code`, and the invariant reads `subtotal = Σ item price_snapshots`, `total_amount = subtotal − discount_amount`, `total_amount ≥ 0`. Everything else here stands unchanged: all stored amounts are toman as integer-valued `Decimal` — the new discount fields included — and rial appears only at explicitly tested boundaries. One of the two original boundaries is gone with the card-to-card instructions page ([ADR-0019](0019-payment-gateway.md)); the gateway adapter is the remaining one.

**Context:** The classic Iranian payments bug is the 10× toman/rial confusion: the UI and operator think in toman, bank apps and gateway APIs speak rial. One missed boundary conversion misprices an order or lets a one-tenth payment match.

**Decision:** Every stored amount (prices, snapshots, payments, refunds) is **toman**, `DecimalField(max_digits=12, decimal_places=0)`. Rial appears in exactly two places, both with explicit tested conversion: the card-to-card instructions page (rendered beside the toman amount, because the customer types rial into their bank app) — *that boundary is gone with the card-to-card rail ([ADR-0019](0019-payment-gateway.md)); the reasoning is kept here as history* — and, later, inside the gateway adapter, which is now the only rial boundary. `Order.total_amount = Σ item price_snapshots` is a stated invariant — there is **no discount field** (a money field with no mechanism behind it is a bug factory; coupons are explicitly not built).

**Alternatives considered:** storing rial (matches gateway/bank, but the operator enters and reasons about prices in toman daily — the error moves to the more frequent surface); float types (rounding errors; forbidden by the working agreement).

**Consequences:** Any new money field defaults to toman; any new external money boundary gets a conversion test before it ships.
