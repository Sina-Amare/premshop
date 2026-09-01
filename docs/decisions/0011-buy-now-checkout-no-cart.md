# ADR-0011 — Buy-now checkout; no cart; item-level status stands regardless

**Status:** Accepted · 2026-09-01

**Context:** At under one order/day the typical order is one plan; a cart drags multi-product checkout UI and edge cases with no demonstrated demand. Cutting it weakens the *original* argument for per-item status, which had to be re-examined rather than passed over.

**Decision:** Checkout is buy-now: product → plan → one checkout page; a **quantity** selector creates N OrderItems in one Order (covers multi-gift-card in one payment). No cart model, no cart page. Item-level status stands on grounds independent of any cart: quantity-N orders are genuinely multi-item (N credential sets, N warranties, possibly staggered delivery); replacement generations and per-item expiry are item-lifecycle facts; and deriving order display from items is a selector while the reverse migration is the bet the working agreement forbids. Display rule for n>1: the order header shows the earliest-stage item (PENDING_PAYMENT < QUEUED < AWAITING_INPUT < REPLACEMENT_REQUESTED < DELIVERED; terminal label only when all items are terminal).

**Alternatives considered:** session cart (cheap, but still buys UI surface for an unproven case); DB cart (out of scope wholesale).

**Consequences:** The schema stays fully multi-item, so a future cart is a view/template change, not a migration. Add one only when a customer actually asks to combine different products in one payment.
