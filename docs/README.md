# Documentation

Start with the [README](../README.md) for installation and onboarding.

| Guide | Purpose |
| --- | --- |
| [User guide](guide.md) | Documents, drafts, questions, models, schedules, backups, Gmail and troubleshooting |
| [Raspberry Pi](pi.md) | Setup, background services, restarts and upgrades |
| [Platform connections](connected-workspace.md) | Optional Handshake/Work at a Startup connections and capability gates |
| [Release status](public-release.md) | Candidate scope, verification boundaries and remaining release gates |
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
