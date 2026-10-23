# Documentation

I built this to spend less time repeating the same application steps. These guides explain how to set it up for your own search, use it day to day, and work on the code.

The [main README](../README.md) has the story, installation instructions and a sample workspace you can try without personal data. To tailor your own setup, start with [Make it yours](../README.md#make-it-yours) and the [user guide](guide.md#set-it-up-for-your-search). For code changes, head to [Contributing](../CONTRIBUTING.md).

## Guides

| Guide | Purpose |
| --- | --- |
| [User guide](guide.md) | Documents, drafts, questions, models, schedules, backups, Gmail and troubleshooting |
| [Raspberry Pi](pi.md) | Setup, background services, restarts and upgrades |
| [Platform connections](connected-workspace.md) | Optional Handshake/Work at a Startup connections and capability gates |
| [Release status](public-release.md) | Candidate scope, verification boundaries and remaining release gates |
| [AutoApply comparison](autoapply-comparison.md) | Ideas adopted, strengths retained and the next improvements |
| [Job-search feedback loop](feedback-loop.md) | Unified application records and the reliability, measurement and networking roadmap |
| [Architecture](architecture.md) | Component authority, persistence, concurrency, browser boundaries and recovery |
| [Worker contract](worker-contract.md) | Executable policy and submission invariants |
| [Configuration examples](../config/README.md) | Public reference formats for private facts and settings |
| [Discovery data](../data/README.md) | Seed inputs and public research |
| [Scripts](../scripts/README.md) | Managed-worker wrappers, research tools and release checks |
| [Contributing](../CONTRIBUTING.md) | Local validation and safe fixture development |

## Source layout

- `hireme/`: Python worker, CLI, local server and persistence. The package stays flat to preserve imports and command compatibility.
- `hireme/static/`: framework-free dashboard assets, with no runtime build step.
- `hireme/resources/`: discovery seeds bundled into installed distributions.
- `tests/`: synthetic ATS, email, browser, PDF and state fixtures.
- `requirements/`: pinned runtime and development dependencies.
- `config/` and `data/`: public examples, seeds and research; applicant state belongs outside the checkout.
- `scripts/`: compatibility wrappers, read-only research and distribution validation.

[PRODUCT.md](../PRODUCT.md) and [DESIGN.md](../DESIGN.md) describe product and interface conventions. Superseded repair reports and retired helpers remain available in Git history rather than the current guide.
