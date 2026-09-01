# PremShop Documentation

Which document answers which kind of question.

| You're asking… | Read |
|---|---|
| What is this product, for whom, and what's in/out of scope? | [brief.md](brief.md) *(Persian — the product design document)* |
| How do we work — quality bar, step rhythm, definition of done? | [working-agreement.md](working-agreement.md) *(Persian)* + [../CLAUDE.md](../CLAUDE.md) |
| Why is it built this way? | [decisions/](decisions/README.md) — ADRs 0001–0016, plus the legacy shorthand map |
| What tables/fields/constraints exist? | [data-model.md](data-model.md) — **contract** |
| What may an order/payment do next, and what happens when it does? | [state-machine.md](state-machine.md) — **contract** (full transition matrix, event catalog, tests) |
| Which app owns what; what are the service signatures? | [services-and-modules.md](services-and-modules.md) — **contract** |
| What messages go out, when, in what words? | [notifications-and-bot.md](notifications-and-bot.md) (incl. the Telegram webhook contract) |
| What gets built, in what order, with what gates? | [build-plan.md](build-plan.md) (S1…S12 + S6b) |
| What does the current step involve exactly? | [step-plans/](step-plans/) — one approved plan per step |
| What do things look like — fonts, colors, type rules? | [design-language.md](design-language.md) |
| What still needs the owner's answer? | [open-questions.md](open-questions.md) |
| Where did we leave off? | [progress.md](progress.md) — read this first, every session |

**Contracts** (data model, state machine, services) harden at the step that builds them and change only through a conversation — see the firmness note at the top of each. The design *discussion* that produced all this lives outside the repo in `docs-local/` (gitignored); the conclusions live here.
