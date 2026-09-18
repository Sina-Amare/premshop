# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read `docs-local/progress.md` first, every session (a newest-first log per step, as the working agreement §6 prescribes — keep that shape), then the "Where you are", "Now" and "Log" sections of `docs-local/learning.md`; its concept map and answer keys are opened only when writing a brief or grading. Continuity lives in files, not in memory.

The parent folder's `CLAUDE.md` is the generic template this file was built from, and Claude Code loads it too. Where the two differ, **this file wins** — in particular, progress, the roadmap and open questions live in `docs-local/` here, not `docs/`. The owner's methodology behind both is in `../AI Backend Development — Master Project & Learning Operating System.md` and `../Documentation Discipline.md`; this file is how they apply to this project.

## What this is

An Iranian web shop (premshop.ir) selling digital subscriptions and accounts — run by **one person**, serving a few dozen customers, moving **real money** through an Iranian payment gateway with an operator-only manual fallback, and storing **other people's account credentials**. The scope is genuinely small. The stakes are not. The engineering bar is set by the stakes, not the scope.

## Scope versus quality

Cutting features is fine. Cutting craft is not. The test for which is which is **reversibility**: a feature we skip can be added later; a wrong data model, a plaintext credential, an unguarded money path cannot be cheaply unwound. When deciding how much to build, ask "is this decision reversible?" — not "do we need this now?". The irreversible things were decided once, carefully, and live in `docs/decisions/`.

## Never traded away for speed

- Field-level encryption for delivered credentials **and** `customer_input` — with the key escrowed offline before any real delivery. A restore that can't decrypt is a failed restore. (ADR-0007)
- Server-side price calculation, server-side verification only — never a callback's word — with an amount comparison that fails the payment on mismatch, idempotent confirmation through the one shared entry point, the inquiry sweep that catches lost callbacks, and a `Refund` ledger row behind every REFUNDED status. (ADR-0005, 0019)
- Status transitions only through service methods; every transition writes its audit row in the same transaction. (ADR-0003)
- Object-level ownership checks — logged-in is not authorized.
- No credential *values* in any message, log, or error report — ever. The single-use magic link is the one sanctioned convenience, and only with its full constraint list. (ADR-0008)
- Tests on every money and auth path; reversible migrations; toman everywhere with rial only at the two tested boundaries.
- Persian UI text, English code and logs. No `letter-spacing` on Persian, no italics, no Latin placeholder text in the UI.

## Deliberately not built — don't helpfully add these back

No DRF/API before phase 3. No `IN_PROGRESS` or `REPLACED` status. No inventory, supplier, or FX models. No RBAC, wallet, or ticket system. No Postgres FTS. No identity tables. No PaymentProvider interface until a genuine *second* gateway exists — Zibal is the one implementation, not the first of two (ADR-0013). Each has an ADR with the condition that would flip it (see ADR-0014); meeting the condition is the only way back in.

The cart, discount codes and promotional pricing **are in scope** and left this list — ADR-0018, ADR-0020, ADR-0021. Promotional pricing is built (S3); the cart and discount codes are built at S4. Don't remove them on the strength of an older note.

## How we work

