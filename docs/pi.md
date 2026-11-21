# Raspberry Pi guide

Run the destination commands **on the Pi**. The target is a Pi 4 with 4 GB RAM, 64-bit Raspberry Pi OS, reliable power and system Chromium. Model inference runs remotely. Use Raspberry Pi OS with Desktop if you need visible employer sign-in or human verification through Raspberry Pi Connect.

## Install and set up

```bash
sudo apt update
sudo apt install -y git python3-venv chromium
git clone https://github.com/hoverdart/please-hire-me.git
cd ~/please-hire-me
```

Check out the branch or revision you intend to run before installing. Use `HIREME_PI=1 ./setup.sh` for a fresh applicant workspace. Setup selects system Chromium, headless application browsing and two discovery workers. Complete the applicant checklist, connect your selected model provider on the Pi, and finish paused.

### Move an existing workspace

On the source machine, pause submissions, remove the schedule, and wait for active work to finish. [Create and validate a private history backup](guide.md#back-up-and-move-your-data), then transfer it to the Pi over a private channel. Keep the source paused during the cutover.

On the Pi, install the code before creating the destination workspace. Restore requires a new directory.

```bash
HIREME_PI=1 ./setup.sh --install-only
.venv/bin/python -m hireme restore /absolute/private/history.zip
.venv/bin/python -m hireme pi configure
.venv/bin/python -m hireme dashboard --open
```

If the Pi already has a workspace at the default path, restore with `--data-dir /absolute/path/to/new-workspace` before the subcommand. Include that same option on every subsequent command, including `pi install`. Use a separate OS user if both workspaces need their own background services.

Confirm that history and documents are present. Reconnect the model provider and any Gmail or platform integrations you use. You can transfer employer passwords separately through the encrypted transfer described in the [backup guide](guide.md#back-up-and-move-your-data). If you use a commissioned rollout, review the saved scope and evidence and recommission it on the Pi before running a batch. The rollout approval file is local to each installation.

For a compatible Ashby form that presents verification after the final click, Preferences can keep the same application browser open while you complete the challenge. Turn off background browsing, set **Wait for human verification** to between 1 and 600 seconds, and stay at the Pi desktop through Connect while the batch runs. Zero turns the wait off. Pause or timeout leaves the recorded outcome uncertain. Verify that outcome with the employer before applying again.

Visible sign-in and verification need access to the Pi's desktop display. If the user service cannot open a browser window, stop the dashboard service and run `.venv/bin/python -m hireme dashboard --open` from a terminal inside the Pi desktop. Close that foreground dashboard before restarting the service.

## Verify and install background services

The initial dashboard runs in the foreground. Stop it with **Ctrl+C**, then check the destination as the user who will run the services:

```bash
cd ~/please-hire-me
.venv/bin/python -m hireme pi preflight
.venv/bin/python -m hireme doctor --verify-login
```

Check that `userspace_bits` is 64, `chromium_launches` and `systemd_user_available` are true, and setup is complete. The Chromium check opens a temporary headless session with a local test page. If the user manager is unavailable, run setup from a login session on the Pi under the account that will own the service.

If system Chromium repeatedly fails to launch, you can install Playwright's browser with `.venv/bin/python -m playwright install chromium` and choose **Bundled Chromium** under **Browser** in Preferences. Check that the selected browser can prepare a draft while submissions are paused. Pi preflight continues to report the state of system Chromium.

```bash
.venv/bin/python -m hireme pi install
.venv/bin/python -m hireme open-dashboard
.venv/bin/python -m hireme schedule status
```

Installation enables a user-owned dashboard service and worker timer. It retires this applicant's previous cron schedule to avoid duplicate scheduling. After fresh activation or reboot, the first automatic run waits the saved interval. The dashboard's **Start scheduled applications** action also requests an immediate first batch. Confirm the dashboard and schedule work while submissions are paused. When ready to test job discovery or draft preparation, use **Find opportunities** or **Prepare drafts** in the dashboard. To let user services run after logout/reboot without a desktop login:

```bash
loginctl enable-linger "$USER"
```

The services configure memory limits, but Raspberry Pi OS can boot with the memory controller disabled. Check `memory_controller_available` in `hireme pi preflight`. If false, preserve `/boot/firmware/cmdline.txt`, append `cgroup_enable=memory` to its existing single line, and reboot while application work is paused. The [Raspberry Pi kernel maintainers](https://github.com/raspberrypi/linux/issues/6980) explain this opt-in; `cgroup_memory=1` is unnecessary on current kernels. Verify that `/sys/fs/cgroup/cgroup.controllers` contains `memory` afterward. `/proc/cgroups` describes the older v1 interface and cannot establish v2 availability.

Verify the active dashboard's actual kernel limit as well:

```bash
group=$(systemctl --user show please-hire-me-dashboard.service -p ControlGroup --value)
cat "/sys/fs/cgroup${group}/memory.max"
```

The default dashboard limit is `805306368` bytes. A configured `MemoryMax` property alone does not prove enforcement. The Pi health monitor reports `memory_controller_unavailable` when the controller is absent.

Preflight and health also read `vcgencmd get_throttled`. A historical warning is distinct from a current fault; the [firmware flag reference](https://www.raspberrypi.com/documentation/computers/os.html#vcgencmd) lists both. Verify the power supply and cable if undervoltage is recorded. The [recommended Pi 4 supply](https://www.raspberrypi.com/documentation/computers/getting-started.html#power-supply) provides 5 V at 3 A at the plug. After remediation, observe the normal workload for recurrence instead of treating a reboot's cleared flags as proof.

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

A scheduled run that stops because submissions are paused exits successfully and records a paused batch. A paused batch with no application attempts keeps a local summary without sending an email; a pause after attempts still reports their outcomes. Setup errors and other blocked runs still return a failure status.

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
git pull --ff-only
HIREME_PI=1 ./setup.sh --install-only
systemctl --user start please-hire-me-dashboard.service
.venv/bin/python -m hireme doctor
.venv/bin/python -m hireme open-dashboard
```

Run each step only after the preceding one succeeds. If Git reports local changes or a diverged branch, resolve those before continuing; do not discard them to force an update. The install-only flag keeps the existing workspace settings and leaves dashboard startup to systemd.

Confirm history and documents in the dashboard. Restore the saved interval with **Preferences → Apply saved interval**, check the installed schedule, then resume when ready. A code update does not require reimporting your applicant data. Keep off-device backups, and check available disk space before large local test runs.
