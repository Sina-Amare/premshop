# Architecture decision records

Every decision someone might later ask «چرا این‌طوری؟» about. Each record has the same shape: status, context, decision, alternatives considered, consequences, and the condition that would justify revisiting it.

A record is never rewritten. A later change is a dated amendment appended to it, and a replaced decision is marked superseded and kept. "Last change" below is the date of the newest amendment.

| ADR | Decision | Status | Last change |
|---|---|---|---|
| [0001](0001-stack-and-architecture.md) | Stack: Django modular monolith, server-rendered | Accepted, amended | 2026-11-04 |
| [0002](0002-module-boundaries-and-service-layer.md) | Module boundaries and the service layer | Accepted, amended | 2026-10-28 |
| [0003](0003-order-status-on-item-seven-states.md) | Status lives on `OrderItem`, with exactly seven states | Accepted, amended | 2026-10-29 |
| [0004](0004-no-drf-in-phase-1.md) | No DRF and no HTTP API in phase 1 | Accepted, amended | 2026-11-10 |
| [0005](0005-money-in-toman.md) | All stored amounts are toman, integer-valued decimals | Accepted, amended | 2026-09-01 |
| [0006](0006-card-to-card-payment-design.md) | Card-to-card payment design | Superseded by [0019](0019-payment-gateway.md) | 2026-09-01 |
| [0007](0007-credential-encryption-key-escrow-retention.md) | Credential encryption, key escrow and retention | Accepted, amended | 2026-11-07 |
| [0008](0008-content-free-messages-and-magic-link.md) | Messages carry no credential values; a single-use magic link is the one convenience | Accepted, amended | 2026-10-29 |
| [0009](0009-sla-clock-and-input-timeout-ladder.md) | Delivery deadlines, pause arithmetic and the input-timeout ladder | Accepted, amended | 2026-11-05 |
| [0010](0010-event-log-and-notification-outbox.md) | Append-only event log and notification outbox | Accepted, amended | 2026-11-10 |
| [0011](0011-buy-now-checkout-no-cart.md) | Buy-now checkout without a cart | Superseded by [0018](0018-shopping-cart-and-guest-checkout.md) | 2026-10-23 |
| [0012](0012-auth-email-otp.md) | Email identity, inline checkout sign-in, one way in per account | Superseded by [0031](0031-sign-in-by-phone.md) | 2026-11-12 |
| [0013](0013-payment-provider-interface-deferred.md) | No payment-provider interface until a second gateway exists | Accepted | 2026-09-01 |
| [0014](0014-deliberate-simplifications.md) | Deliberate simplifications, each with its flip condition | Accepted, amended | 2026-11-04 |
| [0015](0015-error-tracking-glitchtip.md) | Error tracking: sentry-sdk reporting to self-hosted GlitchTip | Accepted | 2026-09-01 |
| [0016](0016-brand-and-type.md) | Brand and type: Estedad over Vazirmatn; the accent colour; amber means attention; five status tones | Accepted, amended (cobalt accent since 2026-10-25; status tones 2026-11-11) | 2026-11-11 |
| [0017](0017-development-environment.md) | Development on Windows, parity enforced by CI, Docker in WSL | Accepted, amended | 2026-10-23 |
| [0018](0018-shopping-cart-and-guest-checkout.md) | Persistent cross-device cart and anonymous browsing | Accepted, amended | 2026-10-27 |
| [0019](0019-payment-gateway.md) | The payment gateway is the rail; verification is server-side and confirmation idempotent | Accepted, amended | 2026-11-08 |
| [0020](0020-discount-codes.md) | Discount codes: scoped, server-computed, concurrency-safe, floored at zero | Accepted, amended | 2026-11-08 |
| [0021](0021-promotional-pricing.md) | Promotional pricing lives on the plan, behind one function | Accepted, amended | 2026-10-24 |
| [0022](0022-transactional-email.md) | Transactional email through an SMTP relay, with attempt accounting | Accepted, amended | 2026-11-12 |
| [0023](0023-email-templates.md) | Email templates: hand-built tables, system fonts, wordmark set in type | Accepted, amended | 2026-10-31 |
| [0024](0024-auth-implementation.md) | Authentication: cache-backed codes, one flow for sign-in and registration | Accepted, amended | 2026-11-12 |
| [0025](0025-catalog-implementation.md) | Catalogue: whole-toman money, one price function, a seeded shop | Accepted, amended | 2026-11-04 |
| [0026](0026-storefront-composition.md) | Storefront composition; the current look is in the [design language](../design-language.md) | Accepted, amended | 2026-10-23 |
| [0027](0027-operator-text-restricted-markdown.md) | Operator-written text is restricted Markdown through one renderer | Accepted, amended | 2026-10-20 |
| [0028](0028-product-page-content-model.md) | A product page is facts in fields and prose in sections of six kinds | Accepted, amended | 2026-10-31 |
| [0029](0029-unpaid-claims-and-scheduled-release.md) | Unpaid claims, whole-order release and scheduled jobs | Accepted, amended | 2026-11-10 |
| [0030](0030-legal-page-drafts.md) | Legal pages with protected drafts | Accepted, amended | 2026-11-02 |
| [0031](0031-sign-in-by-phone.md) | Sign-in by mobile number and SMS code | Accepted | 2026-11-12 |

## Writing a record

1. Take the next number, `0032`, and name the file `NNNN-short-title.md`.
2. Follow the shape of the existing records: status, context, decision, alternatives considered, consequences, and when to revisit.
3. Add a row to the table above.
4. To change an accepted decision, append `**Amendment — YYYY-MM-DD.**` with what changed and why, and update its row here. To replace one, write a new record and mark the old one superseded.

## Names used in older records

Older records refer to documents that have since been reorganised or were never part of this repository.

| Older name | Now |
|---|---|
| `services-and-modules.md`, `notifications-and-bot.md` | [architecture.md](../architecture.md) |
| `state-machine.md`, with transition IDs A1–A14 | [order-lifecycle.md](../order-lifecycle.md), which keeps the same IDs |
| `open-questions.md` | An internal planning note, kept outside this repository |
