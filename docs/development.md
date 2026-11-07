# Development

How to set up, run, test and build PremShop. How the code is organised is in [architecture](architecture.md); deploying is in the [runbooks](runbooks/deploy.md).

- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running locally](#running-locally)
- [Sample data](#sample-data)
- [Commands](#commands)
- [Quality gate and CI](#quality-gate-and-ci)
- [Documentation](#documentation)
- [Tests](#tests)
- [Front-end assets](#front-end-assets)
- [Translations](#translations)
- [Production image](#production-image)
- [Private notes backup](#private-notes-backup)
- [Troubleshooting](#troubleshooting)

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| Python | 3.14, pinned in `.python-version` | The only version development and CI support |
| uv | current | Python dependencies, locked in `uv.lock` |
| Node.js | 20 or later | The CSS build and the accessibility scanner |
| PostgreSQL | 15, as in CI and production | The database. Tests run on it too, never on SQLite |
| Redis | 7 | Sign-in codes, rate limits and the worker's queue |
| Docker | with WSL on Windows | Redis locally, and building and checking the production image |

## Setup

```bash
cp .env.example .env                    # then fill SECRET_KEY, DATABASE_URL, FIELD_ENCRYPTION_KEY and the EMAIL_ values
uv sync                                 # Python dependencies from uv.lock
npm ci                                  # the CSS toolchain and axe-core, as package-lock.json pins them
npm run css                             # builds static/css/app.css
python -m playwright install chromium   # Chromium for the browser tests
python manage.py migrate
python manage.py seed_catalog           # 12 sample products, development only
python manage.py runserver              # http://127.0.0.1:8000
```

On Windows, `python` may resolve to the system interpreter: call the virtual environment's `.venv/Scripts/python.exe` directly. CI, on Linux, runs the same tools through `uv run`.

## Configuration

Settings come from the environment (`config/settings/{base,dev,prod}.py`), with `.env` filling only what the environment leaves unset. `manage.py` and pytest use `dev`; `wsgi` and `asgi` use `prod`, which refuses to start without its required values. `.env.example` documents every variable, and a test fails when one is missing from it.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django's signing key |
| `DEBUG` | Development only. Turns on the email previews and the style guide for everyone |
| `DATABASE_URL` | The PostgreSQL connection |
| `REDIS_URL` | Redis, for the cache and the worker's queue |
| `SITE_URL` | The scheme and host every absolute link is built on |
| `ADMIN_PATH` | Production only: the panel's path, one segment ending in a slash, never `admin/`. Development and tests serve the panel at `/admin/` |
| `FIELD_ENCRYPTION_KEY`, `FIELD_ENCRYPTION_KEY_FALLBACKS` | The key for encrypted columns, and retired keys that must stay readable |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL`, `SERVER_EMAIL`, `SUPPORT_EMAIL` | The SMTP relay and the shop's addresses |
| `EMAIL_QUOTA_RESET_DATE`, `EMAIL_QUOTA_STARTING_USAGE` | Align mail accounting with the provider's billing period |
| `DEMO_CATALOG` | Marks the catalogue as sample data. Always off in production |
| `SENTRY_DSN`, `SENTRY_ENVIRONMENT` | Error reporting |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SECURE_HSTS_SECONDS` | Production hosts and transport security |
| `LOG_LEVEL`, `GIT_COMMIT` | Logging, and the commit `/healthz` reports |

**The encryption key.** Every new order stores an encrypted support identifier, so configure this key before placing orders, even when no activation input is required. Generate one locally and keep it out of git and separate from `SECRET_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

To rotate it, move the current key into `FIELD_ENCRYPTION_KEY_FALLBACKS`, set the new one, and run `python manage.py reencrypt_fields --batch-size 200`, which is safe to repeat. Remove a retired key only when every row has been rotated and no backup still needs it. `python manage.py verify_decrypt` checks that every encrypted value decrypts and prints the key's fingerprint, never the key.

## Running locally

| Process | Command | Notes |
|---|---|---|
| Web | `python manage.py runserver` | Needs Redis. Serves `http://127.0.0.1:8000` |
| Worker | `python manage.py run_huey` | In a second terminal, against the same database. Run one consumer per database; stop it with Ctrl+C and let current work finish |
| Redis on Windows | `wsl -d Ubuntu-24.04 -u root -e bash -lc 'service docker start && docker start premshop-redis'` | Redis runs in Docker inside WSL and stops when WSL idles |

- `/healthz` reports whether the database and the cache are reachable, and the running commit. It does not check the worker; the panel's order list warns when the last expiry sweep is more than three minutes old.
- With `DEBUG` on, `/dev/emails/` renders every email, and `/styleguide/` shows the type specimens and the right-to-left regression strings. Without `DEBUG`, the style guide is for staff only and the email previews are a 404.

## Sample data

- `python manage.py seed_catalog` adds 12 sample products in four categories. It only adds what is missing; `--reset` deletes the seeded products and their rows first, including any edits made to them, and `--no-images` skips the brand marks.
- `--extended` adds 14 more, for 24 public products. It runs only with `DEBUG` and `DEMO_CATALOG` on, against a local database named `premshop_ui_review`, and never with `--reset`. While `DEMO_CATALOG` is on, every page shows a sample-data notice.
- Orders are placed through the storefront like a customer, or in a shell with `orders.services.place_order` and `OrderLine` values. A local order really sends its confirmation email when the SMTP settings are real, so use your own address.
- `python manage.py purge_guest_carts` removes guest carts inactive for more than 30 days; the worker runs it daily.

## Commands

| Command | Does |
|---|---|
| `python scripts/check.py` | The whole gate as CI runs it, except the Docker image job (about 7 minutes) |
| `python scripts/check.py --no-audit` | The same without `pip-audit`, which needs the network |
| `python scripts/check.py --docs` | For a change to Markdown and `docs/` only: the written-voice test. Anything else runs the whole gate |
| `python -m pytest apps/catalog -q -n auto` | One app's tests, one worker per core ([Parallel runs](#tests)) |
| `python -m pytest -k promo -q` | Tests matching a keyword; a handful runs faster without `-n` |
| `python -m pytest -m "not browser" -q -n auto` | Everything except the browser tests |
| `REQUIRE_BROWSER=1 python -m pytest -q -n auto` | As CI: a browser test that cannot start Chromium fails instead of skipping |
| `python scripts/ci.py` | Waits for every CI run on `HEAD` and prints one line per run |
| `python scripts/review_branch.py <branch>` | Lists what in a change needs a careful read |
| `npm run css`, `npm run css:watch` | Build the stylesheet once, or on every change |
| `python scripts/compile_messages.py` | Compile the panel's translations after editing a `.po` file |
| `python scripts/subset_fonts.py` | Subset the fonts after replacing one (`uv pip install fonttools brotli` first) |
| `python manage.py render_share_images` | Redraw the link-preview images and icons |
| `uv add <package>` | Add a dependency |

## Quality gate and CI

The gate is eight steps, defined in `.github/workflows/ci.yml` and mirrored by `scripts/check.py`; the two are kept in step:

1. `ruff check .`
2. `black --check .`
3. `mypy apps config`
4. `npm run css`
5. `python manage.py makemigrations --check --dry-run`: a model change without its migration. A new app without a migrations package is caught by `apps/core/tests/test_migrations_present.py`.
6. `pytest -q -n auto` with `REQUIRE_BROWSER=1`
7. `pip-audit --skip-editable`
8. `bandit -q -r apps config -x "*/tests/*"`. Bandit does not read ruff's `# noqa`: suppress a finding with `# nosec B###` and give the reason in an ordinary comment.

CI also runs an `image` job, which builds the production image and checks it ([Production image](#production-image)). It needs Docker, so `check.py` skips it. `check.py` prints one line per step and the full output only of a step that fails; `--verbose` prints everything.

A second workflow, `.github/workflows/codeql.yml`, runs CodeQL over the Python, the JavaScript and the workflow files on every pull request and on every push to `main`. It has no path filter, because it is a required check and one that never started would hold a pull request open. Its findings are in the repository's Security tab; a false positive is dismissed there with its reason.

Before pushing, run the fast check, `uv run ruff check . && uv run black --check . && uv run mypy apps config`, and the tests of the apps the change touches with `-n auto`. CI is the verdict.

**Branches and reviews.**

- `main` is the only long-lived branch. Every change reaches it through a pull request, and branch protection requires the lint, test, audit, image and CodeQL checks to pass on a branch that is up to date with `main`.
- A migration, money or sign-in code, or work across several apps gets a second reader before it merges. `scripts/review_branch.py <branch>`, or `HEAD --base <commit>` for commits already on `main`, lists what needs a careful read: an applied migration edited, a migration that moves or removes data, tests removed or skipped or with changed assertions, a change to the gate, a raised query budget, a new dependency, money or sign-in code, and a production change (code and templates, not tests, migrations, translations or documents) over 600 lines in money, sign-in or credential code or 1,500 elsewhere, past which one review misses too much and two readers split it by area.
- Commit messages say what changed and why.

**Reading CI.** The verdict is the run for the pushed commit, not the newest run. `python scripts/ci.py` waits for every run on `HEAD` and exits non-zero unless all succeeded. By hand:

```bash
gh run list --commit "$(git rev-parse HEAD)" --json headBranch,status,conclusion
```

`gh run watch --exit-status` can gate a script, but not through a pipe: `gh run watch … | tail -2` reports the exit status of `tail`, so a failed run reads as a pass.

## Documentation

The README lists every document. They follow four rules:

- **One home per fact.** Each fact is stated in one document; the others link to it.
- **Describe what is built.** When behaviour changes, the document that owns it is edited in place to state the current rule. Designed but unbuilt work appears only where it shapes what exists, marked as not built and linked to its decision record.
- **Decisions are not rewritten.** A decision record changes only by a dated amendment at its end, and a replaced decision is marked superseded and kept ([decision records](decisions/README.md#writing-a-record)).
- **Plain technical English.** Persian appears only where the subject is Persian text. `apps/core/tests/test_written_voice.py` checks every tracked text file.

## Tests

- **Database.** Tests run on the PostgreSQL in `DATABASE_URL`, because row locks and constraints are part of what they test. `apps/conftest.py` gives every test an in-memory cache and a fast password hasher, so no test needs a running Redis or pays the real hasher's cost.
- **Layout.** Tests live under `apps/` (`testpaths = ["apps"]`), including the settings tests in `apps/core/tests/`. Fixtures are plain pytest fixtures. `time-machine` moves the clock for expiry windows and deadlines.
- **Browser tests** (marker `browser`) drive Chromium through Playwright against pytest-django's live server, sharing one `browser` fixture. They load the built `static/css/app.css`, so a stale build is what they test. Without Chromium they skip, unless `REQUIRE_BROWSER` is set, as it is in CI and in `check.py`.
- **Parallel runs.** `-n auto` (pytest-xdist) starts one worker per core. pytest-django gives each worker its own test database (`test_premshop_gw0`, `_gw1`…) and its own live server on a free port, and each worker launches its own Chromium per module, so workers never share rows, ports or pages. What they can share is anything outside the process: a test writes to `tmp_path`, never to a fixed path, and never relies on another test having run first. A browser test waits for a state, not a duration: under load a fixed wait reads a smooth scroll or a transition mid-flight. CI runs the whole suite this way; a single test or a `-k` selection is quicker without `-n`, which spends a few seconds starting the workers.
- **Accessibility.** `apps/core/tests/test_accessibility_browser.py` runs axe-core on the key pages at 390 and 1280 pixels wide: the storefront as a visitor, the account page signed in, and the panel's product list and form. Any serious or critical WCAG 2.2 A or AA violation fails. It also holds fields, code cells and the header search to 3:1 contrast, the search placeholder to 4.5:1, and every focus ring to 3:1. `apps/catalog/tests/test_admin_browser.py` scans the rest of the panel. The list of allowed exceptions is empty; an exception would name one rule, page and element with its reason.
- **Query budgets.** Six queries on the home page, five on a category page and nine on a product page with related products. The legal-page links in the footer cost one query however many pages are published, tested separately so a query per link cannot hide inside a budget.
- **Written voice.** `apps/core/tests/test_written_voice.py` scans every tracked text file for emoji, brochure adjectives, tutorial and essay openers, and attribution lines.
- **Persian copy.** `apps/catalog/tests/test_storefront_copy.py`, `apps/core/tests/test_persian_register.py` and `apps/accounts/tests/test_copy.py` hold the [voice and glossary](design-language.md#voice) rules that a pattern can check.

## Front-end assets

- **CSS.** Tailwind v4 builds `static/css/app.css`, a git-ignored output, from `static/css/input.css`. It scans `templates/` only and skips the panel's `templates/admin/` and `templates/unfold/`, which Unfold styles; a class used for the first time does nothing until `npm run css` runs. The storefront layer (`storefront.css`), the order pages (`orders.css`) and the panel (`admin.css`, `admin-experience.css`) are plain stylesheets.
- **Fonts.** Estedad and Vazirmatn are self-hosted and already subset: `scripts/subset_fonts.py` keeps the codepoints the interface renders (basic Latin, the Arabic block that carries the Persian letters, ZWNJ and the bidi marks, punctuation and the guillemets) and the `locl`, joining and mark-positioning features, cutting 313 KB to 129 KB. After running it, look at `/styleguide/`: a subset that loses a joining feature raises nothing and renders Persian as disconnected letters. Latin is kept in both faces, so a brand name inside Persian text does not fall back to another font mid-line.
- **Fallback metrics.** `Estedad Fallback` is the local Tahoma at `size-adjust: 76.5%`, fitted so text does not jump when the web font arrives. Re-fit it when the home headline changes or a face is replaced, by rendering the same string in both and comparing their widths. A wrong value costs layout shift, not an error.
- **Brand marks** are SVG files kept in `apps/catalog/marks/`, with their sources in `SOURCES.md`; `seed_catalog` copies them into `MEDIA_ROOT`. Only the `--extended` sample products download theirs, and each download must contain `<svg` and a `viewBox`. The running site never loads a mark from a CDN, because most CDNs are unreachable from Iran.
- **Share images and icons.** `python manage.py render_share_images` draws `static/img/share.png` (1200×630), a `share/<slug>.png` per public product with `--products`, and the favicons, through Playwright's Chromium. Run it after changing the artwork, the fonts or the copy shown on the images, then inspect and commit the results; it is not part of the build.
- **Compression.** `GZipMiddleware` compresses responses. In production nginx serves `/static/` compressed, with `Cache-Control: public, max-age=31536000, immutable` for the hashed files. Fonts are already compressed.

## Translations

The panel's own strings are written in Persian in the code and wrapped in `gettext_lazy`. `locale/en/` gives their English for the panel's language switch, and `locale/fa/` rewords Django's and Unfold's strings. After editing a `.po` file, run `python scripts/compile_messages.py`. `apps/catalog/tests/test_panel_translations.py` fails when a `.mo` file does not match its `.po`, or a Persian string has no English. The compiler refuses a line it cannot read rather than dropping it, and a plural's `msgid` must match the template's text exactly, indentation included.

## Production image

Production runs one image per commit, built from the `Dockerfile`: the CSS, the locked dependencies without the development group, the static files collected under hashed names, gunicorn as a non-root user, and the commit in `GIT_COMMIT`, which `/healthz` reports. `.dockerignore` is an allowlist, so `.env`, local notes, backups, media and caches stay out of the image. `deploy/compose.yaml` runs it as `web` and `worker` beside PostgreSQL 15, Redis behind a password and nginx, the only service with a published port; a one-shot `static` service copies the image's static files into the volume nginx serves.

To build and check it locally, open WSL (`wsl -d Ubuntu-24.04`), change to the repository under `/mnt/c/`, and with the commit from `git rev-parse HEAD`:

```bash
docker build --build-arg GIT_COMMIT=<commit> --tag premshop:<commit> .
bash deploy/smoke.sh premshop:<commit> <commit>
bash deploy/rehearse.sh premshop:<commit> <commit>
```

- **`deploy/smoke.sh`** writes throwaway secrets and a self-signed certificate to a temporary directory and starts the stack as the Compose project `premshop-smoke` on ports 8443 and 8080. Over HTTPS through nginx it checks that `/healthz` reports the commit; that the home, a category and a product page carry a hashed stylesheet; that the panel answers at `ADMIN_PATH` while `/admin/` is a 404; that forged `X-Forwarded-For` and `X-Real-IP` headers cannot get around the search limit; that Redis refuses a client without its password; that the worker has neither restarted nor logged a traceback; that no tracking token reaches a log; and that every page is the 503 maintenance page while `web` is stopped. It removes everything, pass or fail, and never touches the development server or its database.
- **`deploy/rehearse.sh`** drives `scripts/deploy.py` the way the runbooks do, as the Compose project `premshop-rehearsal` on ports 8543 and 8180: a first deploy; a deploy of a broken image, which must return to the previous one by itself; a deploy refused because its compose file changes `db` and `redis`, with the database untouched and no backup taken; a deploy carrying a changed nginx file, then a rollback; a restore into fresh volumes, which must decrypt the seeded input with the right key and keep the site closed under a wrong one; and a restore of a backup taken before any order, which must fail without `--allow-empty`.

A stack kept running reads one environment file outside the repository, given with `--env-file`: every variable `prod.py` requires, including `ADMIN_PATH`; `POSTGRES_PASSWORD` and `REDIS_PASSWORD`, in hex, because Compose puts them into URLs unescaped; `PREMSHOP_IMAGE`; `PREMSHOP_TLS_DIR`, holding `fullchain.pem` and `privkey.pem`; and `PREMSHOP_ENV_FILE`, the file's own path. `deploy/smoke.sh` is the worked example. HSTS starts at one hour with preload off.

## Private notes backup

`docs-local/` is a git-ignored folder of private business notes with its own git history. `scripts/backup_docs_local.py` backs it up after every commit made in it, through a post-commit hook that `python scripts/backup_docs_local.py setup` installs.

- **What a backup is:** a git bundle of the folder's whole history, encrypted with gpg (AES-256) under a passphrase kept in `~/.premshop-backup/passphrase` and, off the laptop, in a password manager and on paper. The script strips a byte-order mark from the passphrase file and refuses one shorter than 20 characters.
- **Where it goes:** the `docs-local-backup` branch of this repository, one commit replaced each time, holding no workflow files so it starts no CI; and a local folder that keeps the last five, whose path is read from `~/.premshop-backup/mirror` (default `~/premshop-backups`). The local copy protects against a mistake inside the folder; the GitHub copy is the one that survives a lost laptop.
- **Checks:** before copying, the script confirms that the file decrypts to the bundle and that a wrong passphrase does not open it. A failure prints `BACKUP FAILED` with the reason; the next successful backup catches up, since each holds the whole history. Uncommitted edits are not in a backup.
- **Restore drill:** `python scripts/backup_docs_local.py verify` restores from the published branch into an empty folder with an empty home directory and decrypts every local copy. `verify --ask` prompts for the passphrase instead of reading the stored file, which is the only way to test the copies kept off the laptop. Run it after changing anything about the backup.
- **Passphrase exposure:** a force-push retracts nothing on GitHub; every replaced backup commit stays fetchable by its hash. One leaked passphrase therefore exposes the whole archive history, and a new passphrase protects only the backups made after it.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The sign-in page says «ورود فعلاً ممکن نیست» | Redis is down. Start it (see [Running locally](#running-locally)) |
| A class added to a template has no effect | `static/css/app.css` is a build output: run `npm run css` |
| A template change does not show | `runserver --noreload` serves the templates it started with. Restart without it |
| `uv sync` fails halfway on Windows | A running server holds a compiled module (Pillow, psycopg) open. Stop every `runserver`; if the package is already broken, run `uv sync --frozen --reinstall-package <name>` |
| Persian prints as garbage in a terminal | The console code page is cp1256. Set `PYTHONIOENCODING=utf-8`; in PowerShell 5.1, pass `-Encoding utf8` to `Get-Content` |
| Markdown or Persian text arrives damaged through a shell | Git Bash heredocs break on Persian mixed with quotes, and in a PowerShell double-quoted string a backtick is an escape character. Write such content with an editor or a script file |
| A browser test is skipped | Chromium is not installed: `python -m playwright install chromium` |
