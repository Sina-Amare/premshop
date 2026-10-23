# ADR-0001 — Stack: Django modular monolith, server-rendered

**Status:** Accepted · 2026-09-01

**Context:** One operator, ~15–20 customers/month, real money and stored credentials. Ops burden and SEO matter more than architectural ambition; prices must be in the HTML, not after a fetch.

**Decision:** Django 5.x modular monolith. Django templates + HTMX + Alpine for all pages; Tailwind (standalone CLI); PostgreSQL; Redis + Celery + Beat for async work; Docker Compose on a single foreign VPS behind an Iranian CDN. Boring technology, chosen deliberately.

**Alternatives considered:** Next.js/SPA front end (doubles ops for one person, hurts SSR/SEO); microservices (senseless at this size); API-first with separate front end (same SEO cost, no consumer).

**Consequences:** One deployable, one database, one service layer. Anything interactive is HTMX-sized; if a rich client is ever needed it arrives as a new consumer of the same services (see ADR-0004).

**Amendment — 2026-10-23: the built frontend toolchain.** No storefront or account page loads HTMX or Alpine, and no template or script of this project uses either. The countdown, password reveal, buy box and code-entry controls use small inline scripts; code entry submits by `fetch` to the existing view. The third-party Unfold panel supplies its own frontend libraries. Tailwind runs through the npm package `@tailwindcss/cli` and `npm run css`, not a standalone binary. Node is therefore part of the development and CI toolchain: npm builds the CSS and supplies axe-core, which the accessibility tests load into the browser from `node_modules`.
