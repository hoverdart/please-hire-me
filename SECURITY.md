# Security

Report security problems through [GitHub private vulnerability reporting](https://github.com/hoverdart/please-hire-me/security/advisories/new). Include the affected version, a minimal reproduction with invented applicant data, and the expected and observed behavior.

Keep resumes, filled answers, tokens, cookies, passwords, screenshots and private logs out of public issues. If reproducing the issue could send an application, upload a document or create an account, use a local fixture instead.

The supported deployment is one applicant's private local installation. The dashboard binds to loopback and requires its access token. It is not an internet-facing multi-user service. Use the Pi desktop or an authenticated SSH tunnel to access another machine's loopback dashboard.

The current release candidate receives fixes. Older revisions may lack current transport safeguards, dependency patches and recovery behavior. Follow the upgrade and rollback procedure in the [release runbook](docs/final-release-runbook.md), preserving the latest application history.

If an incident may have exposed credentials, pause work, preserve the current ledger and evidence, revoke the affected credentials through their providers, and reconnect only after checking the installation. An uncertain employer request must stay held for verification. Removing a local error or reinstalling the app does not establish that the employer received nothing.
