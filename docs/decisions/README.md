# Architecture Decision Records

Every decision someone might later ask «چرا این‌طوری؟» about. Format: Status · Context · Decision · Alternatives considered · Consequences. New ADRs take the next number; a superseded ADR is marked so, never deleted.

| # | Decision |
|---|---|
| [0001](0001-stack-and-architecture.md) | Django modular monolith, server-rendered, boring stack |
| [0002](0002-module-boundaries-and-service-layer.md) | One-way app imports; thin views; composite money services |
| [0003](0003-order-status-on-item-seven-states.md) | Status on OrderItem; exactly seven states; replacement cycles |
| [0004](0004-no-drf-in-phase-1.md) | No DRF/API until a real out-of-process consumer exists |
| [0005](0005-money-in-toman.md) | Toman everywhere; rial only at the gateway adapter *(amended: discount field now exists — 0020; the instructions-page boundary is gone)* |
| [0006](0006-card-to-card-payment-design.md) | ~~Card-to-card: DB-enforced uniqueness, pending-only expiry, revive, card snapshots~~ — **superseded by [0019](0019-payment-gateway.md)** |
| [0007](0007-credential-encryption-key-escrow-retention.md) | Field encryption, key escrow, retention periods |
| [0008](0008-content-free-messages-and-magic-link.md) | No credential values in messages; the constrained single-use magic link |
| [0009](0009-sla-clock-and-input-timeout-ladder.md) | Wall-clock due_at + snap-forward; pause arithmetic; 21-day input-timeout ladder |
| [0010](0010-event-log-and-notification-outbox.md) | Append-only event log; outbox with dedupe keys; accepted on-commit gap |
| [0011](0011-buy-now-checkout-no-cart.md) | ~~Buy-now + quantity; no cart~~ — **superseded by [0018](0018-shopping-cart-and-guest-checkout.md)**; its item-status argument still stands |
| [0012](0012-auth-email-otp.md) | Email identity; inline checkout OTP; new-sign-in alert guard |
| [0013](0013-payment-provider-interface-deferred.md) | One payment implementation, no interface; extract only if a real second gateway arrives |
| [0014](0014-deliberate-simplifications.md) | The simple-over-flexible verdicts, each with its flip condition |
| [0015](0015-error-tracking-glitchtip.md) | sentry-sdk → self-hosted GlitchTip |
| [0016](0016-brand-and-type.md) | Estedad + Vazirmatn; teal on stone; amber means time |
| [0017](0017-development-environment.md) | Windows-native dev; CI enforces Linux parity; WSL Docker rehearses production |
| [0018](0018-shopping-cart-and-guest-checkout.md) | Persistent `Cart`/`CartItem` in their own `cart` app, plan+quantity only, guest cart merges on login; anonymous browsing, identity inside checkout |
| [0019](0019-payment-gateway.md) | Gateway is the rail; server-side verify only, amount checked, idempotent, inquiry job mandatory |
| [0020](0020-discount-codes.md) | Scoped discount codes computed server-side; `DiscountRedemption` rows; `used_count` under row lock; total floored at zero |
| [0021](0021-promotional-pricing.md) | Promo price + window on `Plan`; one `effective_price` function is the only place the rule lives |
| [0022](0022-transactional-email.md) | SMTP2GO relay, env-driven; TLS derived from the port; prod fails closed on a blank relay password |
| [0023](0023-email-templates.md) | Email templates: tables, system fonts, wordmark in type; three files per message; amber still only means time |
| [0024](0024-auth-implementation.md) | Auth build: user model is migration 0001; codes in cache; one flow for login+registration; no separate password reset |
| [0025](0025-catalog-implementation.md) | Catalog build: money as whole toman; region values; Persian CHECK messages; search folding; seeded shop; Unfold admin |

## Legacy shorthand map

The contract documents (data model, state machine, services, notifications, build plan) grew during the design phase and occasionally cite decision-sheet shorthand. Resolve it here:

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
