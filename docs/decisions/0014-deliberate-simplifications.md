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

**Amendment — 2026-10-23: search index and category controls.** Catalog migration 0006 added `product_search_text_trgm`, a GIN index on `search_text` with `gin_trgm_ops`, together with the `pg_trgm` extension. Search still uses word-by-word `icontains`, then `TrigramWordSimilarity` at 0.4 only when the first pass finds nothing. The index's existence does not establish its use: query-plan checks on 2026-10-23 found sequential scans for both generated query shapes even with sequential scans discouraged. Matching those queries to a usable index remains a separate correction; no search-speed benefit is claimed here.

No page filters the catalog. The `region` column was dropped in catalog 0003; a region-bound product states that fact in a spec row. The delivery-type filter was removed on 2026-10-14. ADR-0025 records why two or three products do not warrant category filters and sets the revisit point at roughly fifteen products in one category, with sorting first. Column promotion remains the path for an attribute that actually needs filtering.

**Amendment — 2026-10-26: shared stock reservations.** Limited stock is now a requirement: two customers must not reserve the same last unit. The earlier exclusion of stock is superseded by a nullable total count on Plan and a reservation deadline on CartItem, as specified in ADR-0018. Available stock is the total minus live cart reservations. There is no inventory app, supplier model or background expiry job. Checkout must transfer reservations into its own order/payment lifecycle before it is released; cart expiry alone cannot protect a payment in progress.

**Amendment — 2026-10-27: stock without cart holds.** ADR-0018 now keeps untimed purchase selections and removes cart-stage reservations. Plan still carries the available stock count; carts do not consume it. Checkout must claim stock atomically when creating an order. Separate inventory and supplier models remain excluded; revisit only when stock movements need their own accounting.


**Amendment — 2026-10-28: search queries match the index.** Normalized containment uses LIKE on the normalized column. Fuzzy search first uses the indexed word-similarity operator with connection threshold 0.3, then retains the measured 0.4 score filter and ranking. The existing GIN index supports both paths; PostgreSQL plan regressions exercise that compatibility. Exact matches still suppress the fuzzy fallback.
