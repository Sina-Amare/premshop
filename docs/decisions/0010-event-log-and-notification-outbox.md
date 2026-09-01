# ADR-0010 — Append-only event log + notification outbox (and the accepted crash gap)

**Status:** Accepted · 2026-09-01

**Context:** Three promised features (public tracking timeline, SLA pause math, delivery metrics) need per-transition history; notifications must never roll back a delivery, never double-send, and legitimately re-send after a replacement.

**Decision:**
- `OrderItemEvent`: append-only, written **inside** every transition transaction. It is the timeline, the audit trail, the metrics source, and it is never purged.
- Events are dispatched by a thin in-process `events.emit()` registered via `transaction.on_commit` — no Django signals, no pub/sub framework. Its only job is creating `Notification` outbox rows.
- Outbox: per-(occurrence, recipient, channel) rows with a globally-unique `dedupe_key` (`{occurrence}:{recipient}:{channel}`); `user = NULL` means the operator; channels are exactly `email` and `telegram` (IN_APP cut — the order pages are the in-app surface; SMS deferred, phone field kept). Sender uses `select_for_update(skip_locked=True)`; fixed backoff ladder (now, 1m, 5m, 15m, 1h, 6h → failed); non-retryable errors fast-fail. Operator order/payment alerts go out on **both** channels; a beat-driven external dead-man heartbeat alarms from outside the app when the pipeline itself dies.
- **Accepted trade-off:** enqueueing on `on_commit` means a process crash in the commit-to-enqueue gap silently drops that occurrence's notifications. Accepted at this volume — the overdue digest, the operator queue, and the heartbeat are the backstops. Do not "fix" this ordering without reopening this ADR.

**Alternatives considered:** `unique_together(order_item, event_type, channel)` dedup (NULL-hostile for order-level events, and blocks the legitimate second "delivered" after a replacement); classic in-transaction outbox (stronger guarantee, more machinery than this volume justifies).

**Consequences:** A notification failure can never affect order state. Re-sends are modeled by new occurrences, never by relaxing uniqueness.
