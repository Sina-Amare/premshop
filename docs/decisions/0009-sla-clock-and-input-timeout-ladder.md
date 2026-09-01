# ADR-0009 — SLA clock: wall-clock + snap-forward, pause arithmetic, and the input-timeout ladder

**Status:** Accepted (ladder revised at owner review) · 2026-09-01

**Context:** The public promise («حداکثر ۴۸ ساعت») is read as wall-clock by customers; a full business-hours engine would be the subtlest logic in the codebase in service of nothing the customer sees. Separately, an item waiting on customer input with a stopped clock must not hold money forever.

**Decision:**
- `due_at = payment_confirmed_at + product.delivery_hours` (wall-clock), snapped **forward** to the next support window if it lands outside one, hard-capped at `+48h`. One pure function, exhaustively tested.
- Pause: `sla_paused_at` set on entering AWAITING_INPUT; on resume `due_at += now − sla_paused_at`. The cap bounds *the shop's* promise — customer-caused delay legitimately pushes `due_at` past it (owner-confirmed). Holiday "pause SLA" reuses exactly this mechanic, per-item.
- **Input-timeout ladder** (owner-required defined end): 48h re-reminder → 7-day operator flag → 14-day automated final warning naming the deadline → **21-day system auto-cancel** (`input_timeout`) creating an unexecuted Refund row; the destination is collected from the customer afterwards. Holiday does not pause the ladder. The operator can always cancel earlier. This policy is stated on the refund page.

**Alternatives considered:** full business-hours `due_at` accounting (internally green while publicly breached — the two clocks disagree exactly when it matters); human-only timeout with no end (indefinitely held money is the worse Enamad complaint).

**Consequences:** `due_at` has exactly three writers: the compute function, pause/resume arithmetic, and the explicit `extend_due_at` supply-delay service. Anything else touching it is a bug.
