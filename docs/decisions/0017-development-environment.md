# ADR-0017 — Development on Windows; parity enforced by CI; Docker in WSL for production rehearsal

**Status:** Accepted · 2026-09-01

**Context:** Production is a Linux VPS running Docker Compose. The developer's machine is Windows, with PostgreSQL already installed and running natively, and WSL2 (Ubuntu 24.04) carrying Docker but with a stopped daemon that needs a sudo password per boot. Dev/prod parity is a real concern — the gap between a developer's machine and production is where "works on my machine" bugs live — but eliminating it entirely costs a permanent daily tax.

**Decision:** Daily development runs **natively on Windows**: the project's Python virtual environment plus the pre-existing local PostgreSQL. Parity is enforced **by CI**, which runs the full test suite on Ubuntu against real PostgreSQL on every push, so Windows-only assumptions surface within minutes without maintaining a second environment. The Docker Compose stack written for production is exercised in **WSL Docker** before it reaches the server (at the soft-deploy step), so the deployment path is rehearsed rather than debugged live. Redis and Celery arrive with the steps that need them; how they run locally is decided then.

Database access follows **least privilege**: a dedicated `premshop` role (LOGIN, CREATEDB — the latter so the test runner can create and drop its own test databases) owns the `premshop` database, with a generated password stored only in the gitignored `.env`. The PostgreSQL superuser credential was used once for provisioning and is stored nowhere.

**Alternatives considered:**
- *Full WSL development* (best parity: same OS, native Docker and Celery). Rejected for now — it requires relocating the repository into the WSL filesystem for acceptable speed, re-establishing git credentials there, a sudo password on every boot, and markedly slower tooling; it buys protection against a narrow risk class that CI already covers on every push.
- *Docker Desktop on Windows*: another daemon and licence surface for the same coverage CI provides.
- *SQLite for local development*: rejected outright — the design depends on partial unique indexes that SQLite lacks, so tests would pass while proving nothing (see [ADR-0019](0019-payment-gateway.md)).

**Consequences:** Local Postgres is 15.4 while production will pin its own major version — immaterial for the features used, and revisited when the production stack is built. Celery on Windows will need a development-only pool flag when it arrives. If CI ever starts catching Windows-specific failures repeatedly, that is the signal to revisit full WSL development.
