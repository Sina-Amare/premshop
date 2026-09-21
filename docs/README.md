# PremShop Documentation

Technical reference for the codebase. Which document answers which question:

| You're asking… | Read |
|---|---|
| Why is it built this way? | [decisions/](decisions/README.md) — architecture decision records |
| What tables, fields, and constraints exist? | [data-model.md](data-model.md) |
| What may an order or payment do next, and what happens when it does? | [state-machine.md](state-machine.md) — the order-item machine in full; the payment machine as intent until S5 |
| Which app owns what; what are the service signatures? | [services-and-modules.md](services-and-modules.md) |
| What messages go out, when, and in what words? | [notifications-and-bot.md](notifications-and-bot.md) — the built emails in full; later messages and the Telegram bot as intent |
| What does the interface look like — type, colour, layout rules? | [design-language.md](design-language.md) |
| How do I run it, test it, and what are this setup's traps? How does the built code hang together across files? | [development.md](development.md) |
| How does work happen here — steps, approvals, reviews? | [../AGENTS.md](../AGENTS.md) |

## Which document owns which kind of fact

Each fact is stated in **one** of these and referred to from the others — restating it elsewhere is how documents drift apart. A step's preparation brings that step's sections into line with this table; where two documents disagree, the disagreement is settled in the step's briefing, not by picking one silently.

| Kind of fact | Its one home |
|---|---|
| Tables, fields, constraints, indexes, what happens on delete | `data-model.md` |
| Which status may change to which and who may change it; the events each change announces and their dedupe-key families; concurrency rules; the tests that prove the two machines | `state-machine.md` |
| Which app owns what and may import what; service function signatures; background tasks and their schedule | `services-and-modules.md` |
| The words of each message, its channel, how the outbox delivers it and builds a dedupe key; the Telegram webhook | `notifications-and-bot.md` |
| Type, colour, spacing, layout, the look of emails | `design-language.md` |
| Setup, commands, environment traps, how built code hangs together across files | `development.md` |
| Why any of it is so | `decisions/` |
| For anything already built | the code — these documents keep only what the code cannot say |

## Reading these documents

Each of the four design documents opens with an **"At a glance"** box: what it answers, which parts are built, which are still draft, and which sections the next step needs. Nobody is expected to read them end to end.

The data model, state machine, and service contracts are **contracts**: they exist so the same decision isn't made three different ways across sessions. Each hardens at the step that implements it — the note at the top of each file says what is settled and what is still draft. Discovering mid-work that a contract is wrong is a conversation, not a workaround.

**Contract detail exists only for the current step and the next one** (today: S4a orders, S4b cart and checkout). Anything further out is one paragraph of intent that names the ADRs already binding it, plus a pointer to the full earlier draft in git (`git show 027c5d1:docs/<file>`); it is specified again when its step is planned. Detail written far ahead of the code is a guess, and these documents showed what guesses do: dozens of them had quietly contradicted each other.

The **ADRs** carry the reasoning. If you are about to change something and wonder why it is the way it is, look there first; the answer, its alternatives, and the condition that would justify revisiting it are usually recorded.

Product strategy, the build roadmap, and internal working notes are kept outside this repository.
