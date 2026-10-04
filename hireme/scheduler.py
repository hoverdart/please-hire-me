from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .util import private_dir

LABEL='com.pleasehireme.worker.v3'


def install(store,repo):
    check_owner(store)
    if store.missing_setup() or not store.settings()['onboarding_complete']:
        raise ValueError('Complete onboarding before installing automatic runs')
    hours=store.settings()['schedule_hours']
    if sys.platform=='darwin':
        directory=Path.home()/'Library/LaunchAgents';directory.mkdir(parents=True,exist_ok=True)
        path=directory/(LABEL+'.plist')
        if path.is_symlink():raise ValueError('Unsafe LaunchAgent path')
        logs=private_dir(store.root/'logs')
        payload={'Label':LABEL,'ProgramArguments':[sys.executable,'-m','hireme','--data-dir',str(store.root.resolve()),'run'],
          'WorkingDirectory':str(repo.resolve()),'StartInterval':hours*3600,'RunAtLoad':False,
          'StandardOutPath':str(logs/'worker.out.log'),'StandardErrorPath':str(logs/'worker.err.log'),
          'EnvironmentVariables':{'PATH':os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin'),
              **{k:os.environ[k] for k in ('CLAUDE_CONFIG_DIR','CODEX_HOME','XDG_CONFIG_HOME') if os.environ.get(k)}}}
        # Connection secrets stay in a private file, never argv or logs.
        with tempfile.NamedTemporaryFile(dir=directory,prefix='.'+LABEL,delete=False) as f:
            temporary=Path(f.name)
            try:
                f.write(plistlib.dumps(payload));f.flush();os.fsync(f.fileno())
                os.replace(temporary,path)
            finally:temporary.unlink(missing_ok=True)
        subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{LABEL}'],capture_output=True)
        r=subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],capture_output=True)
        if r.returncode:raise ValueError('launchd rejected worker; run hireme daemon in a terminal')
        return str(path)
    if sys.platform=='linux' and shutil.which('systemctl') and (Path.home()/'.config/systemd/user/please-hire-me-worker.timer').exists():
        from .pi import install as install_pi
        return install_pi(store,repo,enable=True)['directory']
    if 24%hours:raise ValueError('Cron requires a schedule dividing 24 hours')
    r=subprocess.run(['crontab','-l'],capture_output=True,text=True)
    lines=[l for l in r.stdout.splitlines() if not l.endswith('# '+LABEL)]
    command='cd '+shlex.quote(str(repo.resolve()))+' && '+shlex.join([sys.executable,'-m','hireme','--data-dir',str(store.root.resolve()),'run'])
    command=command.replace('%',r'\%')
    lines.append(f'0 */{hours} * * * {command} # {LABEL}')
    subprocess.run(['crontab','-'],input='\n'.join(lines)+'\n',text=True,check=True)
    return 'cron'


def uninstall(cron_only=False,store=None):
    if store is not None:check_owner(store,cron_only=cron_only)
    if sys.platform=='darwin':
        subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{LABEL}'],capture_output=True)
        path=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')
        if path.exists() and not path.is_symlink():
            payload=plistlib.loads(path.read_bytes())
            if payload.get('Label')==LABEL:path.unlink()
    else:
        if not cron_only and sys.platform=='linux' and shutil.which('systemctl') and (Path.home()/'.config/systemd/user/please-hire-me-worker.timer').exists():
            subprocess.run(['systemctl','--user','disable','--now','please-hire-me-worker.timer'],capture_output=True,check=True)
        if not shutil.which('crontab'):return
        r=subprocess.run(['crontab','-l'],capture_output=True,text=True)
        lines=[l for l in r.stdout.splitlines() if not l.endswith('# '+LABEL)]
        subprocess.run(['crontab','-'],input='\n'.join(lines)+'\n',text=True,check=True)


def _details(arguments, store, interval=None, **extra):
    root = None
    try:root = arguments[arguments.index('--data-dir') + 1]
    except (ValueError, IndexError):pass
    matches = None if store is None else root is not None and Path(root).is_absolute() and Path(root).resolve() == store.root.resolve()
    return {'installed': True, 'matches_applicant': matches, 'interval_hours': interval, **extra}


