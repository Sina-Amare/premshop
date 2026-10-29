# ADR-0008 — Messages carry no credential values; a single-use magic link is the one sanctioned convenience

**Status:** Accepted (amended at owner review) · 2026-09-01

**Context:** Sending credentials in Telegram/email leaves permanent plaintext copies outside every at-rest control. But this customer base migrated from receiving credentials in Telegram PV, and "open the site and log in" is real friction. Email possession already equals account takeover here (email OTP login + reset exist), so the analysis centered on the Telegram channel.

**Decision:** Notifications never contain credential values, `customer_input`, or card numbers beyond last4. Delivery/replacement messages may carry a **single-use, 72-hour magic link** opening that one item's **masked** credential view without login. Binding constraints:

1. Token ≥128-bit random, stored **hashed** (a DB dump yields no live links).
2. Atomic single-use redeem under the item's row lock; expired/used/invalid → uniform 404, rate-limited.
3. Issued at delivery and redelivery (regeneration kills the old link); invalidated on warranty-refund cancel.
4. Scope: that item's masked view + its logged reveal (`via='magic_link'`) — no session, no other pages.
5. `no-store`, `Referrer-Policy: no-referrer`, token truncated from server logs and scrubbed from error reports.
6. Every such message also carries the logged-in fallback link; the claim-rejected notice carries no token.

**Alternatives considered:** strictly content-free messages (safest on paper, but the marginal risk of the constrained link is small and the migration-friction cost is real); credentials inline (rejected — permanent third-party copies).

**Consequences:** A bounded, deliberate widening on the Telegram side only — strictly safer than the PV status quo customers come from. The constraint list is part of the decision; implementing the link without any item of it reopens this ADR.

**Amendment — 2026-10-26.** The operator may manually receive a customer's activation password or direct-login details in Telegram for a product that disclosed this requirement before purchase, and only after verified payment. The website does not collect those values. This exception covers incoming customer-to-operator activation details; it does not permit software notifications or delivery messages to include credential values. Those messages keep the constraints above. A downloadable purchase invoice and an operator payment lookup are separate future features; neither an image nor possession of an order number proves payment or ownership.


**Invoice and tracking amendment — 2026-10-29.** The owner-only downloadable invoice is now built as a frozen purchase summary. It contains no credentials, customer inputs/contact, tracking tokens or support identifiers; unpaid/cancelled state and a payment-evidence disclaimer are explicit. Public tracking is a separate read-only projection of product names and safe statuses/times, never invoice access. The operator payment lookup and verified-payment activation remain future work.
