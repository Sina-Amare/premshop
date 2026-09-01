# ADR-0005 — All stored amounts are toman, integer-valued Decimal

**Status:** Accepted · 2026-09-01

**Context:** The classic Iranian payments bug is the 10× toman/rial confusion: the UI and operator think in toman, bank apps and gateway APIs speak rial. One missed boundary conversion misprices an order or lets a one-tenth payment match.

**Decision:** Every stored amount (prices, snapshots, payments, refunds) is **toman**, `DecimalField(max_digits=12, decimal_places=0)`. Rial appears in exactly two places, both with explicit tested conversion: the card-to-card instructions page (rendered beside the toman amount, because the customer types rial into their bank app) and, later, inside the gateway adapter. `Order.total_amount = Σ item price_snapshots` is a stated invariant — there is **no discount field** (a money field with no mechanism behind it is a bug factory; coupons are explicitly not built).

**Alternatives considered:** storing rial (matches gateway/bank, but the operator enters and reasons about prices in toman daily — the error moves to the more frequent surface); float types (rounding errors; forbidden by the working agreement).

**Consequences:** Any new money field defaults to toman; any new external money boundary gets a conversion test before it ships.
