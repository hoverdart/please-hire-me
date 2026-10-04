# Documentation map

Start with the [README](../README.md) for installation, guided setup, provider choices, scheduling, Gmail, and Raspberry Pi Connect deployment.

| Reference | Purpose |
| --- | --- |
| [Architecture](architecture.md) | Component boundaries, state, concurrency, and external verification limits |
| [Readiness audit](readiness.md) | Fresh-environment proof and outstanding live/account requirements |
| [Worker contract](worker-contract.md) | Executable policy and submission invariants |
| [Configuration examples](../config/README.md) | Public reference formats; never applicant defaults |
| [Discovery data](../data/README.md) | Runtime seed inputs and historical public research |
| [Compatibility scripts](../scripts/README.md) | Existing wrappers and read-only tools |
| [Contributing](../CONTRIBUTING.md) | Local checks and safe fixture testing |
| [Archive](archive/README.md) | Retired recipes and disabled helpers, kept only for provenance |

## Source layout

The `hireme/` package stays flat so existing imports and command wrappers remain stable.

| Responsibility | Modules |
| --- | --- |
| Entry points and local desk | `cli.py`, `server.py`, `setup_status.py`, `demo.py`, `doctor.py`, `recovery.py`, `ledger.py`, `saved_views.py`, `outcome_ledger.py`, `run_ledger.py`, `source_ledger.py`, `document_controls.py`, `document_downloads.py`, `posting_check.py`, `presentation.py`, `static/` |
| Applicant documents, writing and question review | `onboarding.py`, `materials.py`, `material_ledger.py`, `letters.py`, `answers.py`, `saved_answers.py`, `question_ledger.py` |
| Inference and credentials | `provider.py`, `connections.py` |
| Employer accounts, metadata review and encrypted transfer | `accounts.py`, `account_ledger.py`, `account_transfer.py`, `account_transfer_web.py` |
| Application execution and company boundaries | `worker.py`, `browser.py`, `policy.py`, `company_controls.py` |
| Discovery | `discovery.py`, `posting_import.py`, `opportunity_search.py`, `portals.py`, `net.py` |
| Persistence and migration | `store.py`, `config.py`, `backup.py`, `backup_inspection.py`, `migration.py`, `util.py` |
| Email | `gmail.py`, `reports.py` |
| Local/Pi scheduling | `scheduler.py`, `pi.py` |

`tests/` mirrors these responsibilities and uses synthetic ATS/mail fixtures. Dependency locks live in `requirements/`: runtime for installation, development for CI and local tests. Private applicant state lives outside the repository; see the main README for its location and migration commands.