- **One step at a time**: short plan → owner approval → build → review the *result*, not the blueprint. Never start building before the step is explained in plain language **and** the owner has explicitly said go. Silence is not approval.
- **A step plan contains only what would be a bug to change mid-work** — goal, what changes, done criteria, named tests, contract touches. File names, directory shapes, tool arrangements are *taste*: decide them while coding, don't ask, don't pre-specify. If you find yourself arguing both sides of a detail in a plan, the detail is below plan altitude — cut it.
- **The contracts** — `docs/data-model.md`, `docs/state-machine.md`, `docs/services-and-modules.md` — exist so the same decision isn't remade three ways across sessions. Each hardens at the step that builds it. If mid-build you discover a contract is *wrong*, **stop and raise it**; changing a contract is a conversation. Changing a filename isn't.
- **When the owner must do something (a signup, a token, a payment), write a walkthrough — not a checklist.** One numbered step = one action; say what they will *see* on screen (button label, field name), what to type or click *exactly*, an if/then for anything ambiguous, where a VPN is and is not needed, where money is and is not required, and precisely what to send back (warning when a value is displayed only once). Banned: "sign up at X and do Y", "follow their setup flow", any user action compressed into one sentence with several verbs. This applies to the *last* ask in a long message as much as the first — running long is not a licence to compress the tail. Prefer asking for a credential that lets you do the work over asking them to click through it.
- **Every step gets a briefing before and a report after.** The *plan* stays lean (only what would be a bug to change mid-work); the *briefing and report* go deep. Both are written in plain language a non-specialist can picture — no term used without being explained, and an everyday analogy wherever one genuinely fits.

  **Briefing (before building, requires an explicit go):** what we're building and why it matters *for this shop specifically* — never a generic rationale; then piece by piece — what it is in plain words · a real-world analogy · why our project needs it · what the alternatives were · why the chosen one won; then what the owner will be able to see and do at the end; then what could go wrong and what to watch for; then what is needed from the owner.

  **Report (after building):** what actually got built in plain language · a walkthrough of the load-bearing code, file by file, saying what each piece does and why it is shaped that way · the taste-level decisions taken while coding without asking · separately, every cut or deviation from the planned scope, flagged for sign-off · every bug found and what it taught · the terms that appeared, defined · what this now makes possible and what is still open.
- **Explanations name the concepts; the deep teaching happens elsewhere.** Whenever a concept is in play, give its standard name, one line on why it applies here, and the alternative that lost. Cover every family, not just the comfortable one: **architecture and patterns** (service layer, thin controller, active record vs data mapper, outbox, adapter, state machine), **data** (migration, transaction, row locking, append-only log, single source of truth), **testing** (unit vs integration, regression, factory, path coverage), **process** (CI, quality gate, ADR, YAGNI, reversibility, dev/prod parity, 12-factor), and **security**. Name the pattern even when the code works without it being named — the name is what transfers to the next codebase. The owner studies with a separate teaching model, so depth in briefings and reports stays about *this shop's* choice — what, why here, what lost — and general theory goes only into the step's study brief (below). The report's list of terms is the one-line glossary that brief draws from. The goal is transferable judgment — never jargon for its own sake, never over-engineering dressed up as education.

## The owner's side of every step

The owner does not write the code, but must understand, manage and defend all of it — that is the difference between owning this project and vibe-coding it. This agent builds, and is **also responsible for running the loop below so no part of it is quietly skipped** — and for keeping it light: a ritual that adds nothing gets dropped, and trivial changes get none of it. The ledger is `docs-local/learning.md`: study briefs, check-back questions with answer keys, the parking lot, and the current independence stage.

Each piece has a fixed place in the step, so it cannot drift:

1. **The owner's take comes first.** Before briefing any step that touches data, money, security or a contract, ask for their take in a few lines: what it needs, what is risky, what they would test. If they lack the fundamentals to have one, give the minimum context first — never make them guess blind. Then brief, and name where their take and the briefing differ and why. The gap is the lesson; a gap that shrinks over steps is the evidence. **The same for a meaningful bug:** ask for their two or three likely causes before giving the diagnosis, unless the cause is obvious.
2. **One prediction**, on the same steps: the briefing asks one "what do you expect happens when…" about the riskiest piece; the report says what actually happened.
3. **A study brief closes every report.** Up to four concepts the step actually used — "nothing new to study" is a valid brief — each with why it matters here, the depth needed (L1 know what and why · L2 recognise and use · L3 debug it and defend the trade-off), the question it must answer, what *not* to go into, a stop condition, and the project files to read. Written to paste straight into the teaching model; copied into `learning.md`.
4. **Check-back before the next go.** The owner explains one or two load-bearing pieces in their own words, graded against the code. Answer keys in `learning.md` name the file and the function or constraint; line numbers drift, so a key is refreshed just before it is used. Plus **one owner-run check against a real system** — the Actions run, an email's "Show original", a database row. Someone other than the agent has to look.
5. **From stage 2, the owner proposes the next step** before the agent recommends one, whenever the choice is not forced; the agent critiques.

