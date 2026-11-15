"""Explicit owner policy for new Workday accounts; secrets never enter the ledger."""
from __future__ import annotations

import json
import os
import secrets

from .accounts import AccountVault,MIN_PASSWORD_LENGTH
from .store import worker_lock
from .util import Blocked,atomic_json,digest,private_dir
from .workday_site import site_for_url


def path(store):
    return private_dir(store.root/'integrations')/'workday-registration.json'


def _read(store):
    source=path(store)
    try:
        info=source.stat()
        if (source.is_symlink() or not source.is_file() or info.st_size>4096 or info.st_mode&0o077
                or hasattr(os,'getuid') and info.st_uid!=os.getuid()):return None
        data=json.loads(source.read_text())
        fact=store.facts().get('email',{})
        if (not isinstance(data,dict) or data.get('version')!=1 or data.get('enabled') is not True
                or not fact.get('confirmed') or data.get('owner')!=digest(fact['value'].casefold())
                or data.get('accept_account_terms') is not True
                or not isinstance(data.get('password'),str) or not MIN_PASSWORD_LENGTH<=len(data['password'])<=128
                or any(c in data['password'] for c in ('\x00','\r','\n'))):return None
        return data
    except (OSError,ValueError,TypeError):return None


def configure(store,password,confirmation,*,accept_account_terms=False):
    if (not isinstance(password,str) or not MIN_PASSWORD_LENGTH<=len(password)<=128
            or any(c in password for c in ('\x00','\r','\n'))):
        raise ValueError('Use a password of 12–128 characters without line breaks')
    try:password.encode('utf-8')
    except UnicodeError:raise ValueError('Use readable characters in the password') from None
    if not isinstance(confirmation,str) or not secrets.compare_digest(password.encode(),confirmation.encode()):
        raise ValueError('The passwords must match')
    if accept_account_terms is not True:raise ValueError('Authorize the account creation and privacy terms first')
    with worker_lock(store.root):
        if store.settings()['live_enabled']:raise ValueError('Pause submissions before configuring Workday registration')
        fact=store.facts().get('email',{})
        if not fact.get('confirmed'):raise ValueError('Confirm the main applicant email first')
        destination=path(store)
        if destination.is_symlink():raise ValueError('Unsafe registration policy path')
        atomic_json(destination,{'version':1,'enabled':True,'owner':digest(fact['value'].casefold()),
                                'password':password,'accept_account_terms':True})
        store.event('workday_registration_configured','accounts',{'enabled':True,'existing_credentials_unchanged':True})
    return status(store)


def status(store):
    return {'enabled':_read(store) is not None,'uses_confirmed_main_email':True,
            'existing_credentials_unchanged':True,'credentials_in_application_history':False}


def disable(store):
    with worker_lock(store.root):
        source=path(store)
        if source.is_symlink():raise ValueError('Unsafe registration policy path')
        source.unlink(missing_ok=True)
        store.event('workday_registration_disabled','accounts',{})
    return status(store)


def credentials(store,job,*,create=False):
    site=site_for_url(job['url'])
    if job.get('host')!=site.host:raise Blocked('unapproved_destination')
    origin='https://'+site.host
    vault=AccountVault(store)
    try:return vault.credentials(origin,site.tenant)
    except Blocked as error:
        if error.reason!='account_credentials_unavailable' or not create:raise
    policy=_read(store)
    if not policy:raise Blocked('workday_registration_not_configured','Configure the private Workday account password first')
    return vault.credentials(origin,site.tenant,create=True,password=policy['password'])
