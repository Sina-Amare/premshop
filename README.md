# پرم‌شاپ · PremShop

Online shop for digital subscriptions and accounts (premshop.ir). Django modular monolith; server-rendered Persian RTL; payments through an Iranian gateway, with an operator-only manual fallback. One operator, real money, real credentials — small scope, high bar.

- **Start here:** [docs/README.md](docs/README.md) — maps every question to its document
- **Why it's built this way:** [docs/decisions/](docs/decisions/README.md)
- **Working with AI sessions:** [CLAUDE.md](CLAUDE.md)

## Local setup

Requires Python 3.11+, Node 20+ (for the CSS build), a local PostgreSQL, and Redis. Login codes and rate limits live in Redis, so the site cannot sign anyone in without it.

```bash
cp .env.example .env            # then fill SECRET_KEY, DATABASE_URL and the EMAIL_ values
uv sync                         # Python dependencies, from uv.lock
npm install                     # CSS toolchain, from package-lock.json
npm run css                     # build static/css/app.css (gitignored; rebuild after CSS or template changes)
python manage.py migrate
python manage.py seed_catalog   # 12 mock products, development only
python manage.py runserver      # http://127.0.0.1:8000
```

`npm run css:watch` rebuilds stylesheets while you work. `/healthz` reports whether the database and the cache are reachable.

### Checks

```bash
python scripts/check.py         # everything CI runs, in CI's order
```

That is `ruff check .`, `black --check .`, `mypy apps config`, `pytest -q` (against real PostgreSQL), `pip-audit` and `bandit`. A local pass is evidence; the workflow run on GitHub is the verdict, so check it after every push. Three tests drive a real browser and need `python -m playwright install chromium` once; they skip themselves without it.

The first migration in this project's history is the custom user model (`accounts.0001`), deliberately: swapping Django's built-in user after tables exist means rebuilding the schema by hand. See [ADR-0024](docs/decisions/0024-auth-implementation.md).
