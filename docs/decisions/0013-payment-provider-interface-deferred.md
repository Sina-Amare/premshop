# ADR-0013 — PaymentProvider interface deferred until a second implementation exists

**Status:** Accepted · 2026-09-01

**Context:** The brief asked for an adapter pattern from day one. But card-to-card (allocate an amount, wait for a human match) and a gateway redirect (init → redirect → server verify) share almost no call shape; an interface abstracted from the single card-to-card implementation would be the wrong interface when Zibal arrives.

**Decision:** Phase 1 ships card-to-card as plain `payments.services` functions. The provider interface is extracted in phase 1.5, when Zibal — a second, real implementation — defines its shape. `Payment.method` / `gateway_name` already keep the door open at zero cost.

**Alternatives considered:** interface-first (the classic one-implementation abstraction; guaranteed rework).

**Consequences:** Honors the brief's intent at the moment it has two callers instead of pretending to honor it with one.
