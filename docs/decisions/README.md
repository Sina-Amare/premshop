# Architecture Decision Records

Every decision someone might later ask «چرا این‌طوری؟» about. Format: Status · Context · Decision · Alternatives considered · Consequences. New ADRs take the next number; a superseded ADR is marked so, never deleted.

| # | Decision |
|---|---|
| [0001](0001-stack-and-architecture.md) | Django modular monolith, server-rendered *(amended 2026-10-23: inline scripts; npm Tailwind and axe-core toolchain)* |
| [0002](0002-module-boundaries-and-service-layer.md) | One-way app imports; thin views; composite money services *(amended 2026-10-23: current rule locations; core's catalog import remains a violation)* |
| [0003](0003-order-status-on-item-seven-states.md) | Status on OrderItem; exactly seven states; replacement cycles |
| [0004](0004-no-drf-in-phase-1.md) | No DRF/API until a real out-of-process consumer exists |
| [0005](0005-money-in-toman.md) | Toman everywhere; rial only at the gateway adapter *(amended: discount field now exists — 0020; the instructions-page boundary is gone)* |
| [0006](0006-card-to-card-payment-design.md) | ~~Card-to-card: DB-enforced uniqueness, pending-only expiry, revive, card snapshots~~ — **superseded by [0019](0019-payment-gateway.md)** |
| [0007](0007-credential-encryption-key-escrow-retention.md) | Field encryption, key escrow, retention periods; key rotation and manual incoming activation details; 2026-10-31 amendment: retention remains internal, short public privacy; 2026-10-29: cancelled input and interim deletion enforcement |
| [0008](0008-content-free-messages-and-magic-link.md) | No credential values in software messages; constrained magic link; 2026-10-26 incoming manual activation exception |
| [0009](0009-sla-clock-and-input-timeout-ladder.md) | Wall-clock due_at + snap-forward; pause arithmetic; 21-day input-timeout ladder *(amended 2026-10-31: later of payment and required details; missing-input refund exception; 2026-10-29: interim manual enforcement; 2026-10-28: delivery cap 48 hours for every product; 2026-10-20, evening: delivery note printed as written)* |
| [0010](0010-event-log-and-notification-outbox.md) | Append-only event log; outbox with dedupe keys; accepted on-commit gap |
| [0011](0011-buy-now-checkout-no-cart.md) | ~~Buy-now + quantity; no cart~~ — **superseded by [0018](0018-shopping-cart-and-guest-checkout.md)**; its item-status argument still stands *(amended 2026-10-23: the planned persistent cart needs its own tables and migration)* |
| [0012](0012-auth-email-otp.md) | Email identity; inline checkout OTP; new-sign-in alert guard *(amended 2026-10-23: code sign-in restores access, not forgotten-password replacement; Iranian-host deliverability remains untested)* |
| [0013](0013-payment-provider-interface-deferred.md) | One payment implementation, no interface; extract only if a real second gateway arrives |
| [0014](0014-deliberate-simplifications.md) | The simple-over-flexible verdicts, each with its flip condition *(amended 2026-10-14: trigram fallback; 2026-10-23: the GIN index exists but query/index mismatch remains; no category filters)* |
| [0015](0015-error-tracking-glitchtip.md) | sentry-sdk → self-hosted GlitchTip |
| [0016](0016-brand-and-type.md) | Estedad + Vazirmatn; teal action colour; amber means time *(amended 2026-10-13: white canvas and neutral ramp; 2026-10-14: ink surfaces and two general radii; 2026-10-23: constellation radii remain an unresolved divergence)* |
| [0017](0017-development-environment.md) | Windows-native dev; CI enforces Linux parity; WSL Docker rehearses production *(amended 2026-10-23: PostgreSQL rationale corrected; daily Redis container, in-memory test cache)* |
| [0018](0018-shopping-cart-and-guest-checkout.md) | Persistent cart, guest merge and anonymous browsing; 2026-10-27 amendment: untimed selections without stock holds, visible unavailable rows and atomic stock claims deferred to checkout |
| [0019](0019-payment-gateway.md) | Gateway is the rail; server-side verify only, amount checked, idempotent, inquiry job mandatory |
| [0020](0020-discount-codes.md) | Scoped discount codes computed server-side; `DiscountRedemption` rows; `used_count` under row lock; total floored at zero |
| [0021](0021-promotional-pricing.md) | Promo price + window on `Plan`; one `effective_price` rule *(amended 2026-10-23: countdowns; 2026-10-24: separate plan offers and successive server-computed card expiry prices)* |
| [0022](0022-transactional-email.md) | SMTP2GO relay, env-driven; TLS derived from the port; prod fails closed on a blank relay password |
| [0023](0023-email-templates.md) | Email templates: tables, wordmark in type; three files per message; amber means time *(amended 2026-09-18: expanded font stack and web-font link; 2026-10-23: fallback tests, built digit normalization and radius comparison; 2026-10-31: web-font link removed)* |
| [0024](0024-auth-implementation.md) | Auth build: user model is migration 0001; codes in cache; one login+registration flow; no separate password reset *(amended 2026-09-21: implementation defects fixed; 2026-10-23: atomic attempts and single-use deletion; forgotten-password replacement still missing)* |
| [0025](0025-catalog-implementation.md) | Catalog build: whole-toman money; Persian CHECK messages; search folding; seeded shop; Unfold admin *(amended 2026-10-13, 2026-10-14, 2026-10-14b and 2026-10-20: region removed, per-plan quantities, no category filters, renewable products, key specs and required browser tests; 2026-10-23: search-index limitation, home cap, mark sources, query budgets and delivery label; 2026-10-24: shared brand-color cards, separate plan offers and selected-plan summary)* |
| [0026](0026-storefront-composition.md) | Storefront: ink top strip and footer; subset fonts; brand marks sourced per product *(amended 2026-10-14b: light brand-wash hero and linked constellation; 2026-10-23: tile radii diverge from the general rule and await an owner decision)* |
| [0027](0027-operator-text-restricted-markdown.md) | Operator-written text is restricted Markdown through one renderer: markdown-it-py with raw HTML off, then nh3's allowlist; Latin runs in `<bdi>`; never an `h1` *(amended 2026-10-20: a full address on the shop's own host is an internal link)* |
| [0028](0028-product-page-content-model.md) | A product page is facts in fields and prose in sections of six kinds; «پیش از خرید» when it says more than the warranty (amended 2026-10-20); questions in `catalog`; one publishing rule; staff preview of a draft at its own address *(amended 2026-10-22: `iran_stance` became `usage_notes`, «نکات استفاده»; the stance's sources requirement was dropped; 2026-10-31: eligible unavailable pages, separate for-sale selectors, draft-editable product addresses and loc-only sitemap)* |
| [0029](0029-unpaid-claims-and-scheduled-release.md) | Fixed unpaid deadlines, whole-order stock/discount release, checkout replay and Huey scheduling; amended 2026-10-30: remove activation-consent collection, preserve history |
| [0030](0030-legal-page-drafts.md) | Fixed legal-page URLs, private staff previews, separate publishing authority and purchase-record boundary |

## Legacy shorthand map

The contract documents (data model, state machine, services, notifications) grew during the design phase and occasionally cite decision-sheet shorthand. Resolve it here:

| Shorthand | Meaning → ADR |
|---|---|
| D1, D2 | Item statuses / payment machine + Refund gate → 0003, ~~0006~~ **0019** |
| D3 | Toman + no discount → 0005; the discount half → **0020**; promotional pricing → **0021** |
| D4 | No DRF → 0004 |
| D5, D6 | Buy-now / auth flow → ~~0011~~ **0018**, 0012 |
| D7, D8 | SLA + SiteSetting (holiday, hours; card slots deleted; the two clocks are now `gateway_timeout_minutes` and `unpaid_order_ttl_hours`) → 0009, ~~0006~~ **0019** |
| D9, D10 | New tables / OrderItem additions → 0010, 0007, 0003 |
| D11, D12 | Payment fields + constraints / channels + tracking token → ~~0006~~ **0019** |
| D13 | Content-free messages (as amended: magic link) → 0008 |
| D14 | Encryption + escrow (private receipts deleted with the card-to-card rail) → 0007, 0019 |
| D15, D16 | Flat catalog, normalized search / review cuts → 0014 |
| D17, D18 | Outbox + dedupe + heartbeat / events on-commit → 0010 |
| D19, D20 | Cache no-store rules / observability + scrubbing → 0010, 0015 |
| D21, D22 | App map / step-order changes → 0002; roadmap kept outside the repo |
| G1–G12 | Gateway-round rulings (manual-payment action G2, inquiry sweep G4, gateway refund API G5, cart/discount shape G10, G12), all folded into **0018–0021** — cite the ADR inline, not the G number |
| R1–R22 | Cross-artifact reconciliation rulings, folded into the documents themselves |
