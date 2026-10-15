# please-hire-me

A private job application workspace that runs on your computer or a Raspberry Pi. Discover openings, prepare answers and cover letters from your confirmed facts, submit supported applications, and keep a record of outcomes and evidence.

![Application desk with synthetic sample data](docs/images/application-desk-demo.png)

[Try the sample workspace](#try-it-without-personal-data) or read the [user guide](docs/guide.md). The screenshot uses invented data; see its [provenance](docs/images/application-desk-demo.provenance.md).

## Get started

Requires **Python 3.11+**, Git, and macOS or Linux. Windows users need a supported Linux environment such as WSL2. For Raspberry Pi OS, follow the [Pi guide](docs/pi.md).

```bash
git clone https://github.com/hoverdart/please-hire-me.git
cd please-hire-me
./setup.sh
```

Setup installs pinned dependencies and Chromium, starts the dashboard, and opens your browser when available. Use the printed dashboard URL, including its token. Keep the terminal open; **Ctrl+C** stops the dashboard. If Linux browser libraries are missing, install them with `.venv/bin/python -m playwright install-deps chromium` using an account allowed to install OS packages.

To start it again after installation:

```bash
cd please-hire-me
.venv/bin/python -m hireme dashboard --open
```

Complete the setup checklist:

1. Upload a resume PDF with selectable text. A transcript is optional.
2. Review extracted facts and confirm them. Supply missing information, including work authorization and sponsorship.
3. Add context and writing samples, then review and approve how each source may be used.
4. Choose search preferences, company limits, batch timing and request limits.
5. Connect a supported subscription CLI or explicitly choose paid API billing.
6. Finish paused. Review prepared drafts before enabling automatic submissions.

Your documents, credentials and history live outside the checkout in `~/.local/share/please-hire-me/`. Setup can be resumed later. Choosing Pi in setup does not transfer your files to another machine.

## Try it without personal data

After installing dependencies:

```bash
.venv/bin/python -m hireme demo
```

Open the printed URL. The sample workspace is temporary and read-only, uses invented records, and makes no model requests or applications. **Ctrl+C** removes its sample data. It uses port 8767; the regular dashboard uses 8766.

## Everyday use

Use **Find a tool** or **Ctrl/⌘ K** to locate tasks in the workspace.

| Task | Where to go |
| --- | --- |
| Find jobs, prepare drafts or review progress | Today / Jobs → **Overview** |
| Answer held questions or check uncertain outcomes | **Needs you** |
| Update a resume or confirmed information | Profile → **Your facts** |
| Add writing examples or project context | Materials → **Writing & context** |
| Change search targets, limits, timing or model settings | **Preferences** / **Model connection** |
| Back up history | Preferences → **Your data and backups** |

**Pause** stops work at cancellation checkpoints; a request already sent to an employer cannot be undone. Unknown outcomes and email-verification challenges stay held for review. Verify them with the employer before recording a result.

For setup problems:

```bash
.venv/bin/python -m hireme doctor
.venv/bin/python -m hireme doctor --verify-login
```

These checks send no model requests or applications. See the [user guide](docs/guide.md#troubleshooting) for dashboard restarts and recovery.

## What to expect

- Answers use confirmed facts, scoped saved responses and approved personal sources. Missing legal, identity or demographic facts require your input.
- Model inference uses your selected remote provider. Claude Code and Codex subscription modes reject API-only authentication; paid API usage must be selected explicitly. Request counters measure this worker's usage, not your vendor balance.
- Submission budgets, durable intents and local locks limit work and preserve uncertain outcomes across restarts.
- Login walls, CAPTCHA, unknown questions, unsupported controls and ambiguous receipts need manual review. Discovering an ATS posting does not establish support for its application form.
- Optional Gmail handles recognized Greenhouse verification codes and batch reports. Optional platform connections start disabled and require independently verified access and transport.

The version is **0.5.0rc2**, a public release candidate for self-hosting. Stable-release validation remains incomplete; see the [release status](docs/public-release.md) for capability limits and remaining gates. Each applicant runs a private local instance.

## Guides and development

- [User guide](docs/guide.md): documents, drafts, questions, models, scheduling, backups and Gmail.
- [Raspberry Pi guide](docs/pi.md): setup, background services, restart and upgrades.
- [Documentation map](docs/README.md): architecture, platform connections and contributor references.
- [Contributing](CONTRIBUTING.md): local tests, packaging checks and safe fixture development.

This project began as a fork of [alecswang/please-hire-me](https://github.com/alecswang/please-hire-me). The original MIT license and attribution remain in [LICENSE](LICENSE).
