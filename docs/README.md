# Documentation

Technical documentation for PremShop. It describes the system as it is built; the reasons behind it are in the decision records, and the code and its tests are the final word on detail.

| Document | Read it to |
|---|---|
| [Architecture](architecture.md) | Understand the components, application boundaries, request flows, background jobs, email and security |
| [Data model](data-model.md) | Look up a table, a constraint, a deletion rule or an index |
| [Order lifecycle](order-lifecycle.md) | See how an order moves between statuses and how simultaneous requests stay safe |
| [Development](development.md) | Set up, configure, run, test or build the shop |
| [Design language](design-language.md) | Design a page or write Persian copy |
| Runbooks: [deploy](runbooks/deploy.md), [rollback](runbooks/rollback.md), [restore](runbooks/restore.md) | Operate the production server |
| [Decision records](decisions/README.md) | Find out why something is the way it is |

## Conventions

- **One home per fact.** Each fact is stated in one document; the others link to it.
- **Built, then planned.** A document describes what exists. Designed but unbuilt work appears only where it shapes what exists, marked as not built and linked to the decision that governs it.
- **Decisions are not rewritten.** A decision record changes only by a dated amendment, and a replaced decision is marked superseded and kept.
- **Plain technical English.** Persian appears only where the subject is Persian text. `apps/core/tests/test_written_voice.py` checks every tracked text file.

Product strategy, the build plan and internal working notes are kept outside this repository.
