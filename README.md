# پرم‌شاپ · PremShop

Online shop for digital subscriptions and accounts (premshop.ir). Django modular monolith; server-rendered Persian RTL; payments through an Iranian gateway, with an operator-only manual fallback. One operator, real money, real credentials — small scope, high bar.

- **Run it:** [docs/development.md](docs/development.md) — setup, commands, tests, and the traps of this environment
- **How the docs are organised:** [docs/README.md](docs/README.md) — which document answers which question
- **Why it's built this way:** [docs/decisions/](docs/decisions/README.md)
- **How work happens here (for people and coding agents):** [AGENTS.md](AGENTS.md)

Built so far: sign-in (email codes and passwords), the catalog, and the email path. Next: orders.

The whole quality gate, exactly as CI runs it: `python scripts/check.py`. A local pass is evidence; the workflow run on GitHub for the pushed commit is the verdict.
