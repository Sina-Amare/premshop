# ADR-0002 — Module boundaries and the service layer

**Status:** Accepted · 2026-09-01

**Context:** Business logic scattered across views is how small Django projects rot; this one moves money and must stay auditable across many AI-assisted sessions.

**Decision:** Eight apps with a strict one-way import order — `core → accounts, catalog, cms → orders → payments → notifications → panel`. Thin views (auth, form validation, **one** service call, render); business logic only in `services.py`, read queries in `selectors.py`. Flows that must be atomic across apps get a composite service in the *downstream* app (`payments.checkout`, `payments.cancel_with_refund`, `payments.revive_order`) — never view-side chaining, never caller-supplied guard flags. `panel` owns no models and no logic. Full contract: [../services-and-modules.md](../services-and-modules.md).

**Alternatives considered:** logic-in-views (rejected outright); a repository layer over the ORM (abstraction with no second implementation); Django signals for domain events (implicit control flow — see ADR-0010).

**Consequences:** A status write outside a service is a code-review reject. Adding a client (bot, API) is adding a caller, not duplicating rules.

**Amendment — 2026-10-23: implementation and remaining boundary violation.** The module rule above holds only in part. No app has `selectors.py`; catalog has no `services.py`. Catalog's rules live on its models and in `pricing.py`, `search.py` and `promises.py`; storefront queries live in `views.py` through `Product.objects.public()`, and publishing is an admin action. Accounts has `services.py`, but password sign-in is still decided in its view. [Services and modules](../services-and-modules.md) records the current locations. For orders and payments, the service rule is unchanged: a status write outside a service remains a code-review reject.

`apps.core.context.shell` imports catalog models to build the header's departments. This violates the one-way boundary; it is not an approved exception. Moving that query into a catalog context processor and adding a test that rejects imports from core into other project apps remain required before this violation can be called fixed.
