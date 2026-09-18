# PremShop Documentation

Technical reference for the codebase. Which document answers which question:

| You're asking… | Read |
|---|---|
| Why is it built this way? | [decisions/](decisions/README.md) — architecture decision records |
| What tables, fields, and constraints exist? | [data-model.md](data-model.md) |
| What may an order or payment do next, and what happens when it does? | [state-machine.md](state-machine.md) — full transition matrix, event catalog, required tests |
| Which app owns what; what are the service signatures? | [services-and-modules.md](services-and-modules.md) |
| What messages go out, when, and in what words? | [notifications-and-bot.md](notifications-and-bot.md) — including the Telegram webhook contract |
| What does the interface look like — type, colour, layout rules? | [design-language.md](design-language.md) |
| How do I run it locally? | [../README.md](../README.md) |

## Which document owns which kind of fact

Each fact is stated in **one** of these and referred to from the others — restating it elsewhere is how documents drift apart. A step's preparation brings that step's sections into line with this table; where two documents disagree, the disagreement is settled in the step's briefing, not by picking one silently.

| Kind of fact | Its one home |
|---|---|
| Tables, fields, constraints, indexes, what happens on delete | `data-model.md` |
| Which status may change to which and who may change it; the events each change announces and their dedupe-key families; concurrency rules; the tests that prove the two machines | `state-machine.md` |
| Which app owns what and may import what; service function signatures; background tasks and their schedule | `services-and-modules.md` |
| The words of each message, its channel, how the outbox delivers it and builds a dedupe key; the Telegram webhook | `notifications-and-bot.md` |
| Type, colour, spacing, layout, the look of emails | `design-language.md` |
| Why any of it is so | `decisions/` |
| For anything already built | the code — these documents keep only what the code cannot say |

## Reading these documents

Each of the four design documents opens with an **"At a glance"** box: what it answers, which parts are built, which are still draft, and which sections the next step needs. Nobody is expected to read them end to end.

The data model, state machine, and service contracts are **contracts**: they exist so the same decision isn't made three different ways across sessions. Each hardens at the step that implements it — the note at the top of each file says what is settled and what is still draft. Discovering mid-work that a contract is wrong is a conversation, not a workaround.

The **ADRs** carry the reasoning. If you are about to change something and wonder why it is the way it is, look there first; the answer, its alternatives, and the condition that would justify revisiting it are usually recorded.

Product strategy, the build roadmap, and internal working notes are kept outside this repository.
