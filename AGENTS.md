# AGENTS.md — how work happens in this repository

Read this file at the start of every session. It is short on purpose: everything here is a trigger you act on without being asked. A process that lives only in someone's head does not survive context resets or busy weeks; a process that lives here does.

## 0. Why this file exists

- Software built with coding agents is often solid and unexplained: it works, and nobody involved can say why it is shaped the way it is. This file exists so that this repository produces two outputs, not one: working software, and engineering judgment that stays with the owner after the session ends. Neither is optional.
- Two failure modes to avoid: vibe-coding (building without understanding) and perfectionism (planning, documenting, abstracting beyond what the problem needs). Optimize for reliable progress with understanding attached.
- Depth of reasoning follows reversibility: decide cheap-to-reverse things fast; reason carefully about data models, ownership boundaries, auth, public contracts, destructive migrations.
- The engineering bar follows the stakes of failure, not the size of the project.
- Every trigger below carries a "because." Use it to handle situations this file did not anticipate. Use judgment about proportion. Do not use judgment to skip the loop silently; there is an explicit way to skip it (section 3, "just build").
- The repository is the source of truth. Conversation memory is not.

## 1. This project

- What it is: PremShop (premshop.ir), an Iranian web shop selling digital subscriptions and accounts, run by one person. A Django modular monolith, server-rendered Persian RTL, on PostgreSQL and Redis.
- Who it is for: a few dozen Iranian customers, many of them from the owner's existing Telegram sales, and the one operator who delivers every order by hand.
- Stakes if it breaks: real money (an Iranian payment gateway, with an operator-only manual fallback) and other people's account credentials, stored for delivery. Lost money, leaked credentials, a customer who paid and never received. The scope is small; the stakes are not, and the bar follows the stakes.
- Commands — run: `.venv/Scripts/python.exe manage.py runserver` (needs Redis) · test: `.venv/Scripts/python.exe -m pytest -q` · the whole gate, exactly as CI runs it: `.venv/Scripts/python.exe scripts/check.py`. A local pass is evidence; the verdict is the GitHub run *for the pushed commit*, read by `head_sha`. Setup, every other command and the environment's traps: `docs/development.md`.
- Non-negotiables:
  - No secrets in git. `.env` stays local; `.env.example` documents every variable (a test enforces it).
  - Authorization is enforced server-side, per object — logged-in is not authorized.
  - Field-level encryption for delivered credentials **and** `customer_input`, with the key escrowed offline before any real delivery. A restore that can't decrypt is a failed restore. (ADR-0007)
  - Server-side price calculation; a payment is confirmed only by server-side verification, never a callback's word; an amount mismatch fails the payment; confirmation is idempotent through one shared entry point; an inquiry sweep catches lost callbacks; a `Refund` row stands behind every REFUNDED status. (ADR-0005, ADR-0019)
  - Status changes only through service functions, each writing its audit row in the same transaction. (ADR-0003)
  - No credential *values* in any message, log or error report — ever. The single-use magic link is the one sanctioned convenience, and only with its full constraint list. (ADR-0008)
  - Tests on every money and auth path; reversible migrations; toman everywhere, with rial only at the two tested gateway boundaries.
  - Persian UI text, English code and logs. No `letter-spacing` on Persian, no italics, no Latin placeholder text in the UI.
- Deliberately not built — don't helpfully add these back: no DRF/API before phase 3; no `IN_PROGRESS` or `REPLACED` status; no inventory, supplier or FX models; no RBAC, wallet or ticket system; no Postgres full-text search; no identity tables; no PaymentProvider interface until a genuine *second* gateway exists (ADR-0013). Each has the condition that would bring it back in ADR-0014, which is this project's "not building yet" list. The cart, discount codes and promotional pricing **are** in scope (ADR-0018, ADR-0020, ADR-0021).

## 2. Files you maintain (create them when missing; the owner never writes them)

Project state lives in `docs-local/`, which is gitignored: it holds commercial detail (costs, margins, suppliers) and working notes. It has its own local git history — commit there after editing it (`git -C docs-local log`). `docs/` is the public engineering reference. When a decision made in `docs-local/` matters to someone reading the code, it becomes an ADR.

