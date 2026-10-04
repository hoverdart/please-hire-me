"""User-owned systemd deployment; previewable without changing services."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .util import private_dir

WORKER = 'please-hire-me-worker'
DASHBOARD = 'please-hire-me-dashboard'


def _quote(value):
    value = str(value)
    if '\n' in value or '\r' in value or '\x00' in value:
        raise ValueError('Service paths cannot contain control characters')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def service_units(store, repo, executable=None):
    python = executable or sys.executable
    command = ' '.join(_quote(x) for x in (python, '-m', 'hireme', '--data-dir', store.root.resolve()))
    common = ('[Unit]\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\n'
              f'WorkingDirectory={_quote(repo.resolve())}\nUMask=0077\nNoNewPrivileges=yes\n'
              'TimeoutStopSec=30\nKillMode=control-group\n'
              f'Environment={_quote("PATH="+str(Path.home()/".local/bin")+":"+os.environ.get("PATH","/usr/local/bin:/usr/bin:/bin"))}\n'
              'UnsetEnvironment=ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN\n')
    worker = common + f'Type=oneshot\nExecStart={command} run\nTimeoutStartSec=4h\n'
    dashboard = (common + f'Type=simple\nExecStart={command} dashboard\nRestart=on-failure\nRestartSec=5\n'
                 '\n[Install]\nWantedBy=default.target\n')
    timer = ('[Unit]\nDescription=Scheduled application batches\n\n[Timer]\n'
             f'OnBootSec=5min\nOnUnitInactiveSec={store.settings()["schedule_hours"]}h\n'
             f'Unit={WORKER}.service\n\n[Install]\nWantedBy=timers.target\n')
    return {WORKER+'.service': worker, WORKER+'.timer': timer, DASHBOARD+'.service': dashboard}


def install(store, repo, directory=None, enable=False):
    if enable and (sys.platform != 'linux' or not shutil.which('systemctl')):
        raise ValueError('Service activation requires Linux with systemd')
    if enable:
        from .scheduler import check_owner
        check_owner(store)
    directory = private_dir(directory or Path.home()/'.config/systemd/user')
    for name, contents in service_units(store, repo).items():
        path = directory/name
        if path.is_symlink():
            raise ValueError('Unsafe systemd unit path')
        path.write_text(contents);os.chmod(path, 0o600)
    if enable:
        # Retire this applicant's previous cron scheduler before enabling the timer.
        from .scheduler import uninstall
        if shutil.which('crontab'):uninstall(cron_only=True,store=store)
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable','--now',DASHBOARD+'.service',WORKER+'.timer'],check=True)
    return {'directory':str(directory),'enabled':enable,'units':list(service_units(store,repo))}


def configure(store):
    store.update_settings({'live_enabled':False,'browser_channel':'system-chromium','headless':True,'discovery_workers':2,'deployment':'pi'})
    return {'paused':True,'browser':'system-chromium','discovery_workers':2}


def preflight(store):
    result = {'platform':sys.platform,'architecture':platform.machine(),'python':platform.python_version(),
              'chromium':bool(shutil.which('chromium') or shutil.which('chromium-browser')),
              'claude':bool(shutil.which('claude')),'systemd':bool(shutil.which('systemctl')),
              'data_directory':str(store.root),'paused':not store.settings()['live_enabled'],
              'missing_setup':store.missing_setup()}
    mem = Path('/proc/meminfo')
    if mem.is_file():
        for line in mem.read_text().splitlines():
            if line.startswith('MemTotal:'):
                result['memory_mib']=int(line.split()[1])//1024;break
    return result
