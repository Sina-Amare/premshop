# Development

How to run PremShop locally, the commands you will use, the traps this particular machine setup has, and how the built code hangs together across files. The per-session working rules are in `../AGENTS.md`; this file is reference.

## Setup

Requires Python 3.11+, Node 20+ (for the CSS build), a local PostgreSQL, and Redis. Login codes and rate limits live in Redis, so the site cannot sign anyone in without it.

```bash
cp .env.example .env            # then fill SECRET_KEY, DATABASE_URL and the EMAIL_ values
uv sync                         # Python dependencies, from uv.lock
npm install                     # CSS toolchain, from package-lock.json
npm run css                     # build static/css/app.css
python manage.py migrate
python manage.py seed_catalog   # 12 mock products, development only
python manage.py runserver      # http://127.0.0.1:8000
```

`/healthz` reports whether the database and the cache are reachable; both must say `ok`.

## Commands

`uv` is not on PATH on the owner's machine and `python` may resolve to the system interpreter, so call the venv directly. CI (Linux) uses `uv run <tool>` for the same things.

```bash
.venv/Scripts/python.exe scripts/check.py              # the whole gate, exactly as CI runs it
.venv/Scripts/python.exe scripts/check.py --no-audit   # skip pip-audit (network, slow)

.venv/Scripts/python.exe -m pytest apps/catalog/tests/test_views.py::test_search_rate_limited -q
.venv/Scripts/python.exe -m pytest -k promo -q         # by keyword
.venv/Scripts/python.exe -m pytest -m "not browser" -q # skip the Playwright tests

.venv/Scripts/python.exe manage.py runserver           # needs Redis up — see below
.venv/Scripts/python.exe manage.py seed_catalog        # 12 mock products; --reset, --no-images
.venv/Scripts/python.exe -m uv add <package>           # uv lives inside the venv
npm run css                                            # or css:watch
```

- **The gate is six steps**, in `.github/workflows/ci.yml` and mirrored by `scripts/check.py`: `ruff check .`, `black --check .`, `mypy apps config`, `pytest -q`, `pip-audit`, `bandit -r apps config`. Keep the two files in step. `bandit` does not read ruff's `# noqa`; its marker is a bare `# nosec B###`, with the reason in an ordinary comment.
- **After a push**, the verdict is the run *for that commit*: `curl -s "https://api.github.com/repos/Sina-Amare/premshop/actions/runs?head_sha=$(git rev-parse HEAD)"` — wait until `status` is `completed`, then read `conclusion`. The newest run overall may belong to an older commit or still be running. CI runs on every branch. `gh` is not installed.

## Tests

- They run against the real local PostgreSQL (`DATABASE_URL`), never SQLite. `apps/conftest.py` forces an in-memory cache for every test, so no test depends on a running Redis; the accounts tests switch to a fast password hasher (`apps/accounts/tests/conftest.py`).
- They live under `apps/` because `testpaths = ["apps"]` — including the settings tests, in `apps/core/tests/`.
- The three `browser` tests drive Chromium through Playwright (`python -m playwright install chromium` once) and skip themselves when it is absent, as they do in CI.
- `time-machine` moves the clock in tests that depend on time, such as a login code expiring.

## Environment traps

- **Redis** runs in Docker inside WSL and stops when WSL idles. Start it with `wsl -d Ubuntu-24.04 -u root -e bash -lc 'service docker start && docker start premshop-redis'` (as root, so no sudo password is asked). A login page showing «ورود فعلاً ممکن نیست» means Redis is down.
- **`static/css/app.css` is a gitignored build artifact.** Tailwind v4 scans `templates/` only (`source(none)` in `input.css`), so a class used for the first time in a template does nothing until `npm run css` runs.
- **Never `runserver --noreload` while editing templates** — it serves the old ones, and a screenshot of a stale page looks like a fix that did not work.
- **RTK is off here.** RTK is a global Claude Code hook on this machine that rewrites shell commands (`grep`, `find`, `git`, `pytest`, `curl`, …) into filtered versions to save tokens; here it made failed checks look clean (AGENTS.md §7). `~/.claude/hooks/rtk-guard.sh` skips it in any project holding `.claude/rtk-off`, and `.claude/settings.json` runs `scripts/rtk_canary.py` at every session start, which prints one line either way. If it says `FAILED`, reinstalling or upgrading RTK (`rtk init -g`) has most likely put back the unguarded hook entry; the guard's header comment gives the line to restore. Deleting `.claude/rtk-off` turns RTK back on here.
- **Writing files that mix Persian and quotes:** Git Bash heredocs break on them, and tool layers turn `\n` and `\uXXXX` escapes back into the characters. Use a file-writing tool or a script file; build a backslash with `chr(92)` when an escape must survive. The console is cp1256: set `PYTHONIOENCODING=utf-8` before printing Persian, and in PowerShell 5.1 pass `-Encoding utf8` to `Get-Content`.