| File | What it holds |
|---|---|
| `docs-local/progress.md` | Current state in plain language at the top, then a newest-first log per step. Update after every completed step, not only at session end. |
| `docs-local/learning.md` | The tour of the built code, study briefs (before the next step / later), check-back questions, decision-journal entries, terms, skip counter. The owner pastes from it into a separate tutor chat that cannot see this repo. |
| `docs-local/learning-reference.md` | Answer keys for the check-backs, and the concept map. Opened only to grade a check-back or write a brief. |
| `docs-local/build-plan.md` | The blueprint's job: the steps in order, each step's scope, done-when and gates. It points to `docs/` for contract detail and never restates it. |
| `docs-local/open-questions.md` | What waits on the owner, and what it blocks. |
| `docs/decisions/NNN-title.md` | One note per hard-to-reverse decision (ADRs), indexed in `docs/decisions/README.md`. |
| `docs/README.md` | The map of `docs/`, including which document owns which kind of fact. |
| `docs/development.md` | Setup, commands, environment traps, and how the built code hangs together across files. |

Keep them true. Stale documentation is worse than none. When docs and code disagree, investigate, then fix the side that is wrong — a missing constraint the contract requires is a migration and a test, not a doc edit.

## 3. Triggers

**Session starts.** Read `docs-local/progress.md`, then `docs-local/learning.md` — not `docs/progress.md`, which does not exist here. In at most five lines: milestone, current task, blocker, next action, open question. Ask: continue or redirect? Read only the parts of the repo the current task touches.
Because: the files are our shared memory; rereading everything wastes the session.

**Before building any step — including a bug-fix round.** Explain it in plain language and wait for an explicit go from the owner. Silence, enthusiasm or an adjacent answer is not a go. Inside an approved step, small reversible decisions need no asking. "just build" remains the owner's explicit way to skip the loop.
Because: this is the owner's standing rule for this project, and it overrides any lighter default — nothing gets built that the owner has not understood first.

**No blueprint exists.** Interview the owner: what, for whom, stakes, constraints, what they already know, smallest useful first capability. Then propose the blueprint and ask for their take before writing it. Everything not in the blueprint is decided during the work.
Because: designing distant architecture before the problem justifies it is where perfectionism starts.

**Section 1 still has `{…}` placeholders.** Fill them yourself: what, who, and stakes from the blueprint interview; commands and non-negotiables from the actual toolchain once it exists, after verifying the commands run. Show the owner the filled section for a yes/no. Do not carry placeholders past milestone 1.
Because: the owner never hand-edits process files; you maintain them.

**A step will touch data, security, money, or a contract** (schema, API, state machine, auth, external integration). Before briefing, ask: "In three lines, what would you do and why?" If the owner clearly lacks the fundamentals, give the minimum context first. Then brief your plan (goal, constraints, done-when, tests, risks) and name where you differ. Ask for one prediction about the riskiest piece; the report says what actually happened. For a meaningful bug, ask for the owner's two or three likely causes before giving the diagnosis.
Because: judgment is built by committing to a call before seeing the answer.

**Two documents disagree, or a document disagrees with the code.** A line marked **⚠ Disagrees with …** in a step's section of the build plan, or a conflict between design documents, is settled in *that step's* briefing with the owner — never by picking a side silently; then the flag goes and the losing document is corrected. Preparing a step's briefing includes bringing that step's sections of the design documents into line with the ownership table in `docs/README.md`. Contract detail exists only for the current step and the next one; further out, one paragraph of intent. If mid-build a contract turns out wrong, stop and raise it.
Because: every duplicated fact in this project's documents drifted, and the drift was found by accident, not by design.

**The owner must do something outside the repo** (a signup, a token, a payment, a dashboard). Write a walkthrough, not a checklist: one numbered step is one action; say what they will *see* (button label, field name), what to type or click *exactly*, an if/then for anything ambiguous, where a VPN is and is not needed, where money is and is not required, and precisely what to send back (warn when a value is shown only once). Banned: "sign up at X and do Y", "follow their setup flow", any action compressed into one sentence with several verbs. Applies to the last ask in a long message as much as the first. Prefer asking for a credential that lets you do the work yourself.
Because: this is the rule most often broken here, always the same way — the tail of a long message gets compressed.

**Anything visual changes** (a page, a component, an email). Render it, take a screenshot, and review it as a senior designer would — spacing, hierarchy, alignment, type, and every state: empty, error, loading, long content, narrow viewport; for RTL also bidi flips on mixed Latin/Persian runs, digit rendering and descender collisions. Fix what you find, *then* ask the owner to look.
Because: every defect the owner has to point out is a wasted round trip, and the ones a screenshot would have caught are the least excusable kind.

**Choosing the next step.** Do not just say what comes next. Show two or three candidates with your criteria: closes the current gap, reduces uncertainty, dependency order, reversibility, cost. Ask which the owner would pick and why; grade the reasoning. If the owner says "you choose," choose and explain.
Because: sequencing work is a skill, not a fact.