**Balancing is this agent's call, said out loud:**

- *Under-learning:* if the owner cannot explain a concept the next step builds on (priority *high* in the ledger), recommend a bounded study pass before that step's go. A skipped check-back is recorded as skipped, never dropped; two skipped in a row before a money or security step means pause and say so.
- *Over-learning:* more than four open study items, or two turns of explanation with nothing built, means say "enough for now", park the rest with the condition that would bring it back, and return to building.
- *Independence has three stages*, recorded in `learning.md`. **Stage 1:** the owner gives a take, the agent leads and recommends. **Stage 2:** the owner proposes the step and the next step, the agent critiques — and sometimes asks the owner to name the concept in play before naming it. **Stage 3:** the owner leads, the agent reviews. Move up when the owner's take matches the briefing on the load-bearing points for about three steps running.

## A step is done when

The working agreement §5 checklist, as it actually applies here; the build plan's step-specific items are additions, not replacements.

- `scripts/check.py` passes **and** the GitHub run for the pushed commit reads `success`.
- Tests cover the error paths, not only the happy one, and every money/auth path; every bug found got a regression test that was seen to fail.
- Anything crossing a real boundary (SMTP, DNS, the gateway, a browser) was exercised for real, not only mocked. Migrations were reviewed and are reversible.
- Pages were screenshotted and reviewed by this agent at phone width and desktop in every state; the Persian copy was read as a Persian reader would.
- No `TODO` or half-finished code remains; every cut or deviation from the step's build-plan scope was flagged to the owner for sign-off, not just listed among taste decisions.
- **Documentation impact checked:** each document the change could make stale was updated in the same commit or confirmed unaffected — at least the three contracts, the ADRs and their index (`docs/decisions/README.md`), `docs/README.md`, `design-language.md`, `notifications-and-bot.md`, `.env.example`, `README.md` and this file. A decision someone will later ask "why?" about got a new ADR. A doc that contradicts the code is investigated, never silently resolved either way. A superseded ADR is marked and linked, never rewritten.
- `docs-local/progress.md`, `open-questions.md` and `learning.md` are current; anything deliberately deferred is recorded with the condition that brings it back.
- The owner has reviewed the result, and the check-back is done or recorded as skipped.

**Before a session ends:** nothing is left uncommitted without saying so in the last message, and the next action can be recovered from the files alone.

## How things actually break here

Every one of these has already happened on this project, and every one was **silent** — which is what earns it a line in the file read first each session.

- **A name that matches nothing falls through instead of erroring.** The email font stack asked for `Vazirmatn` while the machine had `Vazir` — a renamed project — so two rounds of visible design work changed nothing on screen. Verify what a value **resolved to**, never that it was set.
- **A guard is only as wide as the tokens someone thought to list.** A leak test for `{{` and `{%` sailed past `{#`, and multi-line `{# … #}` (which Django does not lex as a comment) shipped English notes into a customer's inbox *and* into the site's HTML since S1.
- **Green tests do not mean a working system.** Tests passed while `prod.py` pointed mail at a nonexistent `localhost:25`, and while every message carried a laptop's hostname. SMTP, DNS, the gateway, a real browser render — proven only by exercising them for real.
- **Verify against the system of record, not the command's output.** A push that printed nothing had silently done nothing.
- **Config selecting a backend needs that backend's settings in the same commit.** Latent bugs in unreached code are still bugs; they wait.
- **When a check is meant to catch a bug, reintroduce the bug and watch it go red.** A guard that has never failed is untested.
- **Look at the artefact before researching the world.** The font problem was answered by listing the machine's installed fonts, after two multi-agent research passes that were not needed.
- **A local pass is evidence; the CI run is the verdict.** CI was red on GitHub for seventeen commits while every step report said "all checks green". Three independent causes, all invisible locally: `bandit` was in the workflow but never run by hand; `black` was run on `apps/` instead of `.`; and four tests passed only because the developer's machine had a running Redis and relay credentials in `.env`. Run `scripts/check.py`, and **after every push read the run's conclusion from GitHub** before reporting the gate as green.
- **A comment that promises a behaviour is not a test of it.** Three auth bugs, found 2026-09-18 and still open in `progress.md` until fixed, sit under comments that claim the opposite: "a failure here must not fail the login" (it does), "a wrong guess must not buy time" (in production, it resets the clock), "a nonexistent account does not answer faster" (existing ones answer slower). Each promise in a comment is a test that has not been written yet.
- **A test must build the state it depends on.** If it passes because of something in `.env` or a daemon that happens to be up, it is testing the machine. `apps/conftest.py` forces an in-memory cache for every test for this reason.

