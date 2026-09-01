# CLAUDE.md — PremShop

Read `docs/progress.md` first, every session. Continuity lives in files, not in memory.

## What this is

An Iranian web shop (premshop.ir) selling digital subscriptions and accounts — run by **one person**, serving a few dozen customers, moving **real money** over card-to-card transfers, and storing **other people's account credentials**. The scope is genuinely small. The stakes are not. The engineering bar is set by the stakes, not the scope.

## Scope versus quality

Cutting features is fine. Cutting craft is not. The test for which is which is **reversibility**: a feature we skip can be added later; a wrong data model, a plaintext credential, an unguarded money path cannot be cheaply unwound. When deciding how much to build, ask "is this decision reversible?" — not "do we need this now?". The irreversible things were decided once, carefully, and live in `docs/decisions/`.

## Never traded away for speed

- Field-level encryption for delivered credentials **and** `customer_input` — with the key escrowed offline before any real delivery. A restore that can't decrypt is a failed restore. (ADR-0007)
- Server-side price calculation, idempotent payment confirmation, DB-enforced unique-amount matching, and a `Refund` ledger row behind every REFUNDED status. (ADR-0005, 0006)
- Status transitions only through service methods; every transition writes its audit row in the same transaction. (ADR-0003)
- Object-level ownership checks — logged-in is not authorized.
- No credential *values* in any message, log, or error report — ever. The single-use magic link is the one sanctioned convenience, and only with its full constraint list. (ADR-0008)
- Tests on every money and auth path; reversible migrations; toman everywhere with rial only at the two tested boundaries.
- Persian UI text, English code and logs. No `letter-spacing` on Persian, no italics, no Latin placeholder text in the UI.

## Deliberately not built — don't helpfully add these back

No DRF/API before phase 3. No cart. No coupons or discount field. No `IN_PROGRESS` or `REPLACED` status. No inventory, supplier, or FX models. No RBAC, wallet, or ticket system. No Postgres FTS. No identity tables. No PaymentProvider interface until Zibal provides the second implementation. Each has an ADR with the condition that would flip it (see ADR-0014); meeting the condition is the only way back in.

## How we work

- **One step at a time**: short plan → owner approval → build → review the *result*, not the blueprint. Never start building before the step is explained in plain language **and** the owner has explicitly said go. Silence is not approval.
- **A step plan contains only what would be a bug to change mid-work** — goal, what changes, done criteria, named tests, contract touches. File names, directory shapes, tool arrangements are *taste*: decide them while coding, don't ask, don't pre-specify. If you find yourself arguing both sides of a detail in a plan, the detail is below plan altitude — cut it.
- **The contracts** — `docs/data-model.md`, `docs/state-machine.md`, `docs/services-and-modules.md` — exist so the same decision isn't remade three ways across sessions. Each hardens at the step that builds it. If mid-build you discover a contract is *wrong*, **stop and raise it**; changing a contract is a conversation. Changing a filename isn't.
- When the owner must do something (a signup, a token, a payment), give numbered steps with what each does and what to send back. Prefer asking for a credential that lets you do the work over asking them to click through it.
- **Explanations onboard, fully.** Before each step: what will be done, why, which alternatives were considered, why the pick won. The owner should end up genuinely onboarded on the implementation — they'll ask for extra detail when they want it. Note the division of labor with the plan-altitude rule above: the *plan* stays lean (only what would be a bug to change); the *narration* goes deep.
- **Explanations also teach.** Name the software-engineering terms and concepts as you use them — what the term means, why it applies here, what the alternatives were. The owner is learning the professional way of thinking, not just receiving output; the goal is transferable judgment, never jargon for its own sake and never over-engineering dressed up as education.

## Disagreement is part of the job

If something is wrong, or there's a simpler way to the same result, say so **before** building — silence-and-comply is not help. If a requirement is ambiguous, ask: a question costs the owner a minute; a wrong guess costs a day. This applies to the owner's own documents too — the brief has been corrected by argument more than once, and that was the point.

## The visual position

The shop's biggest obstacle is customer doubt about actually receiving the product. Looking like a generated template directly costs sales, so the visual bar is a **business** requirement paid incrementally on every page — never a polish pass at the end. Work as a creative senior UI/UX designer and developer: every visual choice argued from *this business's* needs, never defaulted from what AI-generated sites usually look like — AI-stereotype palettes and layouts are banned on principle, not just the specific ones listed below. Banned outright: purple-blue gradients, glassmorphism, glowing dark heroes, `rounded-2xl` everywhere, emoji-as-icons, entrance animations, "قدرت‌گرفته از AI" copy. The system in force — Estedad over Vazirmatn, teal accent on warm stone, **amber reserved exclusively for time pressure** — is `docs/design-language.md` (ADR-0016). In the operator panel the only loud thing is time remaining.

## Where to look things up

`docs/README.md` maps every question to its document. Shortest version: *why* → `docs/decisions/` · *what data* → `data-model.md` · *what's allowed to happen* → `state-machine.md` · *who owns what code* → `services-and-modules.md` · *what's next* → `progress.md` + `step-plans/` · *what's undecided* → `open-questions.md`. The design discussion that produced all this is in `docs-local/` (not in the repo); conclusions are in ADRs.
