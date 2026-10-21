"""Durable one-job commissioning fence; process ownership never survives restart."""
from __future__ import annotations

import hashlib
import json
import re
import secrets

from .util import Blocked


START = 'workday_commission_started'
BOUND = 'workday_commission_run_bound'
FINISH = 'workday_commission_finished'
OWNER = '_workday_commission_owner'


def _detail(row):
    try:
        value = json.loads(row['detail'])
    except (ValueError,TypeError):
        return {}
    return value if isinstance(value,dict) else {}


def _pending(store):
    # Historical terminal events remain append-only. An invalid terminal event
    # cannot erase the unfinished fence it purports to close.
    closed = {row['subject'] for row in store.db.execute('SELECT subject,detail FROM events WHERE kind=?',(FINISH,))
              if (data:=_detail(row)).get('version')==1 and data.get('state') in {'finished','failed','recovered'}}
    return [row for row in store.db.execute('SELECT seq,subject,detail FROM events WHERE kind=? ORDER BY seq',(START,))
            if row['subject'] not in closed]


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _owner_for(store,row):
    owner = getattr(store,OWNER,None)
    if not isinstance(owner,dict):
        return None
    token = owner.get('token')
    data = _detail(row)
    if (not isinstance(token,str) or not re.fullmatch(r'[0-9a-f]{64}',token)
            or _hash(token)!=row['subject'] or data.get('version')!=1
            or owner.get('job_id')!=data.get('job_id')
            or type(data.get('generation')) is not int
            or owner.get('generation')!=data['generation']
            or store.control_generation()!=data['generation']):
        return None
    return owner


def _active(store,row):
    owner = _owner_for(store,row)
    if owner is None:
        return False
    bound = store.db.execute('SELECT detail FROM events WHERE kind=? AND subject=? ORDER BY seq DESC LIMIT 1',
                             (BOUND,row['subject'])).fetchone()
    run_id = owner.get('run_id')
    if not bound:
        return run_id is None and getattr(store,'active_run_id',None) is None
    data = _detail(bound)
    if (data.get('version')!=1 or data.get('job_id')!=owner['job_id']
            or not isinstance(run_id,str) or data.get('run_id')!=run_id
            or getattr(store,'active_run_id',None)!=run_id):
        return False
    run = store.db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()
    return bool(run and run['status']=='running')


def _close(store,rows,state,reason):
    # Pause and terminal markers commit together. A failed transaction leaves
    # the original durable fence available for the next recovery attempt.
    store.update_settings({'live_enabled':False})
    for row in rows:
        data = _detail(row)
        bound = store.db.execute('SELECT detail FROM events WHERE kind=? AND subject=? ORDER BY seq DESC LIMIT 1',
                                 (BOUND,row['subject'])).fetchone()
        run_id = _detail(bound).get('run_id') if bound else None
        store.event(FINISH,row['subject'],{'version':1,'job_id':data.get('job_id'),
                    'run_id':run_id,'state':state,'reason':reason})


def recover_commission(store):
    """Pause orphaned commissioning before an ordinary worker can write."""
    with store.transaction():
        rows = _pending(store)
        if not rows:
            return False
        if len(rows)==1 and _active(store,rows[0]):
            return False
        _close(store,rows,'recovered','commission_owner_lost')
    if hasattr(store,OWNER):
        delattr(store,OWNER)
    return True


def begin(store,job_id,*,browser_factory):
    """Called while the existing worker lock is held, before its selected cycle."""
    if (not isinstance(job_id,str) or not job_id or len(job_id)>128 or not callable(browser_factory)
            or getattr(store,OWNER,None) is not None):
        raise Blocked('workday_unapproved_operation')
    settings = store.settings()
    if not settings['live_enabled'] or not settings['remote_drafts']:
        raise Blocked('remote_draft_permission_required')
    owner = {'token':secrets.token_hex(32),'job_id':job_id,'generation':store.control_generation(),
             'run_id':None,'browser_factory':browser_factory}
    with store.transaction():
        if _pending(store):
            raise Blocked('workday_unapproved_operation','Recover the interrupted commissioning run first')
        store.check_job_decision(job_id)
        store.event(START,_hash(owner['token']),{'version':1,'job_id':job_id,'generation':owner['generation']})
    setattr(store,OWNER,owner)


def bind_run(store,run_id,*,job_ids,live,discover,limit,max_attempts,browser_factory):
    """Bind the actual worker run before its first browser or employer request."""
    blocked = False
    with store.transaction():
        rows = _pending(store)
        if not rows:
            return
        owner = _owner_for(store,rows[0]) if len(rows)==1 else None
        try:
            selected = set(job_ids or ())
        except TypeError:
            selected = set()
        running = (store.db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()
                   if isinstance(run_id,str) and 0<len(run_id)<=128 else None)
        valid = (owner is not None and owner.get('run_id') is None and selected=={owner['job_id']}
                 and live is True and discover is False and type(limit) is int and limit==1
                 and type(max_attempts) is int and max_attempts==1
                 and browser_factory is owner['browser_factory']
                 and getattr(store,'active_run_id',None)==run_id and running and running['status']=='running'
                 and store.settings()['live_enabled'] and store.settings()['remote_drafts'])
        if not valid:
            _close(store,rows,'recovered','commission_scope_changed')
            blocked = True
        else:
            store.event(BOUND,rows[0]['subject'],{'version':1,'job_id':owner['job_id'],'run_id':run_id})
            owner['run_id'] = run_id
    if blocked:
        if hasattr(store,OWNER):delattr(store,OWNER)
        raise Blocked('workday_unapproved_operation','The commissioning scope changed; the runner is paused')


def finish(store,*,state):
    """Always pause after the bounded command, including setup/lock failures."""
    if state not in {'finished','failed'}:
        raise ValueError('Choose a terminal commissioning state')
    owner = getattr(store,OWNER,None)
    try:
        with store.transaction():
            rows = _pending(store)
            owned = [row for row in rows if isinstance(owner,dict)
                     and isinstance(owner.get('token'),str) and _hash(owner['token'])==row['subject']]
            if owned:
                _close(store,owned,state,'commission_complete' if state=='finished' else 'commission_failed')
            elif store.settings()['live_enabled']:
                # Recovery may already have closed this token. Returning from
                # commissioning must still leave the worker paused.
                store.update_settings({'live_enabled':False})
    finally:
        if hasattr(store,OWNER):delattr(store,OWNER)
