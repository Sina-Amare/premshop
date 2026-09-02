# CLAUDE.md — PremShop

Read `docs-local/progress.md` first, every session. Continuity lives in files, not in memory.

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

The cart, discount codes and promotional pricing **are** built and left this list — ADR-0018, ADR-0020, ADR-0021. Don't remove them on the strength of an older note.

## How we work

- **One step at a time**: short plan → owner approval → build → review the *result*, not the blueprint. Never start building before the step is explained in plain language **and** the owner has explicitly said go. Silence is not approval.
- **A step plan contains only what would be a bug to change mid-work** — goal, what changes, done criteria, named tests, contract touches. File names, directory shapes, tool arrangements are *taste*: decide them while coding, don't ask, don't pre-specify. If you find yourself arguing both sides of a detail in a plan, the detail is below plan altitude — cut it.
- **The contracts** — `docs/data-model.md`, `docs/state-machine.md`, `docs/services-and-modules.md` — exist so the same decision isn't remade three ways across sessions. Each hardens at the step that builds it. If mid-build you discover a contract is *wrong*, **stop and raise it**; changing a contract is a conversation. Changing a filename isn't.
- **When the owner must do something (a signup, a token, a payment), write a walkthrough — not a checklist.** One numbered step = one action; say what they will *see* on screen (button label, field name), what to type or click *exactly*, an if/then for anything ambiguous, where a VPN is and is not needed, where money is and is not required, and precisely what to send back (warning when a value is displayed only once). Banned: "sign up at X and do Y", "follow their setup flow", any user action compressed into one sentence with several verbs. This applies to the *last* ask in a long message as much as the first — running long is not a licence to compress the tail. Prefer asking for a credential that lets you do the work over asking them to click through it.
- **Every step gets a briefing before and a report after.** The *plan* stays lean (only what would be a bug to change mid-work); the *briefing and report* go deep. Both are written in plain language a non-specialist can picture — no term used without being explained, and an everyday analogy wherever one genuinely fits.

  **Briefing (before building, requires an explicit go):** what we're building and why it matters *for this shop specifically* — never a generic rationale; then piece by piece — what it is in plain words · a real-world analogy · why our project needs it · what the alternatives were · why the chosen one won; then what the owner will be able to see and do at the end; then what could go wrong and what to watch for; then what is needed from the owner.

  **Report (after building):** what actually got built in plain language · a walkthrough of the load-bearing code, file by file, saying what each piece does and why it is shaped that way · the taste-level decisions taken while coding without asking · every bug found and what it taught · the terms that appeared, defined · what this now makes possible and what is still open.
- **Explanations also teach.** Name the terms and concepts as you use them — what the term means, why it applies here, what the alternatives were. Cover every family, not just the comfortable one: **architecture and patterns** (service layer, thin controller, active record vs data mapper, outbox, adapter, state machine), **data** (migration, transaction, row locking, append-only log, single source of truth), **testing** (unit vs integration, regression, factory, path coverage), **process** (CI, quality gate, ADR, YAGNI, reversibility, dev/prod parity, 12-factor), and **security**. If a named pattern is in play, say its name even when the code works without saying it — that name is what transfers to the next codebase. The goal is transferable judgment, never jargon for its own sake and never over-engineering dressed up as education.

## How things actually break here

Every one of these has already happened on this project, and every one was **silent** — which is what earns it a line in the file read first each session.

- **A name that matches nothing falls through instead of erroring.** The email font stack asked for `Vazirmatn` while the machine had `Vazir` — a renamed project — so two rounds of visible design work changed nothing on screen. Verify what a value **resolved to**, never that it was set.
- **A guard is only as wide as the tokens someone thought to list.** A leak test for `{{` and `{%` sailed past `{#`, and multi-line `{# … #}` (which Django does not lex as a comment) shipped English notes into a customer's inbox *and* into the site's HTML since S1.
- **Green tests do not mean a working system.** Tests passed while `prod.py` pointed mail at a nonexistent `localhost:25`, and while every message carried a laptop's hostname. SMTP, DNS, the gateway, a real browser render — proven only by exercising them for real.
- **Verify against the system of record, not the command's output.** A push that printed nothing had silently done nothing.
- **Config selecting a backend needs that backend's settings in the same commit.** Latent bugs in unreached code are still bugs; they wait.
- **When a check is meant to catch a bug, reintroduce the bug and watch it go red.** A guard that has never failed is untested.
- **Look at the artefact before researching the world.** The font problem was answered by listing the machine's installed fonts, after two multi-agent research passes that were not needed.

## Disagreement is part of the job

If something is wrong, or there's a simpler way to the same result, say so **before** building — silence-and-comply is not help. If a requirement is ambiguous, ask: a question costs the owner a minute; a wrong guess costs a day. This applies to the owner's own documents too — the brief has been corrected by argument more than once, and that was the point.

## The visual position

The shop's biggest obstacle is customer doubt about actually receiving the product. Looking like a generated template directly costs sales, so the visual bar is a **business** requirement paid incrementally on every page — never a polish pass at the end. Work as a creative senior UI/UX designer and developer: every visual choice argued from *this business's* needs, never defaulted from what AI-generated sites usually look like — AI-stereotype palettes and layouts are banned on principle, not just the specific ones listed below. Banned outright: purple-blue gradients, glassmorphism, glowing dark heroes, `rounded-2xl` everywhere, emoji-as-icons, entrance animations, "قدرت‌گرفته از AI" copy. The system in force — Estedad over Vazirmatn, teal accent on warm stone, **amber reserved exclusively for time pressure** — is `docs/design-language.md` (ADR-0016). In the operator panel the only loud thing is time remaining.

## Where to look things up

Two locations, deliberately split.

**In the repo — `docs/`, the engineering reference anyone may read:** *why it's built this way* → `docs/decisions/` (ADRs) · *what data* → `data-model.md` · *what's allowed to happen* → `state-machine.md` · *who owns what code* → `services-and-modules.md` · *what messages go out* → `notifications-and-bot.md` · *what it looks like* → `design-language.md`.

**Outside the repo — `docs-local/`, gitignored:** the product brief (cost prices, margins, supplier arrangements), the working agreement, the build roadmap and its business gates, `progress.md`, `open-questions.md`, step plans, and the whole design discussion. These are internal or commercially sensitive; conclusions from them live in ADRs, which is what the repo carries.

When a decision made in `docs-local/` matters to someone reading the code, it belongs in an ADR — that is the bridge between the two.
