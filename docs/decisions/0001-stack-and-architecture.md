# ADR-0001 — Stack: Django modular monolith, server-rendered

**Status:** Accepted · 2026-09-01

**Context:** One operator, ~15–20 customers/month, real money and stored credentials. Ops burden and SEO matter more than architectural ambition; prices must be in the HTML, not after a fetch.

**Decision:** Django 5.x modular monolith. Django templates + HTMX + Alpine for all pages; Tailwind (standalone CLI); PostgreSQL; Redis + Celery + Beat for async work; Docker Compose on a single foreign VPS behind an Iranian CDN. Boring technology, chosen deliberately.

**Alternatives considered:** Next.js/SPA front end (doubles ops for one person, hurts SSR/SEO); microservices (senseless at this size); API-first with separate front end (same SEO cost, no consumer).

**Consequences:** One deployable, one database, one service layer. Anything interactive is HTMX-sized; if a rich client is ever needed it arrives as a new consumer of the same services (see ADR-0004).
