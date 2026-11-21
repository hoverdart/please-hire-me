"""User-owned systemd deployment; previewable without changing services."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .util import private_dir

WORKER = 'please-hire-me-worker'
DASHBOARD = 'please-hire-me-dashboard'
CONNECTION_PATHS = ('CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'XDG_CONFIG_HOME')


def unit_directory():
    config = Path(os.environ.get('XDG_CONFIG_HOME', ''))
    return (config if config.is_absolute() else Path.home()/'.config')/'systemd/user'


def memory_controller_available():
    """Inspect cgroup v2; /proc/cgroups reports the obsolete v1 interface."""
    try:
        return 'memory' in Path('/sys/fs/cgroup/cgroup.controllers').read_text().split()
    except OSError:
        return False


def power_status():
    """Return only bounded firmware flags, with current and historical state separate."""
    binary=shutil.which('vcgencmd')
    if not binary:return {'available':False}
    try:
        result=subprocess.run([binary,'get_throttled'],capture_output=True,text=True,timeout=5)
        match=re.fullmatch(r'throttled=0x([0-9a-fA-F]{1,8})\s*',result.stdout)
        if result.returncode or not match:return {'available':False}
        flags=int(match[1],16)
        if flags&~0xf000f:return {'available':False}
        return {'available':True,'flags':hex(flags),'current_fault':bool(flags&0xf),
                'undervoltage_recorded':bool(flags&0x10000),
                'throttling_recorded':bool(flags&0xe0000)}
    except (OSError,subprocess.SubprocessError):
        return {'available':False}


def _quote(value):
    value = str(value)
    if '\n' in value or '\r' in value or '\x00' in value:
        raise ValueError('Service paths cannot contain control characters')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def _literal_path(value):
    value=str(value)
    _quote(value)  # Apply the same control-character validation.
    return value.replace('%','%%')


def service_units(store, repo, executable=None):
    python = executable or sys.executable
    arguments = (python, '-m', 'hireme', '--data-dir', store.root.resolve())
    # The ":" prefix disables systemd's dollar expansion within literal paths.
    command = ':' + ' '.join(_quote(value) for value in arguments)
    local_bin = str(Path.home()/'.local/bin')
    path = os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')
    if path.split(':', 1)[0] != local_bin:
        path = local_bin + ':' + path
    environment = {'PATH': path}
    for key in (*CONNECTION_PATHS, 'PLAYWRIGHT_BROWSERS_PATH'):
        if os.environ.get(key):
            environment[key] = os.environ[key]
    environment_line = 'Environment=' + ' '.join(_quote(f'{key}={value}') for key,value in environment.items())
    common = ('[Unit]\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\n'
              # WorkingDirectory is a literal path directive, unlike ExecStart
              # arguments. Quotes become part of the path and invalidate it.
              f'WorkingDirectory={_literal_path(repo.resolve())}\nUMask=0077\nNoNewPrivileges=yes\n'
              'TimeoutStopSec=30\nKillMode=control-group\n'
              f'{environment_line}\n'
              'UnsetEnvironment=ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN\n')
    # The app enforces the saved cycle deadline (up to 24h). Leave time for
    # final bookkeeping and keep an outer limit if a child process stalls.
    worker = common + f'Type=oneshot\nExecStart={command} run\nTimeoutStartSec=25h\nMemoryHigh=1536M\nMemoryMax=2G\nTasksMax=256\nOOMPolicy=stop\n'
    dashboard = (common + f'Type=simple\nExecStart={command} dashboard --hide-token\nRestart=on-failure\nRestartSec=5\nMemoryHigh=256M\nMemoryMax=768M\nTasksMax=128\nOOMPolicy=stop\n'
                 '\n[Install]\nWantedBy=default.target\n')
    hours = store.settings()['schedule_hours']
    timer = ('[Unit]\nDescription=Scheduled application batches\n\n[Timer]\n'
             f'OnActiveSec={hours}h\nOnUnitInactiveSec={hours}h\n'
             f'Unit={WORKER}.service\n\n[Install]\nWantedBy=timers.target\n')
    return {WORKER+'.service': worker, WORKER+'.timer': timer, DASHBOARD+'.service': dashboard}


def install(store, repo, directory=None, enable=False, *, dashboard=True):
    if enable and (sys.platform != 'linux' or not shutil.which('systemctl')):
        raise ValueError('Service activation requires Linux with systemd')
    live_directory = unit_directory()
    directory = Path(directory or live_directory).expanduser()
    if enable:
        if directory.absolute() != live_directory.absolute():
            raise ValueError('Systemd activation requires the user unit directory. Use pi units to export to another directory.')
        if not _user_systemd_available():
            raise ValueError('The systemd user manager is unavailable. Run setup from a login session on the Pi.')
        from .scheduler import check_owner
        check_owner(store)
    elif directory.resolve() == live_directory.resolve():
        raise ValueError('Choose a separate export directory with pi units --directory.')
    units = service_units(store, repo)
    if not dashboard:
        del units[DASHBOARD+'.service']
    directory = private_dir(directory)
    for name in units:
        if (directory/name).is_symlink():
            raise ValueError('Unsafe systemd unit path')
    # Replace complete files so a failed write cannot truncate an installed unit.
    for name, contents in units.items():
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(contents)
                stream.flush()
                os.replace(temporary, directory/name)
            finally:
                temporary.unlink(missing_ok=True)
    if enable:
        from .scheduler import uninstall
        subprocess.run(['systemctl','--user','daemon-reload'],check=True,timeout=30)
        # Keep the legacy schedule if the user manager or dashboard fails first.
        if dashboard:
            subprocess.run(['systemctl','--user','enable','--now',DASHBOARD+'.service'],check=True,timeout=30)
        if shutil.which('crontab'):
            uninstall(cron_only=True,store=store)
        subprocess.run(['systemctl','--user','enable','--now',WORKER+'.timer'],check=True,timeout=30)
    return {'directory':str(directory),'enabled':enable,'units':list(units)}


def configure(store):
    store.update_settings({'live_enabled':False,'browser_channel':'system-chromium','headless':True,
                           'human_verification_wait_seconds':0,'human_verification_handoff':False,
                           'discovery_workers':2,'deployment':'pi'})
    return {'paused':True,'browser':'system-chromium','discovery_workers':2}


def _chromium_launches(binary):
    if not binary:
        return False
    # Bound the driver's startup and shutdown as well as the browser launch.
    probe = '''
import sys
from playwright.sync_api import sync_playwright

with sync_playwright() as browser:
    context = browser.chromium.launch_persistent_context(
        sys.argv[2], executable_path=sys.argv[1], headless=True, timeout=30000)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.set_content('<title>HireMeMaybe browser check</title>', timeout=5000)
        ready = page.title() == 'HireMeMaybe browser check'
    finally:
        context.close()
raise SystemExit(0 if ready else 1)
'''
    try:
        with tempfile.TemporaryDirectory(prefix='hireme-chromium-') as profile:
            result = subprocess.run([sys.executable, '-I', '-c', probe, binary, profile],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _user_systemd_available():
    try:
        return subprocess.run(['systemctl','--user','show','--property=Version','--value'],
                              capture_output=True,timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def preflight(store):
    binary = shutil.which('chromium') or shutil.which('chromium-browser')
    systemd = sys.platform == 'linux' and bool(shutil.which('systemctl'))
    result = {'platform':sys.platform,'architecture':platform.machine(),'python':platform.python_version(),
              'userspace_bits':64 if sys.maxsize > 2**32 else 32,
              'chromium':bool(binary),'chromium_launches':_chromium_launches(binary),
              'claude':bool(shutil.which('claude')),'codex':bool(shutil.which('codex')),
              'systemd':systemd,'systemd_user_available':systemd and _user_systemd_available(),
              'data_directory':str(store.root),'paused':not store.settings()['live_enabled'],
              'missing_setup':store.missing_setup(),
              'memory_controller_available':memory_controller_available(),'power':power_status()}
    mem = Path('/proc/meminfo')
    if mem.is_file():
        for line in mem.read_text().splitlines():
            if line.startswith('MemTotal:'):
                result['memory_mib']=int(line.split()[1])//1024;break
    return result
