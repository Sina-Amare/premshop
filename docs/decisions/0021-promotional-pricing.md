# ADR-0021 — Promotional pricing lives on the plan, behind one function

**Status:** Accepted · 2026-09-01

**Context:** The operator needs to run a sale without asking a customer to type anything: a price that is lower for a stated window, shown on the card and charged at checkout, with the original struck through beside it. Discount codes ([ADR-0020](0020-discount-codes.md)) are the other half of the promotion toolkit, but they require the customer to know a code, which makes them useless for the ordinary "this product is on sale this week" case. The danger with a second price is not the schema — it is that "which price is current?" gets answered separately in the catalog card, the product page, the cart line, the checkout summary and the order snapshot, and the day one of those five disagrees, the shop shows one number and charges another. That is a trust failure of the exact kind this shop cannot afford.

**Decision:**

**`Plan` gains three fields:** `promo_price` (Money, nullable) · `promo_starts_at` (nullable) · `promo_ends_at` (nullable). `sale_price` stays what it is — the list price, never rewritten by a promotion, because the struck-through original has to come from somewhere and a promotion has to end.

**One function decides the price:**

```
effective_price(plan, at=now) -> Money
```

It returns `promo_price` when `promo_price` is set **and** `at` falls inside the window; otherwise `sale_price`. An absent bound means open-ended: no `promo_starts_at` means the promotion is already running, no `promo_ends_at` means it runs until the operator ends it.

**This function is the only place the rule lives, and every price that is shown or charged goes through it** — catalog cards, product pages, cart lines, the checkout summary, the eligible-subtotal computation of ADR-0020, and `OrderItem.price_snapshot` at order time. Not "should go through it": any code path that reads `plan.sale_price` to display or charge a price is a bug. The single function is the whole decision; the three columns are just where it reads from. The `at` parameter exists so a test can assert the boundary behaviour at a chosen instant instead of sleeping, and so a snapshot taken during order creation uses the same instant as the summary the customer just agreed to.

**Constraints, enforced by the database:**

- `promo_price` is null **or** greater than zero **and** less than `sale_price`. A "promotion" that raises the price is a data-entry error, and this is where it stops.
- `promo_starts_at` is null **or** earlier than `promo_ends_at`. An inverted window silently disables a promotion the operator believes is running.

**Display.** When a promotion is active, the original price is shown struck through beside the promotional one. The component is already defined in `../design-language.md` — the struck original is de-emphasised, the live price carries the weight, and no amber is used: amber means time pressure and nothing else ([ADR-0016](0016-brand-and-type.md)). A countdown on a promotion end date is not built and is not implied by this ADR.

**Management is admin-only.** The operator sets `promo_price` and the window on the plan. No campaign builder, no scheduling UI beyond the two datetime fields Django's admin already renders.

**Alternatives considered:**

- **A separate `Promotion` model with campaigns** (name, list of plans, percentage or absolute, priority, stacking rules). It buys campaign-level reporting and one promotion spanning many plans — neither of which the operator has asked for, and both of which cost a resolution step ("which of the three matching promotions wins?") that does not exist when the price lives on the plan. It also reintroduces exactly the multi-source-of-truth risk this ADR is built to prevent. *Flips when* the operator is running promotions across enough plans that setting them one at a time is the bottleneck, or when "what did that campaign earn?" becomes a real question — at which point the promotion moves off the plan and `effective_price` gains a lookup, and every caller is already routed through it.
- **A percentage field instead of an absolute price** (`promo_percent`, applied to `sale_price`). Rejected: the operator thinks in the number the customer will see, and a percentage makes the displayed price a computed value that can land on 187,431 toman. An absolute price is what gets decided, what gets checked against cost, and what the constraint `promo_price < sale_price` can actually validate. The percentage is trivially recovered for display if it is ever wanted.
- **Rewriting `sale_price` and keeping the old value elsewhere** — the "elsewhere" is a second price field with worse ergonomics, and it makes ending a promotion a destructive edit that can be forgotten. Rejected.
- **A boolean `is_on_sale` flag plus the window** — redundant with `promo_price IS NOT NULL` and the window, and it introduces a third state (flag on, price null) that means nothing.

**Consequences:** Two ways a price moves — a promotion and a code — and they compose in exactly one stated order: `effective_price` first, then ADR-0020's discount on top of the result. Every money test that asserts an order total now has a promotion variant, because a promotion active at order time and expired at delivery time must change nothing: `price_snapshot` is taken once and is not recomputed. Any new surface that shows a price is reviewed for one thing — does it call `effective_price`?
