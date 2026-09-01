# ADR-0002 — Module boundaries and the service layer

**Status:** Accepted · 2026-09-01

**Context:** Business logic scattered across views is how small Django projects rot; this one moves money and must stay auditable across many AI-assisted sessions.

**Decision:** Eight apps with a strict one-way import order — `core → accounts, catalog, cms → orders → payments → notifications → panel`. Thin views (auth, form validation, **one** service call, render); business logic only in `services.py`, read queries in `selectors.py`. Flows that must be atomic across apps get a composite service in the *downstream* app (`payments.checkout`, `payments.cancel_with_refund`, `payments.revive_order`) — never view-side chaining, never caller-supplied guard flags. `panel` owns no models and no logic. Full contract: [../services-and-modules.md](../services-and-modules.md).

**Alternatives considered:** logic-in-views (rejected outright); a repository layer over the ORM (abstraction with no second implementation); Django signals for domain events (implicit control flow — see ADR-0010).

**Consequences:** A status write outside a service is a code-review reject. Adding a client (bot, API) is adding a caller, not duplicating rules.
