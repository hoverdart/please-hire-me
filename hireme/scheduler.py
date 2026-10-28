from __future__ import annotations

import os
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .util import private_dir

LABEL='com.pleasehireme.worker.v3'
SYSTEMD_TIMEOUT_DROPIN=Path('/usr/lib/systemd/user/service.d/10-timeout-abort.conf')


def _launchd_arguments(output):
    blocks=re.findall(r'^\s*arguments = \{\s*\n(.*?)^\s*\}',output,re.MULTILINE|re.DOTALL)
    return [line.strip() for line in blocks[0].splitlines()] if len(blocks)==1 else None


def _is_worker_command(arguments):
    return (isinstance(arguments,list) and len(arguments)==6
            and all(isinstance(value,str) for value in arguments)
            and arguments[1:4]==['-m','hireme','--data-dir'] and arguments[5]=='run')


def launchd_persistently_enabled(output):
    """Resolve launchd's persistent override, falling back to the plist default."""
    try:
        match=re.fullmatch(r'\s*disabled services = \{\s*(.*?)\s*\}\s*',output,re.DOTALL)
        if not match:return False
        overrides={}
        for line in match.group(1).splitlines():
            if not line.strip():continue
            entry=re.fullmatch(r'\s*"([^"\r\n]+)"\s*=>\s*(enabled|disabled)\s*',line)
            if not entry or entry[1] in overrides:return False
            overrides[entry[1]]=entry[2]
        path=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')
        if path.is_symlink() or path.stat().st_size>65536:return False
        saved=plistlib.loads(path.read_bytes())
        default=saved.get('Disabled',False)
        if saved.get('Label')!=LABEL or type(default) is not bool:return False
        return overrides.get(LABEL,'disabled' if default else 'enabled')=='enabled'
    except (OSError,ValueError,TypeError,plistlib.InvalidFileException):
        return False


def launchd_matches_loaded(output):
    """Check the loaded job, not merely the plist that may have changed since bootstrap."""
    path=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')
    try:
        if path.is_symlink() or path.stat().st_size>65536:return False
        saved=plistlib.loads(path.read_bytes())
        arguments=saved.get('ProgramArguments')
        interval=saved.get('StartInterval')
        directory=saved.get('WorkingDirectory')
        environment=saved.get('EnvironmentVariables')
        if (saved.get('Label')!=LABEL or not _is_worker_command(arguments)
                or type(interval) is not int or not isinstance(directory,str)
                or not isinstance(environment,dict)
                or any(not isinstance(key,str) or not isinstance(value,str) for key,value in environment.items())):
            return False
        directories=re.findall(r'^\s*working directory = (.+)$',output,re.MULTILINE)
        intervals=re.findall(r'^\s*run interval = (\d+) seconds$',output,re.MULTILINE)
        blocks=re.findall(r'^\s*environment = \{\s*\n(.*?)^\s*\}',output,re.MULTILINE|re.DOTALL)
        if len(blocks)!=1:return False
        loaded_environment={}
        for line in blocks[0].splitlines():
            entry=re.fullmatch(r'\s*([A-Za-z_][A-Za-z0-9_]*) => (.*)',line)
            if not entry or entry[1] in loaded_environment:return False
            loaded_environment[entry[1]]=entry[2]
        # launchd adds its own variables; only compare the complete set of
        # connection paths our installer can propagate, without exposing them.
        keys=('PATH','CLAUDE_CONFIG_DIR','CODEX_HOME','XDG_CONFIG_HOME')
        expected_environment={key:environment[key] for key in keys if key in environment}
        actual_environment={key:loaded_environment[key] for key in keys if key in loaded_environment}
        return (len(directories)==len(intervals)==1
                and _launchd_arguments(output)==arguments
                and directories[0]==directory and int(intervals[0])==interval
                and actual_environment==expected_environment)
    except (OSError,ValueError,TypeError,plistlib.InvalidFileException):
        return False


