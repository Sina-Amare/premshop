# ADR-0029 — Unpaid claims, whole-order release and Huey

**Status:** Accepted · 2026-10-29

**Context:** Saved carts do not reserve stock. Unpaid orders commit finite stock and discount capacity, so retries, two tabs and worker outages must not duplicate claims or lose purchase history. Celery was installed without a consumer; the current workload needs expiry and two daily database cleanups.

**Decision:** Unpaid orders have a permanent checkout UUID, a keyed submission digest, a fixed 24-hour deadline and accepted activation-consent wording/version/time. One unexpired unpaid order per customer is enforced under the account lock. Successful identical retries return that order before checking a new cart or quote expiry; another customer or changed content cannot reuse its identifier. Zero-total checkout consumes nothing. The downstream coordinator lives in payments, while cart and orders remain independent; no placeholder Payment row is created.

Creation and release follow account (`FOR NO KEY UPDATE`) → cart when needed → existing orders → products → plans → discount codes → items, each group sorted by primary key. Existing overdue and proposed new resources are locked together before release. One pricing time is captured after locks. Whole-toman Decimal amounts use `effective_price`; overflow fails before writing.

Finite stock is available for new purchases. Each unit records whether stock was debited. Whole unpaid cancellation/expiry restores a debited unit once, and only while the plan has finite stock. Unlimited-origin purchases never create stock. Redemptions retain their amount and history; `released_at` removes them from global/per-customer capacity. Events commit with release. Stale stock forms preserve current quantities for unrelated edits and require review for conflicting intentional edits.

Replace unused Celery with one Redis-backed Huey consumer, one worker and explicit asynchronous mode in development/production. Queue namespaces derive from database host/port/name without credentials. Database-aware tasks expire unpaid orders every minute in bounded per-order transactions and clean guest carts/sessions daily. Tests retain queued execution in memory. Sweep freshness and live backlog appear in the order list. Restart catches up from database deadlines; missed/duplicate runs never renew deadlines or repeat release.

**Alternatives considered:** Cart holds would restore a policy removed in ADR-0018. Item cancellation would split an unpaid purchase and its discount. Deleting redemptions would erase history. Celery plus beat adds another scheduler process for three tasks. Immediate development tasks hide worker failures. A Payment placeholder invents an unimplemented state machine.

**Consequences:** Downtime can delay release; checkout still locks and checks stock/deadlines. Large backlogs take several sweeps. Consistency failures stay visible for review while later rows continue. Deployment needs a supervised consumer, Redis and an observed successful sweep. Gateway initiation/verification stays disabled until coordinated with expiry; later payment failures retry the same order without extending its deadline. Paid fulfilment/refunds and public launch remain separate work.

Sources: [Django row locking](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update), [Huey Django integration](https://huey.readthedocs.io/en/latest/contrib.html).
