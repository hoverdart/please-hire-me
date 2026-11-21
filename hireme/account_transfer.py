"""Explicit, encrypted employer-credential transfer between paused instances."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from .accounts import AccountVault, account_key, MIN_PASSWORD_LENGTH
from .store import worker_lock
from .util import atomic_json

MAX_ARCHIVE = 2 * 1024 * 1024
MAX_ACCOUNTS = 1000


def _cipher(passphrase, salt):
    if not isinstance(passphrase,str) or not 12<=len(passphrase)<=1024:
        raise ValueError('Use a transfer passphrase of 12–1024 characters')
    key=Argon2id(salt=salt,length=32,iterations=3,lanes=4,memory_cost=65536).derive(passphrase.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _owner(store):
    if store.settings()['live_enabled']:
        raise ValueError('Pause this instance before transferring employer credentials')
    email=store.facts().get('email',{}).get('value')
    if not email:
        raise ValueError('Confirm the applicant email first')
    return email


def export_accounts(store, destination: Path, passphrase):
    if destination.exists() or destination.is_symlink():
        raise ValueError('Choose a new credential-transfer filename')
    with worker_lock(store.root):
        email=_owner(store)
        rows=list(store.db.execute('SELECT * FROM employer_accounts ORDER BY id'))
        if len(rows)>MAX_ACCOUNTS:
            raise ValueError('Too many accounts for one credential transfer')
        vault=AccountVault(store)
        accounts=[]
        for row in rows:
            credential=vault.credentials(row['origin'],row['company'])
            accounts.append({'id':row['id'],'origin':row['origin'],'company':row['company'],
                             'email':email,'password':credential['password']})
        salt=os.urandom(16)
        payload=json.dumps({'version':1,'email':email,'accounts':accounts}).encode()
        token=_cipher(passphrase,salt).encrypt(payload).decode('ascii')
        envelope=json.dumps({'version':1,'salt':base64.urlsafe_b64encode(salt).decode('ascii'),'token':token}).encode()
        if len(envelope)>MAX_ARCHIVE:
            raise ValueError('Credential transfer is too large')
        destination.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'wb') as file:
                file.write(envelope);file.flush();os.fsync(file.fileno())
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        store.event('account_credentials_exported','accounts',{'count':len(accounts)})
    return {'accounts':len(accounts),'encrypted':True}


def import_accounts(store, source: Path, passphrase):
    if source.is_symlink() or not source.is_file() or source.stat().st_size>MAX_ARCHIVE:
        raise ValueError('Choose a regular encrypted credential transfer up to 2 MiB')
    try:
        envelope=json.loads(source.read_bytes())
        if not isinstance(envelope,dict) or envelope.get('version')!=1:
            raise ValueError()
        salt=base64.b64decode(envelope['salt'],altchars=b'-_',validate=True)
        if len(salt)!=16 or not isinstance(envelope['token'],str):
            raise ValueError()
        data=json.loads(_cipher(passphrase,salt).decrypt(envelope['token'].encode('ascii')))
    except (ValueError,KeyError,TypeError,UnicodeError,InvalidToken):
        raise ValueError('Invalid transfer or incorrect passphrase') from None
    with worker_lock(store.root):
        email=_owner(store)
        if not isinstance(data,dict) or data.get('version')!=1 or data.get('email')!=email:
            raise ValueError('Credential transfer belongs to a different applicant')
        accounts=data.get('accounts')
        if not isinstance(accounts,list) or len(accounts)>MAX_ACCOUNTS:
            raise ValueError('Invalid credential transfer')
        vault=AccountVault(store);validated=[];seen=set()
        # Validate the entire archive before writing any credential file.
        for entry in accounts:
            if (not isinstance(entry,dict) or not isinstance(entry.get('password'),str)
                    or not MIN_PASSWORD_LENGTH<=len(entry['password'])<=128 or entry.get('email')!=email):
                raise ValueError('Invalid account credential')
            try:key=account_key(entry['origin'],entry['company'])
            except (ValueError,KeyError,TypeError):raise ValueError('Invalid account scope') from None
            row=store.db.execute('SELECT * FROM employer_accounts WHERE id=?',(key,)).fetchone()
            if (key in seen or entry.get('id')!=key or not row
                    or row['origin']!=entry['origin'] or row['company']!=entry['company']):
                raise ValueError('Restore matching account history before importing credentials')
            seen.add(key)
            path=vault.directory/(key+'.json')
            credential={'email':email,'password':entry['password']}
            if path.is_symlink():raise ValueError('Unsafe credential path')
            if path.exists() and vault.credentials(row['origin'],row['company'])!=credential:
                raise ValueError('Existing account credentials differ; nothing was imported')
            validated.append((path,credential))
        for path,credential in validated:
            if not path.exists():atomic_json(path,credential)
        store.event('account_credentials_imported','accounts',{'count':len(validated)})
    # Account confirmation/uncertainty is unchanged. An interrupted import can
    # be repeated: identical files are retained, conflicting files rejected.
    return {'accounts':len(validated),'states_preserved':True,'paused':True}
