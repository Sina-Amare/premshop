# ADR-0006 — Card-to-card design: enforced uniqueness, pending-only expiry, revive

**Status:** Accepted · 2026-09-01

**Context:** Card-to-card with unique-amount matching is the only rail at launch. Transfers are instant and irreversible; customers pay late, pay rounded amounts, pay twice; the operator sleeps. The happy path alone guarantees unaccounted money within the first month.

**Decision:**
- Payment has its own small machine (`pending → submitted → confirmed | rejected`; `pending → expired`; `expired → confirmed` via operator manual match). Operator confirm is **idempotent**.
- Amount uniqueness is a **database guarantee**: partial unique index on `unique_amount` for live payments, allocation by retry-on-IntegrityError, plus a 72h no-reuse quarantine after expiry so a late transfer can never match the wrong order.
- The expiry sweep touches **`pending` only** — a `submitted` payment (customer claimed paid) never auto-expires; it waits for the operator's confirm/reject.
- Payment window **60 minutes** (bank-app friction, رمز پویا delays, nightly maintenance), configurable.
- Late money revives the order: `payments.revive_order` = manual match + per-item revive in one transaction, guard enforced in-service.
- `paid_amount` is recorded separately from `unique_amount` (near-miss shortfalls accepted, delta visible); a tiny `UnmatchedTransfer` ledger holds money that matches nothing; every outbound refund is a `Refund` row (see ADR-0007's sibling gate in the state machine: no item reaches REFUNDED without an executed Refund row).
- Two destination-card slots + an `active_card` selector in settings; each Payment **snapshots** the card it displayed, so flipping cards mid-crisis never desyncs open payments.

**Alternatives considered:** probabilistic uniqueness ("a small random number", per the original brief — a collision misdelivers credentials and money in one move); 30-minute window (guarantees the paid-at-23:00/expired-at-midnight failure); refunds as payment statuses (can't express partials; the ledger row is the auditable object).

**Consequences:** Reconciliation is a 5-minute day-list against the bank app. Every rule above has a named test; they are money-path tests and non-negotiable.
