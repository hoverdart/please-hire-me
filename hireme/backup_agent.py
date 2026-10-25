"""Mac-owned encrypted Pi backups and health monitoring, independent of Gmail."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

from .health import _age
from .storage import require_space
from .store import worker_lock
from .util import atomic_json, now, private_dir

LABEL='com.pleasehireme.operations'
ARCHIVE=re.compile(r'backup-\d{8}T\d{6}Z-[a-f0-9]{8}\.zip\.age')


def load_config(path):
    path=Path(path).expanduser().absolute()
    if path.is_symlink() or not path.is_file() or path.stat().st_size>16384 or path.stat().st_mode&0o077:
        raise ValueError('Use a private regular operations configuration file')
    config=json.loads(path.read_text())
    required={'host','identity','age_identity','age_binary','ssh_binary','destination','state_directory','recipient'}
    if not isinstance(config,dict) or set(config)!=required:raise ValueError('Invalid operations configuration')
    if not isinstance(config['host'],str) or not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9][A-Za-z0-9.-]*',config['host']):
        raise ValueError('Use an explicit SSH user and host')
    if not isinstance(config['recipient'],str) or not re.fullmatch(r'age1[a-z0-9]{50,100}',config['recipient']):
        raise ValueError('Use the matching age public recipient')
    for key in required-{'host','recipient'}:
        value=config[key]
        if not isinstance(value,str) or not Path(value).is_absolute() or any(c in value for c in '\r\n\x00'):
            raise ValueError('Operations paths must be absolute')
        p=Path(value)
        if key in {'age_binary','ssh_binary'}:
            p=p.resolve(strict=True)
            config[key]=str(p)
        elif p.is_symlink():raise ValueError('Operations paths cannot be symlinks')
        if key in {'identity','age_identity'} and (not p.is_file() or p.stat().st_mode&0o077):
            raise ValueError('Operations keys must be private regular files')
        if key in {'age_binary','ssh_binary'} and (not p.is_file() or not os.access(p,os.X_OK)):
            raise ValueError('Install the required age and SSH tools')
    return config


def _ssh(config, command):
    return [config['ssh_binary'],'-F','/dev/null','-T','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15',
            '-o','ServerAliveCountMax=2','-i',config['identity'],config['host'],command]


def _remote(config, command, payload=None):
    result=subprocess.run(_ssh(config,command),input=json.dumps(payload) if payload is not None else None,
                          capture_output=True,text=True,timeout=60)
    if result.returncode or len(result.stdout)>1024*1024:raise ValueError('Remote operation unavailable')
    data=json.loads(result.stdout)
    if not isinstance(data,dict):raise ValueError('Remote operation returned invalid evidence')
    return data


def _health(config):
    data=_remote(config,'health')
    if type(data.get('ready')) is not bool:raise ValueError('Invalid remote health status')
    for key in ('warnings','failures'):
        values=data.get(key)
        if (not isinstance(values,list) or len(values)>100
                or any(not isinstance(value,str) or not re.fullmatch('[a-z_]{1,80}',value) for value in values)):
            raise ValueError('Invalid remote health diagnostics')
    return data


def _encrypted_snapshot(config, destination):
    """Never write a plaintext download into the retained backup directory."""
    processes=[]
    try:
        with destination.open('xb') as output,tempfile.TemporaryFile() as errors:
            os.fchmod(output.fileno(),0o600)
            remote=subprocess.Popen(_ssh(config,'backup'),stdout=subprocess.PIPE,stderr=errors)
            processes.append(remote)
            encrypt=subprocess.Popen([config['age_binary'],'-r',config['recipient']],
                                     stdin=remote.stdout,stdout=output,stderr=errors)
            processes.append(encrypt);remote.stdout.close()
            encrypted=encrypt.wait(timeout=2700);downloaded=remote.wait(timeout=30)
            if encrypted or downloaded:raise ValueError('Encrypted backup transfer failed')
            output.flush();os.fsync(output.fileno())
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill();process.wait(timeout=10)


def verify_encrypted(config, encrypted, temporary_parent):
    from .backup import restore_backup
    from .release_check import _fingerprint
    with tempfile.TemporaryDirectory(prefix='.verify-',dir=temporary_parent) as directory:
        directory=Path(directory)
        archive=directory/'history.zip'
        with archive.open('xb') as output:
            os.fchmod(output.fileno(),0o600)
            decrypted=subprocess.run([config['age_binary'],'-d','-i',config['age_identity'],str(encrypted)],
                                     stdout=output,stderr=subprocess.DEVNULL,timeout=900)
        if decrypted.returncode:raise ValueError('Encrypted backup authentication failed')
        root=directory/'restored'
        restore_backup(archive,root)
        db=sqlite3.connect((root/'ledger.sqlite3').as_uri()+'?mode=ro',uri=True)
        db.row_factory=sqlite3.Row
        try:
            # Restore already checked every checksum/reference and full SQLite
            # integrity, and disabled writes. Retain exact attempt fingerprints.
            return {'applications':db.execute('SELECT COUNT(*) FROM application_records').fetchone()[0],
                    'application_states':dict(db.execute('SELECT state,COUNT(*) FROM application_records GROUP BY state')),
                    'history':{table:_fingerprint(db,table) for table in ('applications','archived_applications')},
                    'restores_paused':not json.loads(db.execute('SELECT value FROM config WHERE id=1').fetchone()[0])['live_enabled']}
        finally:db.close()


def create_encrypted_backup(config):
    destination=private_dir(Path(config['destination']))
    state=private_dir(Path(config['state_directory']))
    require_space(destination,4*1024**3)
    name='backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]+'.zip.age'
    target=destination/name
    with tempfile.TemporaryDirectory(prefix='.incoming-',dir=destination) as directory:
        pending=Path(directory)/'history.zip.age'
        _encrypted_snapshot(config,pending)
        evidence=verify_encrypted(config,pending,state)
        with pending.open('rb') as source:checksum=hashlib.file_digest(source,'sha256').hexdigest()
        result={'verified_at':now(),'sha256':checksum,'bytes':pending.stat().st_size,
                'filename':name,'verified':True,**evidence}
        os.link(pending,target)
        descriptor=os.open(destination,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
        atomic_json(target.with_name(target.name+'.json'),result)
    return result


def _ack(config, backup):
    response=_remote(config,'backup-ack',{key:backup[key] for key in ('verified_at','sha256','bytes','applications')})
    if response.get('recorded') is not True:raise ValueError('Backup acknowledgement was not recorded')


def prune_verified(config, *, clock=None):
    """Only this agent's verified expired archives; always retain the newest two."""
    clock=time.time() if clock is None else clock
    destination=Path(config['destination'])
    verified=[]
    for path in destination.iterdir():
        if not ARCHIVE.fullmatch(path.name) or path.is_symlink() or not path.is_file():continue
        metadata=path.with_name(path.name+'.json')
        try:
            if metadata.is_symlink() or metadata.stat().st_size>16384:continue
            data=json.loads(metadata.read_text())
            if not isinstance(data,dict):continue
            age=_age(data.get('verified_at'),clock)
            if data.get('verified') is True and data.get('filename')==path.name and age is not None:
                verified.append((age,path,metadata))
        except (OSError,ValueError,TypeError):continue
    for age,path,metadata in sorted(verified,key=lambda row:row[0])[2:]:
        if age>14*86400:path.unlink();metadata.unlink()


