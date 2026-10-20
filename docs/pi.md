# Raspberry Pi guide

Run these commands **on the Pi**, using its desktop or a Raspberry Pi Connect terminal. The target is a Pi 4 with 4 GB RAM, 64-bit Raspberry Pi OS, reliable power and system Chromium. Model inference runs remotely.

## Install and set up

```bash
sudo apt update
sudo apt install -y git python3-venv chromium
git clone https://github.com/hoverdart/please-hire-me.git
cd ~/please-hire-me
HIREME_PI=1 ./setup.sh
```

Setup selects system Chromium, headless application browsing and two discovery workers. Complete the applicant checklist, authenticate your selected model CLI on the Pi, and finish paused. Use the Pi's browser through Connect; sign-in on another computer does not connect the Pi's CLI or Gmail integration.

The initial dashboard runs in the foreground. Stop it with **Ctrl+C** before installing background services:

```bash
cd ~/please-hire-me
.venv/bin/python -m hireme pi preflight
.venv/bin/python -m hireme pi install
.venv/bin/python -m hireme open-dashboard
```

Installation enables a user-owned dashboard service and worker timer. It retires this applicant's previous cron schedule to avoid duplicate scheduling. Check setup before resuming applications. To let user services run after logout/reboot without a desktop login:

```bash
loginctl enable-linger "$USER"
```

The dashboard is loopback-only. Access it through the Pi desktop in Connect. Disconnecting Connect leaves background services running.

**Start scheduled applications** installs or updates the worker timer without starting another dashboard on the same port. Wait for an active search or batch to finish first. On a Pi, the first batch and subsequent scheduled batches use the same worker service. Do not also leave `hireme daemon` running alongside the timer.

The sandboxed dashboard uses a short-lived user service to check and retire its legacy cron entry, since `crontab` needs its normal spool permissions. Other cron entries are preserved, and the dashboard keeps `NoNewPrivileges=yes`. An unreadable cron schedule blocks installation instead of being treated as an empty schedule.

## Restart or stop the dashboard

```bash
systemctl --user restart please-hire-me-dashboard.service
cd ~/please-hire-me
.venv/bin/python -m hireme open-dashboard
```

To stop it and start it separately:

```bash
systemctl --user stop please-hire-me-dashboard.service
systemctl --user start please-hire-me-dashboard.service
```

Check its state:

```bash
systemctl --user is-active please-hire-me-dashboard.service
systemctl --user status please-hire-me-dashboard.service
```

Dashboard restart controls the web interface. To pause application work, use the workspace's **Pause** control or `.venv/bin/python -m hireme pause`.

If you launched the dashboard manually instead, **Ctrl+C** in that terminal stops it. Run `.venv/bin/python -m hireme dashboard --open` to start another foreground session. Stop the background service before starting a foreground dashboard on its port. Closing the browser does not stop either process.

## Check scheduling and logs

```bash
cd ~/please-hire-me
.venv/bin/python -m hireme schedule status
systemctl --user status please-hire-me-worker.timer
journalctl --user -u please-hire-me-worker.service -n 50
```

Existing installations may use cron for applications and systemd only for the dashboard. Check `hireme schedule status` before changing scheduling; a missing worker timer does not establish that scheduling is absent. Use one scheduler for the applicant.

A scheduled run that stops because submissions are paused exits successfully and records a paused batch. Setup errors and other blocked runs still return a failure status.

Dashboard service units use `--hide-token` to keep the access URL out of service logs. Reopen it with `hireme open-dashboard` in the Pi desktop session. Keep logs private.

## Update an existing checkout

Pause submissions, remove the schedule, wait for active work to finish and make a validated private backup before upgrading. The [backup guide](guide.md#back-up-and-move-your-data) explains validation and restoration.

```bash
cd ~/please-hire-me
.venv/bin/python -m hireme pause
.venv/bin/python -m hireme schedule uninstall
# Wait for active work to finish; choose a NEW backup filename:
.venv/bin/python -m hireme backup /absolute/private/history-before-update.zip
```

After validating the backup:

```bash
systemctl --user stop please-hire-me-dashboard.service
cd ~/please-hire-me
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements/runtime.lock
.venv/bin/python -m pip install --no-deps -e .
systemctl --user start please-hire-me-dashboard.service
.venv/bin/python -m hireme doctor
.venv/bin/python -m hireme open-dashboard
```

Run each step only after the preceding one succeeds. If Git reports local changes or a diverged branch, resolve those before continuing; do not discard them to force an update. Avoid rerunning `setup.sh` over an active dashboard, since it launches a foreground server.

Confirm history and documents in the dashboard. Restore the saved interval with **Preferences → Apply saved interval**, check the installed schedule, then resume when ready. A code update does not require reimporting your applicant data. Keep off-device backups, and check available disk space before large local test runs.
