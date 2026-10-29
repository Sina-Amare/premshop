# ADR-0009 — SLA clock: wall-clock + snap-forward, pause arithmetic, and the input-timeout ladder

**Status:** Accepted (ladder revised at owner review) · 2026-09-01

**Context:** The public promise («حداکثر ۴۸ ساعت») is read as wall-clock by customers; a full business-hours engine would be the subtlest logic in the codebase in service of nothing the customer sees. Separately, an item waiting on customer input with a stopped clock must not hold money forever.

**Decision:**
- `due_at = payment_confirmed_at + product.delivery_hours` (wall-clock), snapped **forward** to the next support window if it lands outside one, hard-capped at `+48h`. One pure function, exhaustively tested.
- Pause: `sla_paused_at` set on entering AWAITING_INPUT; on resume `due_at += now − sla_paused_at`. The cap bounds *the shop's* promise — customer-caused delay legitimately pushes `due_at` past it (owner-confirmed). Holiday "pause SLA" reuses exactly this mechanic, per-item.
- **Input-timeout ladder** (owner-required defined end): 48h re-reminder → 7-day operator flag → 14-day automated final warning naming the deadline → **21-day system auto-cancel** (`input_timeout`) creating an unexecuted Refund row; the destination is collected from the customer afterwards. Holiday does not pause the ladder. The operator can always cancel earlier. This policy is stated on the refund page.

**Alternatives considered:** full business-hours `due_at` accounting (internally green while publicly breached — the two clocks disagree exactly when it matters); human-only timeout with no end (indefinitely held money is the worse Enamad complaint).

**Consequences:** `due_at` has exactly three writers: the compute function, pause/resume arithmetic, and the explicit `extend_due_at` supply-delay service. Anything else touching it is a bug.

**Amendment — 2026-10-18 (owner's ruling).** The delivery cap moves from **48 to 72 hours**. The decision above is kept as it was made; what changes is the number, in three places that have to move together or the shop starts lying: the `Product.delivery_hours` CHECK (now `product_delivery_hours_1_to_72`, catalog 0013), the FAQ answer that quotes the range to customers, and `due_at`'s hard cap when the SLA clock is built. The public promise in this ADR's own Context — «حداکثر ۴۸ ساعت» — is therefore «حداکثر ۷۲ ساعت» from this date.

Unchanged: the **48-hour re-reminder** in the input-timeout ladder. It shares a number with the old cap and nothing else — one is how long the shop may take, the other is how long a customer is left before being reminded. Whoever next edits this file should not unify them.

Also from this round, and the reason the cap could move cheaply: `Product.delivery_note` lets the operator replace the *displayed* time with a sentence («به‌علت مشکلات پرداخت کمی زمان‌بر (۴۸ تا ۷۲ ساعت)») while `delivery_hours` stays the machine-readable deadline this ADR's arithmetic needs. The note never touches `due_at`.

**Amendment — 2026-10-20.** The delivery note is now a short phrase that takes the place of «تا … ساعت», not a sentence: the panel asks for «۴۸ تا ۷۲ ساعت», and the badge, the card and the buy box print it after «تحویل», as they print the hours («تحویل ۴۸ تا ۷۲ ساعت»). A bare phrase there said nothing about what it measured. The note still never touches `due_at`.

**Amendment — 2026-10-20, evening.** The amendment above is withdrawn: the note is printed exactly as the operator wrote it, in place of «تحویل تا … ساعت», as it was before. The owner's real note is a sentence («به‌علت مشکلات پرداخت کمی زمان‌بر (۴۸ تا ۷۲ ساعت)»), and the prefix turned it into a line that read as a typo on the card, the badge and the buy box. The panel's hint now says the text is shown as written; `test_an_operator_note_replaces_the_hours_everywhere_it_is_shown` checks the owner's sentence comes through untouched.


**Amendment — 2026-10-28.** Delivery is capped at 48 hours for every product, including Netflix, replacing the previous 72-hour cap. The buy box states «در اولین فرصت، حداکثر تا ۴۸ ساعت پس از تأیید پرداخت.» rather than a calculated time of day. Product-specific delivery hours can be shorter; operator-written notes remain editable. Before catalog migration 0028, review and reduce existing delivery limits above 48 and update any delivery note that quotes the old cap. The schema migration refuses inconsistent rows and reverses to the earlier constraint without rewriting catalogue data.

**Amendment — 2026-10-29.** Before the automated input-timeout ladder exists, an operator process may enforce the 21-day limit. Record the request for missing activation input and count 21 calendar days from that request; cancel the affected item and execute its refund through the financial/state services. The shared refund policy requires prompt execution within one working day after necessary payment details arrive, preserving any legally required faster action. Demonstrate this process before affected paid orders are accepted. The refund page states the deadline without claiming automatic cancellation or automated reminders. The later automated ladder remains planned; this amendment does not implement it.