def _notify():
    if sys.platform=='darwin':
        subprocess.run(['/usr/bin/osascript','-e',
            'display notification "Check the private operations status for the Pi." with title "please-hire-me needs attention"'],
            capture_output=True,timeout=10,check=True)


def run_agent(config_path, *, force=False):
    config=load_config(config_path)
    state=private_dir(Path(config['state_directory']))
    with worker_lock(state,'operations'):
        previous={}
        latest=state/'latest.json'
        if latest.is_file() and not latest.is_symlink():
            try:previous=json.loads(latest.read_text())
            except (ValueError,OSError):pass
        if not isinstance(previous,dict):previous={}
        if previous.get('last_backup') is not None and not isinstance(previous['last_backup'],dict):
            previous['last_backup']=None
        result={'checked_at':now(),'failures':[],'last_backup':previous.get('last_backup'),
                'last_acknowledged':previous.get('last_acknowledged')}
        try:
            health=_health(config)
            result['worker_health']=health
            backup=result['last_backup']
            age=_age(backup.get('verified_at'),time.time()) if isinstance(backup,dict) else None
            if force or age is None or age>=6*3600 or (not result['last_acknowledged'] and age>3000):
                backup=create_encrypted_backup(config)
                result['last_backup']=backup
                result['last_acknowledged']=None
            if result['last_backup'] and result['last_acknowledged']!=result['last_backup']['sha256']:
                _ack(config,result['last_backup'])
                result['last_acknowledged']=result['last_backup']['sha256']
                result['worker_health']=_health(config)
            if result['worker_health'].get('ready') is not True:result['failures'].append('worker_needs_attention')
            prune_verified(config)
        except Exception as error:
            result['failures'].append('operations_check_failed')
            result['error_type']=type(error).__name__
        signature=sorted(set(result['failures']+result.get('worker_health',{}).get('failures',[])
                             +result.get('worker_health',{}).get('warnings',[])))
        notification_age=_age(previous.get('notified_at'),time.time())
        if signature and (signature!=previous.get('notification_signature') or notification_age is None or notification_age>=6*3600):
            try:_notify();result['notified_at']=now()
            except (OSError,subprocess.SubprocessError):result['notification_unavailable']=True
        else:result['notified_at']=previous.get('notified_at')
        result['notification_signature']=signature
        result['ready']=not result['failures']
        atomic_json(latest,result)
        return result


def install_agent(config_path, repo):
    if sys.platform!='darwin':raise ValueError('Install this monitor on the Mac receiving the backups')
    config=load_config(config_path)
    state=private_dir(Path(config['state_directory']))
    directory=Path.home()/'Library/LaunchAgents';directory.mkdir(parents=True,exist_ok=True)
    path=directory/(LABEL+'.plist')
    if path.is_symlink():raise ValueError('Unsafe operations service path')
    payload={'Label':LABEL,'ProgramArguments':[sys.executable,'-m','hireme','backup-agent','run',str(Path(config_path).absolute())],
             'WorkingDirectory':str(Path(repo).absolute()),'RunAtLoad':True,'StartInterval':900,'ProcessType':'Background',
             'StandardOutPath':str(state/'operations.log'),'StandardErrorPath':str(state/'operations-error.log')}
    with tempfile.NamedTemporaryFile(dir=directory,prefix='.'+LABEL,delete=False) as output:
        temporary=Path(output.name)
        try:
            os.fchmod(output.fileno(),0o600);output.write(plistlib.dumps(payload));output.flush();os.fsync(output.fileno())
            os.replace(temporary,path)
        finally:temporary.unlink(missing_ok=True)
    subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{LABEL}'],capture_output=True)
    subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True,capture_output=True)
    return {'installed':True,'poll_seconds':900,'backup_hours':6,'retention_days':14}
