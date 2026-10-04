# Contributing

The worker must never invent facts, retry uncertain submissions, exceed company/application budgets or bypass login/CAPTCHA. Keep those properties executable and tested, not in model prompts.

Run `.venv/bin/python -m pytest -q`, `node --check hireme/static/app.js`, and `bash -n` on each changed shell file individually. Browser tests must use synthetic local ATS fixtures. Never test a submission against a real employer as a contributor check.

No personal data belongs in git: facts, documents, screenshots, logs, credentials and application history stay private. Before committing, inspect `git diff --cached` and `git status --short`. Source research is public evidence only; do not write candidate answers into tracked notes.

Every adapter must declare its trusted destinations and bounded actions, demonstrate unknown-field handling, record exact Q&A, and preserve UNKNOWN after ambiguous submission. Unsupported widgets/portals become exceptions rather than broader agent authority.

The frontend is framework-free and has no runtime build step. Keep `hireme/static/` readable and formatted. Optional formatting: `npm exec --yes --package=prettier@3.6.2 -- prettier --write hireme/static/app.js hireme/static/index.html hireme/static/style.css`. Use `hireme demo` for a disposable, read-only sample workspace when reviewing layouts. Its data is invented and never touches an applicant’s real directory.

Display metadata belongs in `presentation.py`, separate from executable eligibility policy. Quiet eligibility decisions must remain recorded, while uncertain outcomes and unanswered questions remain actionable. The overview counts the full ledger; the main dashboard table queries the complete ledger in pages of 50 records; auxiliary state snapshots are bounded to 500 and prioritize unresolved questions and uncertain submissions and CSV exports cover all records.