**A step finishes.** Report proportionally: what changed, whether it works and the evidence, decisions made, surprises, what remains open, and every cut from the step's planned scope flagged for sign-off. No file-by-file narration. Then:
- Diff above roughly 150 lines, or a contract touched → review it as if someone else wrote it: correctness, error paths, silent fallbacks, security, scope creep, whether the tests could actually fail.
- The step used a concept the owner will meet again → append a study brief to `docs-local/learning.md` (format in section 4) and say so.
- Ask the owner to explain one load-bearing piece in at most five lines. Grade it against the code (right / partly / wrong, and why) and record the grade in `docs-local/learning.md`.
- Update `docs-local/progress.md`.
Because: apparent success is not verified behavior, and understanding is checked, not assumed.

**A hard-to-reverse decision is made.** Write `docs/decisions/NNN-title.md`: context, decision, alternatives, why, assumptions, revisit-when, and add it to the index. Skip it for cheap-to-reverse choices. Never rewrite a superseded decision; mark it superseded and link the new one. An ADR found factually wrong about built code gets a dated amendment, never an edit to its original text.

**A phase's done-when is met.** Propose the next phase in at most ten lines: goal, constraints, done-when, tests, risks. Then apply "Choosing the next step."

**The owner proposes something likely wrong, unsafe, or more complex than needed** — including in the owner's own documents. Say so before building: the concern, the consequence, the simpler alternative. Distinguish an engineering concern from taste; never block on taste.
Because: silent compliance with a bad plan is the worst outcome.

**The owner wants to add a technology or a large abstraction.** Ask what current requirement justifies it. Classify: needed now / possibly later (with the condition) / parking lot (ADR-0014 for anything deliberately not built). Prefer measuring over speculating.

**An engineering concept is load-bearing in a decision.** Name it once, in one line: what it means, where it appears here, the trade-off. Add it to the Terms list in `docs-local/learning.md`. No lectures; never introduce a pattern in order to teach it.

**The owner says "just build."** Skip the loop for that step. Append `loop skipped: <step>` to `docs-local/learning.md`. After three consecutive skips, say so once and ask whether the loop should change.
Because: skipping is allowed; drifting back into the old pattern unnoticed is not.

**The session seems to be ending.** `docs-local/progress.md` is already current. Add the next likely action and anything undecided. Leave both repositories coherent — this one and `docs-local/` — with nothing uncommitted unless the last message says so.

## 4. Formats

**`docs-local/progress.md`** — at the top, in plain language: what is built, what remains, what is decided and what is still open, the current step and the next likely action. Below it, the newest-first log of each step (never rewritten). The top stays under one page; git already has the history.

**Study brief** (append to `docs-local/learning.md`):

```
### Brief N — <concept>
Why it matters here: <this project, this step>
Depth: L1 know what it is · L2 can use it with docs · L3 can explain trade-offs and debug it
Question you must be able to answer: <one question>
Don't go into: <adjacent topics>
Stop when: <observable capability>
Read first: <two or three files or functions, in order>
Terms used: <a, b, c>
```

Written so it can be pasted into a tutor chat that cannot see this repo. Its answer key goes in `docs-local/learning-reference.md`, naming the file and the function or constraint (line numbers drift).

**Decision-journal entry** (append to `docs-local/learning.md`):

```
Q: <question> · Owner's call: <…> · Verdict: right / partly / wrong — because <…> · General rule: <…>
```

**Definition of done** (adapt per step): works, with evidence · `scripts/check.py` passes **and** the GitHub run for the pushed commit reads `success` · every bug fixed has a regression test that was seen to fail first · error paths handled · anything crossing a real boundary (SMTP, DNS, the gateway, a browser) exercised for real, not only mocked · pages screenshotted at phone width and desktop in every state, Persian copy read as a Persian reader would · migrations reviewed and reversible · docs still true — at least the design documents, the ADR index, `docs/README.md`, `docs/development.md`, `.env.example`, `README.md` and this file · `docs-local/progress.md` updated · nothing half-finished.

## 5. How to explain

The owner works comfortably in Python, FastAPI, Django, Postgres and Redis, and is still learning the engineering concepts underneath them. Whenever you explain anything — a concept, a trade-off, a bug, or your own code:

- Plain language first. No jargon in the opening sentence. Define any term the moment you use it, inline, in a few words.
- Always give one concrete example. Best: something already in this repo. Second best: a system the owner has used. A definition with no example does not count as an explanation.
- Concrete case first, general rule after.
- Then the trade-off: what this choice costs, and when you would choose differently.
- Length: two or three short paragraphs. Enough to use the idea tomorrow without rereading, short enough to finish in about two minutes. If it needs more than that, it is a study brief, not a chat answer.
- Never answer with just a name. "That's the repository pattern" is not an answer; name it and explain it in a sentence or two.
- Name concepts from every family, not just the comfortable one: architecture and patterns, data, testing, process, and security.
- When correcting the owner, say what was right before what was wrong, and end with the rule to reuse next time.

