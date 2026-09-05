# ADR-0025 — Catalog implementation: money as whole toman, one price function, a seeded shop

**Status:** Accepted · 2026-09-05 · implements ADR-0014 (flat catalog, no FTS) and ADR-0021 (promotional pricing)

**Context:** S3 builds the first thing a customer sees. The contracts settled the shape — four flat tables, `effective_price` as the only price rule, `icontains` on a normalised column — and left three gates to the owner: region values, catalog breadth, images. This records what building it decided.

**Decision:**

- **Money is `DecimalField(max_digits=12, decimal_places=0)`**, whole toman, through one `MoneyField()` definition that every money column in the project will use. There is no fractional toman; Decimal keeps arithmetic exact all the way to the order snapshot; zero decimal places documents the unit in the schema itself.
- **Region values:** `global`, `ir`, `us`, `eu`, `tr`. The owner's ruling named Iran; the others exist so a Turkey-region account can be labelled honestly rather than as «جهانی». Warranty stays as the data model had it; delivery type likewise.
- **The two promotion CHECKs carry `violation_error_message` in Persian.** Django validates `CheckConstraint`s in `full_clean()`, so the admin form shows the database's own rule as a Persian sentence. No custom `clean()` exists to drift from the constraint.
- **Search folds on save and on query** (`apps/catalog/search.py`): Arabic ي/ك → Persian ی/ک, half-space → space (what phones type instead of it), three digit scripts → ASCII, harakat and tatweel removed, Latin lowercased. Lossy on purpose; for matching, never display. `search_text` is `editable=False` and **deliberately unindexed** — a btree cannot serve a substring match and the catalog is a few dozen rows. Rate-limited with the same limiter as login, which moved to `apps.core` so catalog does not depend on accounts.
- **"Featured" on the home page is the newest eight active products.** No flag until the operator asks for one; with a catalog this size the newest are the ones worth showing.
- **A card shows the cheapest available plan.** The struck-through pair therefore appears on a card only when the promotion is on the cheapest plan — which is the honest "from" price, not a defect.
- **The add-to-cart control ships complete but inert** (`data-cart-pending`, `action="#"`): plan selector, stepper capped at one on a customer-input plan, one primary button, disabled with an explanation when nothing is available. S4b sets the action and re-validates the cap on the server.
- **django-unfold for the admin** until S7. The product form carries specs and plans inline; spec titles come from a `<datalist>` of titles already in use, so the same fact is spelled the same way on every product.
- **A `seed_catalog` command** (DEBUG only, idempotent, `--reset`) fills the shop with twelve realistic products so pages are judged full; icons come from Simple Icons on jsDelivr into `MEDIA_ROOT`, and a product without one gets a monogram tile designed for that case.
- **Playwright is a dev dependency.** Chrome's CLI could not click, type, paste, or go below ~485px wide. The browser tests skip themselves when no browser is installed.

**Alternatives considered:** an integer money type (fine today, but a Decimal column is what every payment gateway and accounting export expects, and the cost is nothing); a `featured` flag (a field nobody asked for); indexing `search_text` (decoration — `pg_trgm` is the named upgrade); a real cart endpoint now (S4b's job, and shipping half of it means shipping its bugs early).

**Consequences:** The home page is the catalog, so `/` changed meaning and the style guide moved to `/styleguide/`. Media is served by Django in development only; production needs the reverse proxy to serve `MEDIA_ROOT`, which lands with S6b. The product page has a query budget test (≤15) and the category grid one that does not grow with product count; both are the tests that will catch the next N+1. Open-questions #2 (the real product list) is still open; the seed is a stand-in, not content.