## Disagreement is part of the job

If something is wrong, or there's a simpler way to the same result, say so **before** building — silence-and-comply is not help. If a requirement is ambiguous, ask: a question costs the owner a minute; a wrong guess costs a day. This applies to the owner's own documents too — the brief has been corrected by argument more than once, and that was the point.

## The visual position

The shop's biggest obstacle is customer doubt about actually receiving the product. Looking like a generated template directly costs sales, so the visual bar is a **business** requirement paid incrementally on every page — never a polish pass at the end. Work as a creative senior UI/UX designer and developer: every visual choice argued from *this business's* needs, never defaulted from what AI-generated sites usually look like — AI-stereotype palettes and layouts are banned on principle, not just the specific ones listed below. Banned outright: purple-blue gradients, glassmorphism, glowing dark heroes, `rounded-2xl` everywhere, emoji-as-icons, entrance animations, "قدرت‌گرفته از AI" copy. The system in force — Estedad over Vazirmatn, teal accent on warm stone, **amber reserved exclusively for time pressure** — is `docs/design-language.md` (ADR-0016). In the operator panel the only loud thing is time remaining.

**Look at it yourself before asking anyone else to.** Render the page (or the email), **take a screenshot, and review it as a senior designer would** — spacing, hierarchy, alignment, type, and every state: empty, error, loading, long content, narrow viewport. For RTL work also check bidi flips on mixed Latin/Persian runs, digit rendering, and descender collisions. Fix what you find, *then* say "take a look". Every defect the owner has to point out is a wasted round trip, and the ones a screenshot would have caught are the least excusable kind.

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
- **After a push**, the verdict is the run *for that commit*: `curl -s "https://api.github.com/repos/Sina-Amare/premshop/actions/runs?head_sha=$(git rev-parse HEAD)"` — wait until `status` is `completed`, then read `conclusion`. The newest run overall may belong to an older commit or still be running. `gh` is not installed.
- **Tests** run against the real local PostgreSQL (`DATABASE_URL`), never SQLite; the cache is forced to in-memory. The three `browser` tests drive Chromium through Playwright (`python -m playwright install chromium` once) and skip themselves when it is absent, as in CI.
- **Redis** runs in Docker inside WSL and stops when WSL idles: `wsl -d Ubuntu-24.04 -e bash -lc 'sudo service docker start && docker start premshop-redis'`. Then `/healthz` must report both `database` and `cache` ok. A login page showing «ورود فعلاً ممکن نیست» means Redis is down.
- **`static/css/app.css` is a gitignored build artifact.** Tailwind v4 scans `templates/` only (`source(none)` in `input.css`), so a class used for the first time in a template does nothing until `npm run css` runs.
- **Never `runserver --noreload` while editing templates** — it serves the old ones, and a screenshot of a stale page looks like a fix that did not work.
- **Writing files that mix Persian and quotes:** Git Bash heredocs break on them, and tool layers turn `\n` and `\uXXXX` escapes back into the characters. Use the Write tool or a script file; build a backslash with `chr(92)` when an escape must survive. The console is cp1256: set `PYTHONIOENCODING=utf-8` before printing Persian, and in PowerShell 5.1 pass `-Encoding utf8` to `Get-Content`.

