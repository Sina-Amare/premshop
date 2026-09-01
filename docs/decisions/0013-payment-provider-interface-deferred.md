# ADR-0013 — PaymentProvider interface deferred until a second implementation exists

**Status:** Accepted · 2026-09-01 · **reaffirmed** 2026-09-01 after [ADR-0019](0019-payment-gateway.md)

**Reaffirmed:** The rail changed and the verdict did not. When this was written, the argument was that card-to-card and a gateway share no call shape, so an interface drawn from card-to-card would be wrong when Zibal arrived. Zibal arrived — and card-to-card left with it, so there is *still exactly one payment implementation*. An interface would still be an abstraction with one caller, now shaped by guesses about a second Iranian provider nobody has signed up with. The operator's manual fallback does not count as a second implementation: it is an admin action that writes a confirmed `Payment` directly, not a provider with an init/verify/inquiry/refund surface. Extract the interface if and only if a genuine second gateway is ever added; `Payment.method` / `gateway_name` still keep the door open at zero cost.

**Context:** The brief asked for an adapter pattern from day one. But card-to-card (allocate an amount, wait for a human match) and a gateway redirect (init → redirect → server verify) share almost no call shape; an interface abstracted from the single card-to-card implementation would be the wrong interface when Zibal arrives.

**Decision:** **There is one payment implementation and therefore no provider interface — not now, and not at a scheduled later phase.** `payments.services` holds init, callback, verify, inquiry and refund as plain functions against the one gateway. The interface is extracted **if and only if** a genuine second gateway is ever added; no phase, milestone or date triggers it. `Payment.method` / `gateway_name` keep the door open at zero cost, and that is the entire hedge.

*(The original text scheduled the extraction for phase 1.5 "when Zibal defines its shape". Zibal became the only implementation rather than the second one, so that clause was the wrong verdict written from the wrong assumption and is withdrawn, not deferred.)*

**Alternatives considered:** interface-first (the classic one-implementation abstraction; guaranteed rework).

**Consequences:** Honors the brief's intent at the moment it has two callers instead of pretending to honor it with one.
