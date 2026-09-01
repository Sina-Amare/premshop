# ADR-0003 — Status lives on OrderItem, with exactly seven states

**Status:** Accepted · 2026-09-01

**Context:** An order can hold several items (quantity-N gift cards) with independent delivery, warranty, and renewal lives. A single order-level status lies the moment one item moves. The brief's original machine also carried states with no distinct side effect.

**Decision:** Status is a field on `OrderItem`, never on `Order` (order display status is derived). Exactly seven states: `PENDING_PAYMENT · QUEUED · AWAITING_INPUT · DELIVERED · REPLACEMENT_REQUESTED · CANCELLED · REFUNDED`. Transitions only through service methods, inside a transaction with `select_for_update`; illegal transitions raise; every transition writes one append-only `OrderItemEvent` row. Replacement **cycles back to DELIVERED** with a new credential generation — repeatable, because account bans are routine. The full matrix (12 allowed, 37 forbidden, each with a reason) is the contract in [../state-machine.md](../state-machine.md).

**Alternatives considered:** status on Order (breaks multi-item truth; migrating item-ward later is the risky direction); `IN_PROGRESS` (no side effect with one operator — not even a queue tab); `REPLACED` as terminal (contradicts routine re-bans; silently breaks review eligibility and expiry reminders); a separate `FAILED` state (indistinguishable from CANCELLED — replaced by `cancel_reason`).

**Consequences:** Every state maps to a queue tab, a notification, or a terminal fact. Reintroduce `IN_PROGRESS` only if a second operator ever needs work-claiming.