## Architecture as built

Shipped so far: `core`, `accounts`, `catalog`. The full map, including the apps not built yet, is `docs/services-and-modules.md`; what follows is what spans files and is not obvious from any one of them.

- **Imports run one way.** `core` imports nothing of ours; `accounts` and `catalog` import only `core`. Still to come, in order: `cart` and `orders` (siblings that never import each other), `payments` (the one place they meet), `notifications`, `panel`, `cms`. Anything two apps need lives in `core` — that is why the rate limiter moved out of `accounts` the day search needed it.
- **Views are thin controllers**: parse a form, call one service, render. Rules live in `services.py`, and domain exceptions carry the Persian sentence the customer sees, because the message is part of the behaviour.
- **Settings** are `config/settings/{base,dev,prod}.py`, entirely environment-driven. `.env` is loaded with `setdefault`, so the real environment always wins. `manage.py` and pytest default to `dev`; `wsgi`/`asgi` default to `prod`, which **refuses to boot** without `ALLOWED_HOSTS`, the relay credentials, and a From address at `premshop.ir`.
- **One function per rule — call it, never re-derive it:**
  - every price shown or charged → `catalog.pricing.effective_price`
  - every visitor-facing product query → `Product.objects.public()` (drafts must not leak through a list, a search, or a guessed URL)
  - both sides of search → `catalog.search.normalize_for_search`
  - every email → `core.email.send_templated_email`; a message is three files under `templates/email/`, and needs an entry in `EMAIL_PREVIEWS` (`core/views.py`), which feeds both `/dev/emails/` and the template tests
  - Persian digits, toman, Jalali dates → `core.formatting`, exposed as filters by `{% load fa %}`
  - every money column → `MoneyField()` in `catalog/models.py` (whole toman, exact decimal)
- **The cache is part of authentication.** Login codes and every rate-limit counter live in Redis through Django's cache API. `CacheUnavailableMiddleware` turns a Redis failure anywhere into a Persian 503 instead of a traceback; there is deliberately no failing open, because that would mean unlimited guesses at a six-digit code.
- **Template traps, each of which has shipped once:** `{# … #}` is single-line in Django — a multi-line one is emitted as text, so notes use `{% comment %}` (a test scans every template); a template using the `fa` filters without `{% load fa %}` is a 500 that no view test sees; an `<input>` without `field__input` has no border, because Tailwind's preflight zeroes them.
- **CSS is component classes** (`.card`, `.fact`, `.pcard`, `.field__input`, …) built from the tokens in `static/css/input.css`, not utility classes in markup. Emails are a separate medium: table layout, inline styles, their own font stack (ADR-0023).
- **Tests live under `apps/`** because `testpaths = ["apps"]` — including the settings tests, in `apps/core/tests/`.
- **The admin is django-unfold**, which must precede `django.contrib.admin` in `INSTALLED_APPS`. It is the operator's tool until S7 builds the panel.

## Where to look things up

Two locations, deliberately split.

**In the repo — `docs/`, the engineering reference anyone may read:** *why it's built this way* → `docs/decisions/` (ADRs) · *what data* → `data-model.md` · *what's allowed to happen* → `state-machine.md` · *who owns what code* → `services-and-modules.md` · *what messages go out* → `notifications-and-bot.md` · *what it looks like* → `design-language.md`.

**Outside the repo — `docs-local/`, gitignored:** the product brief (cost prices, margins, supplier arrangements), the working agreement, the build roadmap and its business gates, `progress.md`, `open-questions.md`, step plans, and the whole design discussion. These are internal or commercially sensitive; conclusions from them live in ADRs, which is what the repo carries.

When a decision made in `docs-local/` matters to someone reading the code, it belongs in an ADR — that is the bridge between the two.
