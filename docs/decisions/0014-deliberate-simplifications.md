# ADR-0014 — Deliberate simplifications (each with its flip condition)

**Status:** Accepted · 2026-09-01

**Context:** Several structure questions had a "flexible" and a "simple" answer. Each was decided on this project's actual shape — one operator, a few dozen SKUs, production stakes — with the condition that would flip it written down, so nobody relitigates them casually *or* clings to them past their expiry.

**Decision (one line each):**

- **Identity flat on `User`** (`telegram_id` etc. as unique nullable columns) — no identity tables. *Flips when* users can exist without an email (Telegram-first signup) or a real second login provider is committed.
- **Product specs are title/value rows.** Filters come from the typed columns that already exist (`delivery_type`, `region`, `warranty`, price/duration); the migration path is per-attribute **column promotion**, not a schema registry. *Flips at* hundreds of heterogeneous SKUs with real facet demand.
- **Categories are flat** — no parent FK (the brief's own out-of-scope list rejects two-level). *Flips* trivially with one additive migration if hierarchy is ever real.
- **Catalog: 20–30 focus items**, added one at a time on demand signal (zero-result searches are the signal, not the supplier's inventory). *Flips* only with more operator capacity, automated pricing, and a higher Enamad tier together.
- **Panel = three custom views** (queue, delivery, dashboard) + stock admin for cold-path CRUD. *Flips at* ~10 orders/day, a large repricing-heavy catalog, or a second operator.
- **Search = normalized column + `icontains`** (yeh/kaf/half-space/digit folding), deliberately unindexed; pg_trgm is the named upgrade path. Postgres FTS has no Persian stemmer — normalization *is* the Persian search problem at this size.
- **Review extras cut** (auto credential-scan duplicates total manual moderation; helpfulness voting is noise below ~20 reviews/product).
- **Also not built, recorded here so nobody adds them back:** cart (ADR-0011), coupons/discount field (ADR-0005), inventory/stock, supplier+FX models, RBAC, wallet/ledger, ticket system, queue soft-lock, IN_APP channel.

**Consequences:** Adding any of these back requires meeting its flip condition, not just enthusiasm.
