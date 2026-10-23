# Scripts

If you're exploring how the app finds jobs, runs batches or checks a release, this is the map of its helper scripts. For normal setup and everyday use, start with the [main README](../README.md) and [user guide](../docs/guide.md). To work on a script, see [Contributing](../CONTRIBUTING.md) for the local checks.

Run current commands from the repository root with `.venv/bin/python -m hireme`; use `--help` for supported flags.

| Scripts | Purpose |
| --- | --- |
| `delta_sweep.py`, `portal_sweep.py`, `list_sweep.py` | Compatibility wrappers for managed discovery |
| `schedule.sh`, `scheduled_run.sh`, `dupe_check.sh` | Compatibility wrappers for scheduling, batches and duplicate checks |
| `probe_boards.sh`, `render_boards.py` | Discover into the private ledger and print source health |
| `ashby_form_fields.sh` | Validate slugs and inspect public form fields through structured GraphQL |
| `tracker_companies.py` | Extract explicit company wikilinks from a supplied tracker |
| `fetch_hn_hiring.sh` | Read-only public HN research |
| `check_release_artifacts.py` | Reject private file classes, duplicate/unsafe paths, links and special files in source archives and wheels |
| `check_install_artifact.py` | Install each supplied wheel outside the checkout and verify dependencies, CLI startup, bundled assets, conservative onboarding and history restoration |

Discovery stores snapshots in the private ledger. Wrappers retain their existing paths for compatibility and do not advance legacy timestamp files. Release checks are used by CI; see [Contributing](../CONTRIBUTING.md).
