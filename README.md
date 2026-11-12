# پرم‌شاپ · PremShop

[![CI](https://github.com/Sina-Amare/premshop/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Sina-Amare/premshop/actions/workflows/ci.yml)

An online shop for digital subscriptions and accounts for Iranian customers (premshop.ir). One operator runs it and delivers every order by hand, so the software's job is to take orders correctly, keep customers' details safe and make every promise on the page true. The scope is small; the stakes, real money and other people's account details, are not.

**Status:** pre-launch. Browsing, sign-in, the cart, checkout, operator-recorded payments and order tracking are built. Gateway payments, fulfilment and refunds are not enabled.

## Features

- **Storefront** in Persian, right to left: a home page, categories, product pages built from structured facts, and search that understands Arabic-keyboard spellings, half-spaces, Persian digits and near misses.
- **Prices computed on the server** through one function, including timed promotions with countdowns, and discount codes validated under row locks.
- **Sign-in** with a mobile number and a six-digit code sent by SMS, in one flow for signing in and registering, rate-limited per number, per IP and per day; staff sign in at the panel with a password.
- **A persistent cart** for visitors and customers alike, merged into the account at sign-in and available on every device.
- **Atomic checkout:** stock is claimed, the discount recorded and the purchase terms frozen in one transaction, and a repeated submission returns the same order.
- **Customer order pages**, a public tracking link and a downloadable invoice.
- **Purchase confirmation emails** through a durable outbox, with retries and accounting against the mail provider's quota.
- **An operator panel** in Persian: catalogue editing with publishing checks and live previews, read-only order records with an operator-only form for payments received, and a logged reveal of encrypted customer input.
- **Production tooling:** one Docker image per commit, with scripted deploy, rollback and restore, all rehearsed in CI.

## Technology

| Layer | Choice |
|---|---|
| Language and framework | Python 3.14, Django 5.2 LTS |
| Database | PostgreSQL 15 |
| Cache, rate limits and background jobs | Redis 7, Huey |
| Front end | Server-rendered templates, Tailwind CSS 4, a few small scripts |
| Operator panel | Django admin with django-unfold |
| Security | Field encryption with Fernet, restricted Markdown through markdown-it-py and nh3 |
| Email | An SMTP relay (SMTP2GO) |
| Tests and checks | pytest, Playwright, axe-core, ruff, black, mypy, pip-audit, bandit |
| Delivery | Docker, gunicorn, nginx, GitHub Actions |

## Architecture

A modular monolith of eight Django applications with one-way imports:

```text
core  →  accounts, catalog, cms  →  cart, orders  →  payments  →  notifications
```

A web process and a Huey worker share PostgreSQL and Redis, behind nginx. Every price comes from one function, and every status change goes through a service that writes its history row in the same transaction. Details: [architecture](docs/architecture.md).

## Getting started

Prerequisites: Python 3.14, [uv](https://docs.astral.sh/uv/), Node.js 20 or later, PostgreSQL and Redis.

```bash
cp .env.example .env              # fill in SECRET_KEY, DATABASE_URL, FIELD_ENCRYPTION_KEY and the EMAIL_ values
uv sync
npm ci && npm run css
python manage.py migrate
python manage.py seed_catalog     # sample products for development
python manage.py runserver        # http://127.0.0.1:8000
python manage.py run_huey         # the background worker, in a second terminal
```

Configuration, the test setup and troubleshooting are in [development](docs/development.md).

## Testing

```bash
python scripts/check.py           # the whole gate as CI runs it, except the Docker image job
python -m pytest apps/orders -q   # one application's tests
```

The tests run against PostgreSQL and include Playwright browser tests and axe-core accessibility scans. A local pass is evidence; the CI run for the pushed commit is the verdict.

## Documentation

| Document | Covers |
|---|---|
| [Architecture](docs/architecture.md) | Components, application boundaries, request flows, background jobs, email and security |
| [Data model](docs/data-model.md) | Tables, constraints, deletion rules and indexes |
| [Order lifecycle](docs/order-lifecycle.md) | Order statuses, transitions, locking and the payment rules |
| [Development](docs/development.md) | Setup, configuration, commands, tests, assets, the production image and how these documents are kept |
| [Design language](docs/design-language.md) | Colour, type, layout, components and the Persian voice |
| [Runbooks](docs/runbooks/deploy.md) | Deploy, rollback and restore |
| [Decision records](docs/decisions/README.md) | Why the system is built the way it is |

## Repository layout

```text
apps/        Django applications
config/      settings, URLs, WSGI and ASGI
templates/   pages, panel overrides and emails
static/      stylesheets, scripts, fonts and images
locale/      the operator panel's translations
deploy/      compose file, nginx configuration, smoke and rehearsal scripts
scripts/     the quality gate, the CI reader, the deploy tool and maintenance scripts
docs/        documentation
```
