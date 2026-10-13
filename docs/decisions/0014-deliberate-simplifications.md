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
- **Also not built, recorded here so nobody adds them back:** inventory/stock, supplier+FX models, RBAC, wallet/ledger, ticket system, queue soft-lock, IN_APP channel. *(The cart and the discount field left this ledger on 2026-09-01 — both are now in scope, per [ADR-0018](0018-shopping-cart-and-guest-checkout.md) and [ADR-0020](0020-discount-codes.md), and are built at S4. Corrected 2026-09-18: this line said "now built", which was never true.)*

**Consequences:** Adding any of these back requires meeting its flip condition, not just enthusiasm.

**Amendment — 2026-10-14: the trigram upgrade arrived early, for a different reason.** The condition written here was catalogue growth. What actually triggered it was a miss at twelve products: the owner searched «چت جپت», a real spelling of ChatGPT, and got an empty page. «چت جیپت» was on `Product.aliases` and «چت جپت» was not, and no amount of character folding turns one transliteration into another — the alias list is hand-written and can never be finished, which is a property of the approach, not of its size.

So `search()` keeps the exact word-AND pass and falls back to `TrigramWordSimilarity` at 0.4 when it finds nothing. Fuzzy results for a query that already had a real answer would be noise; a miss is the only moment where a near-match beats an empty page. The threshold is measured rather than chosen, and a test asserts the gap between real misspellings and noise stays wider than 0.1.

**Postgres FTS is still not built and the reasoning is unchanged** — it has no Persian stemmer, and normalisation is the Persian search problem at this size. pg_trgm is character-shape matching, not full-text search; the ban in this ADR is on the latter. New dependency: the `pg_trgm` extension, which is *trusted* from PostgreSQL 13, so `CREATE EXTENSION` needs CREATE on the database rather than a superuser — verified against this project's unprivileged role on 15.4.
