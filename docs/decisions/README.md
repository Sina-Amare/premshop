# Architecture Decision Records

Every decision someone might later ask «چرا این‌طوری؟» about. Format: Status · Context · Decision · Alternatives considered · Consequences. New ADRs take the next number; a superseded ADR is marked so, never deleted.

| # | Decision |
|---|---|
| [0001](0001-stack-and-architecture.md) | Django modular monolith, server-rendered, boring stack |
| [0002](0002-module-boundaries-and-service-layer.md) | One-way app imports; thin views; composite money services |
| [0003](0003-order-status-on-item-seven-states.md) | Status on OrderItem; exactly seven states; replacement cycles |
| [0004](0004-no-drf-in-phase-1.md) | No DRF/API until a real out-of-process consumer exists |
| [0005](0005-money-in-toman.md) | Toman everywhere; rial only at two tested boundaries; no discount field |
| [0006](0006-card-to-card-payment-design.md) | Card-to-card: DB-enforced uniqueness, pending-only expiry, revive, card snapshots |
| [0007](0007-credential-encryption-key-escrow-retention.md) | Field encryption, key escrow, retention periods |
| [0008](0008-content-free-messages-and-magic-link.md) | No credential values in messages; the constrained single-use magic link |
| [0009](0009-sla-clock-and-input-timeout-ladder.md) | Wall-clock due_at + snap-forward; pause arithmetic; 21-day input-timeout ladder |
| [0010](0010-event-log-and-notification-outbox.md) | Append-only event log; outbox with dedupe keys; accepted on-commit gap |
| [0011](0011-buy-now-checkout-no-cart.md) | Buy-now + quantity; no cart; item status stands regardless |
| [0012](0012-auth-email-otp.md) | Email identity; inline checkout OTP; new-sign-in alert guard |
| [0013](0013-payment-provider-interface-deferred.md) | Provider interface extracted only when Zibal gives it a second shape |
| [0014](0014-deliberate-simplifications.md) | The simple-over-flexible verdicts, each with its flip condition |
| [0015](0015-error-tracking-glitchtip.md) | sentry-sdk → self-hosted GlitchTip |
| [0016](0016-brand-and-type.md) | Estedad + Vazirmatn; teal on stone; amber means time |
| [0017](0017-development-environment.md) | Windows-native dev; CI enforces Linux parity; WSL Docker rehearses production |

## Legacy shorthand map

The contract documents (data model, state machine, services, notifications, build plan) grew during the design phase and occasionally cite decision-sheet shorthand. Resolve it here:

| Shorthand | Meaning → ADR |
|---|---|
| D1, D2 | Item statuses / payment machine + Refund gate → 0003, 0006 |
| D3 | Toman + no discount → 0005 |
| D4 | No DRF → 0004 |
| D5, D6 | Buy-now / auth flow → 0011, 0012 |
| D7, D8 | SLA + SiteSetting (holiday, hours, cards, window) → 0009, 0006 |
| D9, D10 | New tables / OrderItem additions → 0010, 0007, 0003 |
| D11, D12 | Payment fields + constraints / channels + tracking token → 0006 |
| D13 | Content-free messages (as amended: magic link) → 0008 |
| D14 | Encryption + escrow + private receipts → 0007 |
| D15, D16 | Flat catalog, normalized search / review cuts → 0014 |
| D17, D18 | Outbox + dedupe + heartbeat / events on-commit → 0010 |
| D19, D20 | Cache no-store rules / observability + scrubbing → 0010, 0015 |
| D21, D22 | App map / step-order changes → 0002; roadmap kept outside the repo |
| R1–R22 | Cross-artifact reconciliation rulings, folded into the documents themselves |