def _crontab(arguments, **kwargs):
    if arguments not in (['-l'], ['-']):raise ValueError('Unsupported cron operation')
    command=['crontab',*arguments]
    restricted=False
    if sys.platform=='linux':
        try:restricted=any(line.split(':',1)[0]=='NoNewPrivs' and line.split(':',1)[1].strip()=='1' for line in Path('/proc/self/status').read_text().splitlines())
        except OSError:pass
    if restricted:
        # crontab needs its normal setgid spool access. Run only this fixed
        # utility through the user manager, rather than relaxing the dashboard.
        runner=shutil.which('systemd-run');binary=shutil.which('crontab')
        if not runner or not binary:
            raise ValueError('The dashboard cannot manage cron here. Run schedule installation from the worker desktop terminal.')
        command=[runner,'--user','--quiet','--collect','--wait','--pipe','--',binary,*arguments]
    kwargs.setdefault('timeout',15)
    result=subprocess.run(command,**kwargs)
    if arguments==['-l'] and result.returncode and 'no crontab for' not in getattr(result,'stderr','').casefold():
        raise ValueError('The existing cron schedule could not be read. Check the user scheduler from the worker desktop terminal.')
    return result


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
        if r.returncode:raise ValueError('launchd rejected worker; run .venv/bin/python -m hireme daemon in a terminal')
        return str(path)
    if sys.platform=='linux' and shutil.which('systemctl') and (Path.home()/'.config/systemd/user/please-hire-me-worker.timer').exists():
        from .pi import install as install_pi
        return install_pi(store,repo,enable=True)['directory']
    if 24%hours:raise ValueError('Cron requires a schedule dividing 24 hours')
    r=_crontab(['-l'],capture_output=True,text=True)
    lines=[l for l in r.stdout.splitlines() if not l.endswith('# '+LABEL)]
    # Cron does not load the interactive shell's PATH; user-installed CLIs must
    # remain discoverable. Forward connection directories, never credentials.
    environment=['PATH='+os.environ.get('PATH','/usr/local/bin:/usr/bin:/bin')]
    environment.extend(k+'='+os.environ[k] for k in ('CLAUDE_CONFIG_DIR','CODEX_HOME','XDG_CONFIG_HOME') if os.environ.get(k))
    if any(c in value for value in environment for c in ('\n','\r','\x00')):raise ValueError('Scheduled worker environment paths cannot contain control characters')
    command='cd '+shlex.quote(str(repo.resolve()))+' && '+shlex.join(['env',*environment,sys.executable,'-m','hireme','--data-dir',str(store.root.resolve()),'run'])
    command=command.replace('%',r'\%')
    lines.append(f'0 */{hours} * * * {command} # {LABEL}')
    _crontab(['-'],input='\n'.join(lines)+'\n',text=True,check=True)
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
        r=_crontab(['-l'],capture_output=True,text=True)
        lines=[l for l in r.stdout.splitlines() if not l.endswith('# '+LABEL)]
        if lines!=r.stdout.splitlines():_crontab(['-'],input='\n'.join(lines)+'\n',text=True,check=True)


def _details(arguments, store, interval=None, **extra):
    root = None
    try:root = arguments[arguments.index('--data-dir') + 1]
    except (ValueError, IndexError):pass
    matches = None if store is None else root is not None and Path(root).is_absolute() and Path(root).resolve() == store.root.resolve()
    return {'installed': True, 'matches_applicant': matches, 'interval_hours': interval, **extra}


def _query_schedule(args):
    try:
        if args==['crontab','-l']:return _crontab(['-l'],capture_output=True,text=True,timeout=5)
        return subprocess.run(args,capture_output=True,text=True,timeout=5)
    except (OSError,subprocess.TimeoutExpired) as error:
        raise ValueError('The system scheduler could not be checked. Check your OS services or use .venv/bin/python -m hireme daemon.') from error


def _trusted_vendor_parent(path):
    # A writable ancestor could replace an otherwise root-owned file.
    for directory in path.parents:
        info=directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:return False
    return True


def _trusted_timeout_dropin(path):
    """Accept only the known vendor timeout policy, not arbitrary overrides."""
    try:
        if not _trusted_vendor_parent(path):return False
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(descriptor,'rb') as stream:
            info=os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022
                    or info.st_size>8192):return False
            contents=stream.read(8193).decode('utf-8')
        lines=[line.strip() for line in contents.splitlines() if line.strip() and not line.lstrip().startswith(('#',';'))]
        return lines==['[Service]','TimeoutStopFailureMode=abort']
    except (OSError,UnicodeError,ValueError):
        return False


def _systemd_loaded_matches(directory):
    """Reject stale, replaced or overridden units before trusting saved files."""
    properties=['LoadState','FragmentPath','NeedDaemonReload','DropInPaths']
    for suffix in ('service','timer'):
        unit='please-hire-me-worker.'+suffix
        fields=properties+(['Unit'] if suffix=='timer' else [])
        result=_query_schedule(['systemctl','--user','show','--all',
                                *('--property='+name for name in fields),unit])
        if result.returncode:return False
        values={}
        for line in result.stdout.splitlines():
            key,separator,value=line.partition('=')
            if not separator or key not in fields or key in values:return False
            values[key]=value
        expected=directory/unit
        dropins=values.get('DropInPaths')
        allowed_dropins=(not dropins or suffix=='service' and dropins==str(SYSTEMD_TIMEOUT_DROPIN)
                         and _trusted_timeout_dropin(SYSTEMD_TIMEOUT_DROPIN))
        if (set(values)!=set(fields) or expected.is_symlink()
                or values['LoadState']!='loaded' or values['NeedDaemonReload']!='no'
                or not allowed_dropins or values['FragmentPath']!=str(expected)
                or (suffix=='timer' and values['Unit']!='please-hire-me-worker.service')):
            return False
    return True


