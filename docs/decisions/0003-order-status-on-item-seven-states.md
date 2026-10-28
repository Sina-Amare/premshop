# ADR-0003 — Status lives on OrderItem, with exactly seven states

**Status:** Accepted · 2026-09-01

**Context:** An order can hold several items (quantity-N gift cards) with independent delivery, warranty, and renewal lives. A single order-level status lies the moment one item moves. The brief's original machine also carried states with no distinct side effect.

**Decision:** Status is a field on `OrderItem`, never on `Order` (order display status is derived). Exactly seven states: `PENDING_PAYMENT · QUEUED · AWAITING_INPUT · DELIVERED · REPLACEMENT_REQUESTED · CANCELLED · REFUNDED`. Transitions only through service methods, inside a transaction with `select_for_update`; illegal transitions raise; every transition writes one append-only `OrderItemEvent` row. Replacement **cycles back to DELIVERED** with a new credential generation — repeatable, because account bans are routine. The full matrix (12 allowed, 37 forbidden, each with a reason) is the contract in [../state-machine.md](../state-machine.md).

**Alternatives considered:** status on Order (breaks multi-item truth; migrating item-ward later is the risky direction); `IN_PROGRESS` (no side effect with one operator — not even a queue tab); `REPLACED` as terminal (contradicts routine re-bans; silently breaks review eligibility and expiry reminders); a separate `FAILED` state (indistinguishable from CANCELLED — replaced by `cancel_reason`).

**Consequences:** Every state maps to a queue tab, a notification, or a terminal fact. Reintroduce `IN_PROGRESS` only if a second operator ever needs work-claiming.

**Amendment — 2026-10-26.** Unpaid order creation and cancellation are implemented behind service functions, with an event per item in the same transaction. Repeating the same cancellation returns the cancelled item without another event. Actors are checked from the stored active account; customer cancellation checks ownership, and operator cancellation requires a note. The seven states remain, but paid/refund/delivery operations are not exposed until their real dependencies exist. Order-to-item and item-to-event foreign keys use PROTECT even for unpaid records; a PostgreSQL trigger refuses event updates and deletes. An order with recorded items cannot be deleted.

**Amendment — 2026-10-29.** Unpaid cancellation covers the whole order, including the compatibility item entry. It locks Order before products/plans/codes/items, restores actual finite claims and active discount capacity once, and records all cancellation events atomically. Expiry uses the same implementation at the fixed 24-hour boundary. The earlier item-only lock scope is superseded for these operations; item status remains the source of truth. See [ADR-0029](0029-unpaid-claims-and-scheduled-release.md).