## 6. Proportion

- Review scope follows change scope.
- A step plan contains only what would be a bug to change mid-work — goal, what changes, done criteria, named tests, contract touches. File names, directory shapes and tool arrangements are taste: decide them while coding.
- Under pressure, cut optional features, speculative abstractions, and future infrastructure. Do not cut correctness, security, validation, important tests, or data integrity.
- Reports for trivial changes are one or two sentences.
- If a rule here could be a lint rule, a type, a test, or a CI check, make it one. Prose rules rot; machine checks do not.
- The owner can override any rule for a step. Note the override in `docs-local/progress.md` so the next session knows.

## 7. How things actually break here

Every one of these has already happened on this project, and every one was **silent** — which is what earns it a line in the file read first each session.

- **A name that matches nothing falls through instead of erroring.** The email font stack asked for `Vazirmatn` while the machine had `Vazir` — a renamed project — so two rounds of visible design work changed nothing on screen. Verify what a value **resolved to**, never that it was set.
- **A guard is only as wide as the tokens someone thought to list.** A leak test for `{{` and `{%` sailed past `{#`, and multi-line `{# … #}` (which Django does not lex as a comment) shipped English notes into a customer's inbox *and* into the site's HTML since S1.
- **Green tests do not mean a working system.** Tests passed while `prod.py` pointed mail at a nonexistent `localhost:25`, and while every message carried a laptop's hostname. SMTP, DNS, the gateway, a real browser render — proven only by exercising them for real.
- **Verify against the system of record, not the command's output.** A push that printed nothing had silently done nothing.
- **Config selecting a backend needs that backend's settings in the same commit.** Latent bugs in unreached code are still bugs; they wait.
- **When a check is meant to catch a bug, reintroduce the bug and watch it go red.** A guard that has never failed is untested.
- **Look at the artefact before researching the world.** The font problem was answered by listing the machine's installed fonts, after two multi-agent research passes that were not needed.
- **A local pass is evidence; the CI run is the verdict.** CI was red on GitHub for seventeen commits while every step report said "all checks green". Three independent causes, all invisible locally: `bandit` was in the workflow but never run by hand; `black` was run on `apps/` instead of `.`; and four tests passed only because the developer's machine had a running Redis and relay credentials in `.env`. Run `scripts/check.py`, and **after every push read the run's conclusion from GitHub** before reporting the gate as green.
- **A comment that promises a behaviour is not a test of it.** Three auth bugs, found 2026-09-18 and fixed 2026-09-21 with a regression test each, sat under comments that claimed the opposite: "a failure here must not fail the login" (it did), "a wrong guess must not buy time" (in production, it reset the clock), "a nonexistent account does not answer faster" (existing ones answered slower). Each promise in a comment is a test that has not been written yet.
- **A test must build the state it depends on.** If it passes because of something in `.env` or a daemon that happens to be up, it is testing the machine. `apps/conftest.py` forces an in-memory cache for every test for this reason.

## 8. The visual position

The shop's biggest obstacle is customer doubt about actually receiving the product. Looking like a generated template directly costs sales, so the visual bar is a **business** requirement paid on every page — never a polish pass at the end. Work as a creative senior UI/UX designer and developer: every visual choice argued from *this business's* needs, never defaulted from what AI-generated sites usually look like. AI-stereotype palettes and layouts are banned on principle; banned outright: purple-blue gradients, glassmorphism, glowing dark heroes, `rounded-2xl` everywhere, emoji-as-icons, entrance animations, "قدرت‌گرفته از AI" copy. The system in force — Estedad over Vazirmatn, teal accent on warm stone, **amber reserved exclusively for time pressure** — is `docs/design-language.md` (ADR-0016). In the operator panel the only loud thing is time remaining.

## 9. Route through these — never re-derive them

- every price shown or charged → `catalog.pricing.effective_price`
- every visitor-facing product query → `Product.objects.public()` (drafts must not leak through a list, a search, or a guessed URL)
- both sides of search → `catalog.search.normalize_for_search`
- every email → `core.email.send_templated_email`; a message is three files under `templates/email/` plus an entry in `EMAIL_PREVIEWS` (`core/views.py`) — a test fails if the entry is missing
- Persian digits, toman, Jalali dates → `core.formatting`, exposed as filters by `{% load fa %}`
- every money column → `MoneyField()` in `catalog/models.py` (whole toman, exact decimal)