def status(store=None,cron_only=False):
    if sys.platform=='darwin' and not cron_only:
        p=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')
        if not p.is_file():return {'installed':False,'path':str(p)}
        try:
            data=plistlib.loads(p.read_bytes())
            interval=data.get('StartInterval')
            arguments=data.get('ProgramArguments',[])
            if data.get('Label')!=LABEL or not _is_worker_command(arguments):
                return {'installed':True,'matches_applicant':False,'path':str(p),'kind':'launchd','error':'The saved schedule could not be verified.'}
            return _details(arguments,store,interval / 3600 if isinstance(interval,(int,float)) else None,path=str(p),kind='launchd')
        except (ValueError,TypeError,plistlib.InvalidFileException):
            return {'installed':True,'matches_applicant':False,'path':str(p),'error':'The saved schedule could not be read.'}
    if sys.platform=='linux' and not cron_only and shutil.which('systemctl'):
        r=_query_schedule(['systemctl','--user','is-enabled','please-hire-me-worker.timer'])
        directory=Path.home()/'.config/systemd/user'
        if not r.returncode or (directory/'please-hire-me-worker.timer').exists():
            try:
                commands=[line.split('=',1)[1] for line in (directory/'please-hire-me-worker.service').read_text().splitlines() if line.startswith('ExecStart=')]
                intervals=[line.split('=',1)[1] for line in (directory/'please-hire-me-worker.timer').read_text().splitlines() if line.startswith('OnUnitInactiveSec=')]
                if len(commands)!=1 or len(intervals)!=1:raise ValueError('Ambiguous worker unit')
                command=commands[0];interval=intervals[0]
                arguments=shlex.split(command.replace('%%','%'))
                if not _is_worker_command(arguments) or not _systemd_loaded_matches(directory):
                    return {'installed':True,'matches_applicant':False,'kind':'systemd',
                            'error':'The loaded service or timer could not be verified.'}
                return _details(arguments,store,float(interval[:-1]) if interval.endswith('h') else None,kind='systemd',enabled=not r.returncode,timer='please-hire-me-worker.timer',loaded_matches_saved=True)
            except (OSError,ValueError,StopIteration):
                return {'installed':True,'matches_applicant':False,'kind':'systemd','error':'The saved service could not be read.'}
        # A loaded timer can outlive a removed or replaced unit file. Do not
        # silently install cron alongside it or claim its ownership is known.
        loaded=_query_schedule(['systemctl','--user','show','--property=LoadState','please-hire-me-worker.timer'])
        if loaded.returncode==0 and loaded.stdout.strip()=='LoadState=loaded':
            return {'installed':True,'matches_applicant':False,'kind':'systemd',
                    'error':'The loaded timer has no verifiable saved schedule.'}
    if not shutil.which('crontab'):return {'installed':False}
    r=_query_schedule(['crontab','-l'])
    records=[]
    for line in r.stdout.splitlines():
        if not line.endswith('# '+LABEL):continue
        try:
            fields=shlex.split(line.replace(r'\%','%'))
            interval=int(fields[1][2:]) if fields[1].startswith('*/') else None
            records.append(_details(fields[5:],store,interval,kind='cron'))
        except (ValueError,IndexError):
            records.append({'installed':True,'matches_applicant':False,'kind':'cron','error':'The saved cron entry could not be read.'})
    if records:
        result={**records[0],'installed_count':len(records)}
        if len(records)>1:
            # Inspect every entry before allowing installation/uninstallation.
            # A second applicant's history must not be hidden by the first row.
            result['matches_applicant']=all(record.get('matches_applicant') is True for record in records) if store is not None else None
            result['error']='Multiple worker cron entries are installed. Reapply the schedule from its original applicant instance.'
        return result
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
            raise ValueError('Schedule activation failed. Check the OS scheduler or run .venv/bin/python -m hireme daemon. Application pause settings were not changed.') from error
        interval=store.settings()['schedule_hours']
        store.event('schedule_installed','worker',{'interval_hours':interval})
    return {'message':f'Schedule applied: every {interval} hours. Paused applications stay paused.', 'location':location,'interval_hours':interval}


def check_owner(store,cron_only=False):
    current=status(store,cron_only=cron_only)
    if sys.platform=='darwin' and not cron_only:
        loaded=_query_schedule(['launchctl','print',f'gui/{os.getuid()}/{LABEL}'])
        if loaded.returncode==0:
            arguments=_launchd_arguments(loaded.stdout)
            if not _is_worker_command(arguments) or not _details(arguments,store).get('matches_applicant'):
                raise ValueError('The loaded schedule belongs to another applicant or cannot be verified. Remove that schedule from its original instance before replacing it.')
        elif loaded.returncode!=113:
            raise ValueError('The loaded schedule could not be checked. Check your OS services before replacing it.')
    records=[current]
    if current.get('kind')=='systemd' and shutil.which('crontab'):
        records.append(status(store,cron_only=True))
    if any(record['installed'] and not record.get('matches_applicant') for record in records):
        raise ValueError('The installed schedule belongs to another applicant or cannot be verified. Remove that schedule from its original instance before replacing it.')
    return current


def start_scheduled_worker(store):
    """Let systemd own both immediate and timed runs of the same worker unit."""
    current=check_owner(store)
    if current.get('kind')!='systemd':return False
    if not current.get('enabled'):
        raise ValueError('Enable the worker timer before starting scheduled applications.')
    subprocess.run(['systemctl','--user','start','--no-block','please-hire-me-worker.service'],check=True)
    return True