def _query_schedule(args):
    try:return subprocess.run(args,capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.TimeoutExpired) as error:
        raise ValueError('The system scheduler could not be checked. Check your OS services or use hireme daemon.') from error


def status(store=None,cron_only=False):
    if sys.platform=='darwin' and not cron_only:
        p=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')
        if not p.is_file():return {'installed':False,'path':str(p)}
        try:
            data=plistlib.loads(p.read_bytes())
            interval=data.get('StartInterval')
            return _details(data.get('ProgramArguments',[]),store,interval / 3600 if isinstance(interval,(int,float)) else None,path=str(p),kind='launchd')
        except (ValueError,TypeError,plistlib.InvalidFileException):
            return {'installed':True,'matches_applicant':False,'path':str(p),'error':'The saved schedule could not be read.'}
    if sys.platform=='linux' and not cron_only and shutil.which('systemctl'):
        r=_query_schedule(['systemctl','--user','is-enabled','please-hire-me-worker.timer'])
        directory=Path.home()/'.config/systemd/user'
        if not r.returncode or (directory/'please-hire-me-worker.timer').exists():
            try:
                command=next(line.split('=',1)[1] for line in (directory/'please-hire-me-worker.service').read_text().splitlines() if line.startswith('ExecStart='))
                interval=next(line.split('=',1)[1] for line in (directory/'please-hire-me-worker.timer').read_text().splitlines() if line.startswith('OnUnitInactiveSec='))
                return _details(shlex.split(command.replace('%%','%')),store,float(interval[:-1]) if interval.endswith('h') else None,kind='systemd',enabled=not r.returncode,timer='please-hire-me-worker.timer')
            except (OSError,ValueError,StopIteration):
                return {'installed':True,'matches_applicant':False,'kind':'systemd','error':'The saved service could not be read.'}
    if not shutil.which('crontab'):return {'installed':False}
    r=_query_schedule(['crontab','-l'])
    for line in r.stdout.splitlines():
        if not line.endswith('# '+LABEL):continue
        try:
            fields=shlex.split(line.replace(r'\%','%'))
            interval=int(fields[1][2:]) if fields[1].startswith('*/') else None
            return _details(fields[5:],store,interval,kind='cron')
        except (ValueError,IndexError):
            return {'installed':True,'matches_applicant':False,'kind':'cron','error':'The saved cron entry could not be read.'}
    return {'installed':False}


def apply_saved_interval(store, repo):
    from .store import worker_lock
    with worker_lock(store.root):
        current=check_owner(store)
        if store.missing_setup() or not store.settings()['onboarding_complete']:
            raise ValueError('Complete onboarding before installing automatic runs')
        try:
            if store.settings()['deployment']=='pi' or current.get('kind')=='systemd':
                if sys.platform!='linux' or not shutil.which('systemctl'):
                    raise ValueError('Pi schedules require Linux with systemd.')
                from .pi import install as install_pi, WORKER
                location=install_pi(store,repo,enable=False)['directory']
                if shutil.which('crontab'):uninstall(cron_only=True,store=store)
                subprocess.run(['systemctl','--user','daemon-reload'],check=True)
                subprocess.run(['systemctl','--user','enable','--now',WORKER+'.timer'],check=True)
            else:location=install(store,repo)
        except (OSError,subprocess.CalledProcessError) as error:
            raise ValueError('Schedule activation failed. Check the OS scheduler or run hireme daemon. Application pause settings were not changed.') from error
        interval=store.settings()['schedule_hours']
        store.event('schedule_installed','worker',{'interval_hours':interval})
    return {'message':f'Schedule applied: every {interval} hours. Paused applications stay paused.', 'location':location,'interval_hours':interval}


def check_owner(store,cron_only=False):
    current=status(store,cron_only=cron_only)
    records=[current]
    if current.get('kind')=='systemd' and shutil.which('crontab'):
        records.append(status(store,cron_only=True))
    if any(record['installed'] and not record.get('matches_applicant') for record in records):
        raise ValueError('The installed schedule belongs to another applicant or cannot be verified. Remove that schedule from its original instance before replacing it.')
    return current