## Backing up docs-local

`docs-local/` (gitignored: costs, margins, supplier notes) is backed up after every commit made in it, by a post-commit hook that `scripts/backup_docs_local.py setup` installed. A backup is a git bundle of its whole history, encrypted with gpg (AES-256) under a passphrase kept in `~/.premshop-backup/passphrase` on the laptop and, off it, in the owner's password manager and on paper. The encrypted file goes to two places: the `docs-local-backup` branch of this repository (one commit, replaced each time; it holds no workflow files, so pushing it runs no CI), and `G:\Backups\premshop\` on the laptop's second physical disk, which keeps the last five. Before copying, the script checks that the file decrypts back to the bundle **and** that a wrong passphrase does not open it. A failure prints `BACKUP FAILED: <where and why>` and `docs-local BACKUP INCOMPLETE` on the commit's output; each backup holds the whole history, so the next one that succeeds catches up.

- **Restore:** the branch's `README.md` has the commands. `.venv/Scripts/python.exe scripts/backup_docs_local.py verify` runs exactly those commands in an empty folder with an empty home directory, cloning from the public URL, checks the result is the same commit as `docs-local/` here, and decrypts the newest G: copy. Run it after changing anything about the backup.
- **Uncommitted edits are not in a backup**; the script says so when there are any.
- **What each copy survives:** G: survives a failed C: drive but not a lost or stolen laptop; GitHub survives both, and is only as good as the passphrase copies kept off the laptop.

## How the built code hangs together

Built so far: `core`, `accounts`, `catalog`. Which app owns what and may import what is `services-and-modules.md` §1; why views stay thin is its §4. What follows spans files and is not obvious from any one of them.

- **Settings** are `config/settings/{base,dev,prod}.py`, entirely environment-driven. `.env` is loaded with `setdefault`, so the real environment always wins. `manage.py` and pytest default to `dev`; `wsgi`/`asgi` default to `prod`, which **refuses to boot** without `ALLOWED_HOSTS`, the relay credentials, and a From address at `premshop.ir`.
- **The cache is part of authentication.** Login codes and every rate-limit counter live in Redis through Django's cache API. `CacheUnavailableMiddleware` turns a Redis failure anywhere into a Persian 503 instead of a traceback; there is deliberately no failing open, because that would mean unlimited guesses at a six-digit code. A login code's deadline is stored *with* the code, because Django's cache API cannot read a remaining lifetime back.
- **Domain exceptions carry the Persian sentence the customer sees** (for example `RateLimited` in `apps/accounts/services.py`), because the message is part of the behaviour.
- **Template traps, each of which has shipped once:** `{# … #}` is single-line in Django — a multi-line one is emitted as text, so notes use `{% comment %}` (a test scans every template); a template using the `fa` filters without `{% load fa %}` is a 500 that no view test sees; an `<input>` without `field__input` has no border, because Tailwind's preflight zeroes them.
- **CSS is component classes** (`.card`, `.fact`, `.pcard`, `.field__input`, …) built from the tokens in `static/css/input.css`, not utility classes in markup. Emails are a separate medium: table layout, inline styles, their own font stack (ADR-0023).
- **The admin is django-unfold**, which must precede `django.contrib.admin` in `INSTALLED_APPS`. It is the operator's tool until S7 builds the panel. Database CHECK constraints carry Persian `violation_error_message`s, so the admin form shows the database's own rule.
