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

## Reading these documents

The data model, state machine, and service contracts are **contracts**: they exist so the same decision isn't made three different ways across sessions. Each hardens at the step that implements it — the note at the top of each file says what is settled and what is still draft. Discovering mid-work that a contract is wrong is a conversation, not a workaround.

The **ADRs** carry the reasoning. If you are about to change something and wonder why it is the way it is, look there first; the answer, its alternatives, and the condition that would justify revisiting it are usually recorded.

Product strategy, the build roadmap, and internal working notes are kept outside this repository.
