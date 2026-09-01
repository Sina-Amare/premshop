# پرم‌شاپ · PremShop

Online shop for digital subscriptions and accounts (premshop.ir). Django modular monolith; server-rendered Persian RTL; card-to-card payments at launch. One operator, real money, real credentials — small scope, high bar.

- **Start here:** [docs/README.md](docs/README.md) — maps every question to its document
- **Why it's built this way:** [docs/decisions/](docs/decisions/README.md)
- **Current state:** [docs/progress.md](docs/progress.md)
- **Working with AI sessions:** [CLAUDE.md](CLAUDE.md)

## Local setup

Requires Python 3.11+, Node 20+ (for the CSS build) and a local PostgreSQL.

```bash
cp .env.example .env          # then fill SECRET_KEY and DATABASE_URL
uv sync                       # Python dependencies, from uv.lock
npm install                   # CSS toolchain, from package-lock.json
npm run css                   # build static/css/app.css
python manage.py runserver    # http://127.0.0.1:8000
```

`npm run css:watch` rebuilds stylesheets while you work.

### Checks (the same four CI runs on every push)

```bash
ruff check .        # likely bugs, import order, insecure patterns
black --check .     # formatting
mypy apps config    # types
pytest -q           # tests, against real PostgreSQL
```

**No migrations yet, deliberately:** the first migration in this project's history must be the custom user model (S2), because swapping Django's built-in user after tables exist is painful. See [ADR-0012](docs/decisions/0012-auth-email-otp.md).
