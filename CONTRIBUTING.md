# Contributing

The worker must never invent facts, retry uncertain submissions, exceed company/application budgets or bypass login/CAPTCHA. Keep these properties executable and tested. All side-effectful browser and mail checks use synthetic local fixtures; contributor checks must not submit to real employers.

## Set up a development environment

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/development.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m playwright install chromium
```

On Linux, install missing Chromium system libraries with `playwright install-deps chromium` using an account allowed to install OS packages. This setup does not start the dashboard or change applicant preferences.

## Validate changes

```bash
.venv/bin/python -m pytest -q
for file in hireme/static/*.js; do node --check "$file"; done
for file in run.sh setup.sh scripts/*.sh; do bash -n "$file"; done
git diff --check
```

Run checks appropriate to the change and record failures and skips. Full-suite success is local fixture evidence; keep hosted CI, device operation, authenticated sessions and employer acceptance separate. See [release status](docs/public-release.md).

For packaging changes, build and inspect fresh distributions:

```bash
.venv/bin/python -m pip install build
.venv/bin/python -m build
.venv/bin/python scripts/check_release_artifacts.py dist/*
.venv/bin/python scripts/check_install_artifact.py dist/*.whl
```

The installed-wheel check uses a temporary isolated environment outside the checkout. It installs declared dependencies and verifies resource loading and conservative onboarding without applicant credentials or employer connections. Build outputs, caches and local review files are ignored; they do not belong in source commits.

## Keep state and authority bounded

Personal facts, documents, screenshots, logs, credentials and application history stay in private storage. Before committing, inspect `git diff --cached` and `git status --short`. Public source research must not contain candidate answers.

Every adapter declares trusted destinations and bounded actions, handles unknown fields, records exact answers and preserves uncertain outcomes after ambiguous submissions. Unsupported controls become review items rather than broader model authority.

Display metadata belongs in `presentation.py`, separate from eligibility policy. Quiet eligibility decisions remain recorded; uncertain outcomes and unanswered questions stay actionable. The main job ledger queries the complete history in pages of 50. Auxiliary snapshots are bounded and prioritize unresolved records; CSV exports cover all jobs.

## Frontend and documentation

The frontend is framework-free and has no runtime build step. Keep `hireme/static/` readable. Use `hireme demo` for a disposable read-only workspace with invented data during layout review. It does not touch the applicant directory.

Update the relevant [guide](docs/README.md) when commands or user behavior change. Keep installation concise in the README, everyday tasks in the user guide, and Pi operations in the Pi guide. Put current verification limits in release status rather than appending dated development transcripts to user documentation. Private runtime reports remain outside Git.

[PRODUCT.md](PRODUCT.md) and [DESIGN.md](DESIGN.md) describe product and visual conventions. Historical repair reports and retired helpers are available in Git history.
