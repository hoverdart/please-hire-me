"""Consistent portable applicant history; restores start paused in a new directory."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from .store import Store, worker_lock

MAX_MEMBER = 256*1024*1024
MAX_EXPANDED = 2*1024*1024*1024
MAX_LEDGER = MAX_EXPANDED
COPY_CHUNK = 64*1024


def _read_member(archive, name):
    try:
        return archive.read(name)
    except (RuntimeError, NotImplementedError, OSError, zlib.error):
        raise ValueError('Cannot read this backup ZIP. Choose the original history backup downloaded from Application desk.') from None


def _manifest(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Backup manifest contains duplicate fields')
            result[key] = value
        return result
    try:
        manifest = json.loads(data, object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError('Backup manifest cannot be read') from None
    if not isinstance(manifest, dict) or type(manifest.get('version')) is not int or manifest['version'] != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Unsupported backup manifest. Choose a history backup created by Application desk.')
    for name,info in manifest['files'].items():
        maximum=MAX_LEDGER if name=='ledger.sqlite3' else MAX_MEMBER
        if not isinstance(info, dict) or type(info.get('bytes')) is not int or not 0 <= info['bytes'] <= maximum or not isinstance(info.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', info['sha256']):
            raise ValueError('Backup manifest has invalid file checksums or sizes')
    return manifest


def _validate_references(store):
    validated=set()
    def document(h,name,directory):
        suffix=r'\.pdf' if directory=='documents' else r'\.(?:pdf|docx|pptx|txt|md)'
        if not isinstance(h,str) or not isinstance(name,str) or not re.fullmatch(r'[a-f0-9]{64}',h) or not re.fullmatch(re.escape(h)+suffix,name):raise ValueError('Invalid document reference in ledger')
        reference=(directory,h,name)
        if reference in validated:return
        parent=store.root/directory
        if parent.is_symlink():raise ValueError('Unsafe private storage directory')
        path=parent/name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=h:raise ValueError('Referenced private document is missing or changed')
        validated.add(reference)
    for table,directory in (('documents','documents'),('generated_documents','documents'),('materials','materials')):
        for row in store.db.execute('SELECT hash,filename FROM '+table):
            document(*row,directory)
    for app in store.db.execute('SELECT package,screenshot FROM applications'):
        # Preserve damaged/legacy package text for recovery. Readable references
        # still must resolve, even after the current upload was replaced/withdrawn.
        try:package=json.loads(app['package'])
        except (ValueError,TypeError):package={}
        references=package.get('documents',[]) if isinstance(package,dict) else []
        if isinstance(references,list):
            for reference in references:
                if isinstance(reference,dict):document(reference.get('hash'),reference.get('filename'),'documents')
        name=app['screenshot']
        if name:
            if not isinstance(name,str) or not re.fullmatch(r'[a-f0-9]{64}-(?:before|after|verified)\.jpg',name):raise ValueError('Invalid screenshot reference in ledger')
            parent=store.root/'screenshots'
            if parent.is_symlink():raise ValueError('Unsafe private storage directory')
            path=parent/name
            if path.is_symlink() or not path.is_file():raise ValueError('Referenced application screenshot is missing')


def create_backup(store, destination: Path):
    if destination.exists() or destination.is_symlink():
        raise ValueError('Choose a new backup filename')
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {}
    with worker_lock(store.root):
        _validate_references(store)
        with tempfile.TemporaryDirectory() as temporary:
            ledger = Path(temporary)/'ledger.sqlite3'
            db = sqlite3.connect(ledger)
            store.db.backup(db);db.close()
            snapshot = Store(Path(temporary))
            # This is a copy. The source worker's configuration is untouched.
            snapshot.update_settings({'live_enabled':False});snapshot.recover();snapshot.close()
            fd, tempname = tempfile.mkstemp(dir=destination.parent, prefix='.backup-')
            try:
                os.close(fd)
                with zipfile.ZipFile(tempname, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    def add(name, data):
                        manifest[name] = {'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                        archive.writestr(name,data)
                    add('ledger.sqlite3',ledger.read_bytes())
                    for directory, pattern in (
                        ('documents',r'[a-f0-9]{64}\.pdf'),
                        ('materials',r'[a-f0-9]{64}\.(?:pdf|docx|pptx|txt|md)'),
                        ('screenshots',r'[a-f0-9]{64}-(?:before|after|verified)\.jpg')):
                        parent=store.root/directory
                        if parent.is_symlink():raise ValueError('Unsafe private storage directory')
                        if not parent.exists():continue
                        for path in sorted(parent.iterdir()):
                            if path.is_symlink():raise ValueError('Unsafe private storage file')
                            if path.is_file() and re.fullmatch(pattern,path.name):
                                data=path.read_bytes()
                                if directory!='screenshots' and hashlib.sha256(data).hexdigest()!=path.stem:
                                    raise ValueError('Stored document hash does not match its filename')
                                add(directory+'/'+path.name,data)
                    archive.writestr('manifest.json',json.dumps({'version':1,'files':manifest}))
                # Link is exclusive: never overwrite an archive appearing concurrently.
                os.link(tempname,destination)
            finally:
                Path(tempname).unlink(missing_ok=True)
    return {'path':str(destination),'files':len(manifest),'credentials_included':False,'restores_paused':True}


def restore_backup(archive_path: Path, destination: Path):
    if destination.exists() or destination.is_symlink():
        raise ValueError('Restore into a new directory; existing applicant history is never overwritten')
    if archive_path.is_symlink() or not archive_path.is_file() or archive_path.stat().st_size>1024*1024*1024:
        raise ValueError('Choose a regular backup archive up to 1 GiB')
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.restore-',dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries=archive.infolist()
            names=[e.filename for e in entries]
            if len(entries)>50000 or len(names)!=len(set(names)) or sum(e.file_size for e in entries)>MAX_EXPANDED:
                raise ValueError('Invalid or excessive backup archive')
            if any(entry.flag_bits & 1 for entry in entries):
                raise ValueError('Encrypted ZIP archives are not supported. Choose the original history backup; encrypted employer passwords use the separate transfer.')
            if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size>8*1024*1024:
                raise ValueError('Backup manifest is missing or too large')
            manifest=_manifest(_read_member(archive,'manifest.json'))
            if set(manifest['files'])|{'manifest.json'}!=set(names):
                raise ValueError('Backup does not match its manifest')
            for name,info in manifest['files'].items():
                p=PurePosixPath(name)
                approved = name=='ledger.sqlite3' or bool(re.fullmatch(
                    r'(?:documents/[a-f0-9]{64}\.pdf|materials/[a-f0-9]{64}\.(?:pdf|docx|pptx|txt|md)|screenshots/[a-f0-9]{64}-(?:before|after|verified)\.jpg)',name))
                if p.is_absolute() or '..' in p.parts or not approved:
                    raise ValueError('Unapproved backup member')
                if archive.getinfo(name).file_size>(MAX_LEDGER if name=='ledger.sqlite3' else MAX_MEMBER):
                    raise ValueError('Backup member is too large')
                if archive.getinfo(name).file_size != info['bytes']:
                    raise ValueError('Backup checksum mismatch')
                target=staging/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                checksum=hashlib.sha256();count=0
                try:
                    with archive.open(name) as source, target.open('xb') as output:
                        os.chmod(target,0o600)
                        while chunk:=source.read(COPY_CHUNK):
                            count+=len(chunk)
                            if count>info['bytes']:raise ValueError('Backup checksum mismatch')
                            checksum.update(chunk);output.write(chunk)
                except (RuntimeError,NotImplementedError,OSError,zlib.error,zipfile.BadZipFile):
                    raise ValueError('Cannot read this backup ZIP. Choose the original history backup downloaded from Application desk.') from None
                if count!=info['bytes'] or checksum.hexdigest()!=info['sha256']:
                    raise ValueError('Backup checksum mismatch')
        if not (staging/'ledger.sqlite3').is_file():raise ValueError('Backup has no ledger')
        # Reject corrupt SQLite before exposing a restored applicant directory.
        db=sqlite3.connect(staging/'ledger.sqlite3')
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid backup ledger')
        finally:db.close()
        restored=Store(staging)
        try:
            _validate_references(restored)
            restored.update_settings({'live_enabled':False});restored.recover();restored.export_config()
        finally:restored.close()
        if destination.exists():raise ValueError('Restore destination appeared concurrently')
        os.rename(staging,destination)
        return {'path':str(destination),'paused':True,'gmail_reconnect_required':True}
    except (zipfile.BadZipFile,sqlite3.DatabaseError,KeyError,TypeError):
        raise ValueError('Cannot restore this backup archive') from None
    finally:
        if staging.exists():shutil.rmtree(staging)
