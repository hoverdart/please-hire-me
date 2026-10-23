# Dependency locks

For your own installation, the [main README](../README.md#get-started) uses `setup.sh` to install the runtime dependencies. If you're here to work on the app, follow the [development setup](../CONTRIBUTING.md#set-up-a-development-environment), which includes the test tools too.

- `runtime.lock`: pinned application and optional Gmail dependencies used by `setup.sh`.
- `development.lock`: runtime dependencies plus pytest and PDF rendering tools used by CI and local verification.

Both install before `pip install --no-deps -e .`. Supported dependency ranges and optional extras are declared in `../pyproject.toml`. Keep the runtime pins consistent with the development lock; do not commit an editable checkout path or a platform-specific local wheel URL.

The suite checks matching runtime/development pins and declared dependency ranges. CI audits the development lock against current vulnerability advisories without installing its packages. See [Contributing](../CONTRIBUTING.md) to repeat the audit in an isolated tooling environment.
