# Contributing

This started with my own frustration with repetitive job applications. If you've run into the same thing, contributions that make it more useful to other applicants are welcome.

You can help by clarifying a setup step, improving discovery, or fixing a form that gets stuck. For form bugs, share the public posting link and what happened, with personal answers and contact details removed. For code changes, the [architecture](docs/architecture.md) explains where things live and the [worker contract](docs/worker-contract.md) explains the behavior to preserve.

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
for file in hireme/static/*.js; do node --check "$file" || exit; done
for file in run.sh setup.sh scripts/*.sh; do bash -n "$file" || exit; done
.venv/bin/python -m compileall -q hireme scripts tests
.venv/bin/python -m pip check
git diff --check
```

On a Raspberry Pi with system Chromium, run the same fixtures with
`HIREME_TEST_BROWSER_CHANNEL=system-chromium .venv/bin/python -m pytest -q`.
The fixtures still use disposable applicant directories and synthetic employers.

To check the visible human-verification handoff on a worker desktop, run
`HIREME_TEST_BROWSER_CHANNEL=system-chromium HIREME_TEST_HUMAN_VISIBLE=1 .venv/bin/python -m pytest -q tests/test_ashby_drafts.py -k browser_human_handoff`.
This opens synthetic application windows; the fixture serves the employer and verification requests. It submits nothing to a real employer. CI checks the same routing and wait behavior in a headless fixture; that does not prove a visible desktop is available on the target machine.

Run checks appropriate to the change and record failures and skips. Full-suite success is local fixture evidence; keep hosted CI, device operation, authenticated sessions and employer acceptance separate. See [release status](docs/public-release.md).

For packaging changes, build and inspect fresh distributions:

```bash
.venv/bin/python -m pip install build
release_dist=$(mktemp -d)
.venv/bin/python -m build --outdir "$release_dist"
.venv/bin/python scripts/check_release_artifacts.py "$release_dist"/*
.venv/bin/python scripts/check_install_artifact.py "$release_dist"/*.whl
```

Build into a fresh directory so stale distributions cannot be mistaken for current output. The archive check requires at least one input and rejects private file classes, duplicate/unsafe paths, links and special files in source archives and wheels. The installed-wheel check verifies every supplied wheel in a separate environment outside the checkout, including CLI startup, dashboard assets, conservative onboarding and history restoration. It uses disposable applicant storage without credentials or employer connections. Build outputs, caches and local review files are ignored; they do not belong in source commits.

CI also audits the development lock, which the suite checks includes every runtime pin. To repeat that audit, install `pip-audit==2.10.1` in a separate tooling environment and run `python -m pip_audit -r requirements/development.lock --disable-pip --no-deps`. An audit needs network access and reflects the advisories available at that time; it does not prove the absence of vulnerabilities. Update both locks together and keep declared dependency ranges compatible.

## Keep state and authority bounded

Personal facts, documents, screenshots, logs, credentials and application history stay in private storage. Before committing, inspect `git diff --cached` and `git status --short`. Public source research must not contain candidate answers.

Every adapter declares trusted destinations and bounded actions, handles unknown fields, records exact answers and preserves uncertain outcomes after ambiguous submissions. Unsupported controls become review items rather than broader model authority.

Display metadata belongs in `presentation.py`, separate from eligibility policy. Quiet eligibility decisions remain recorded; uncertain outcomes and unanswered questions stay actionable. The main job ledger queries the complete history in pages of 50. Auxiliary snapshots are bounded and prioritize unresolved records; CSV exports cover all jobs.

## Frontend and documentation

The frontend is framework-free and has no runtime build step. Keep `hireme/static/` readable. Use `hireme demo` for a disposable read-only workspace with invented data during layout review. It does not touch the applicant directory.

Update the relevant [guide](docs/README.md) when commands or user behavior change. Keep installation concise in the README, everyday tasks in the user guide, and Pi operations in the Pi guide. Put current verification limits in release status rather than appending dated development transcripts to user documentation. Private runtime reports remain outside Git.

[PRODUCT.md](PRODUCT.md) and [DESIGN.md](DESIGN.md) describe product and visual conventions. Historical repair reports and retired helpers are available in Git history.
