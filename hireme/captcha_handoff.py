"""Opt-in, authenticated control of only the current isolated CAPTCHA frame."""
import base64
import hashlib
import json
import math
import secrets
import time
import uuid
import tempfile
import sqlite3

from .util import Blocked, private_dir

FRAMES='iframe[src*="bframe"],iframe[src*="hcaptcha"],iframe[src*="challenges.cloudflare.com"]'
LEASE_SECONDS=20


def _image(store, identity):
    return private_dir(store.root/'tmp')/('captcha-'+identity+'.png')


def _frame_state(value):
    state=json.loads(value) if value else {}
    return state if isinstance(state,dict) else {'sequence':int(state)}


def request(store, data):
    """Called only behind the dashboard's loopback, origin and token checks."""
    from .human_verification import current_wait
    wait=current_wait(store)
    if not wait or wait['phase']!='challenge':raise ValueError('No live challenge is available')
    action=data.get('action')
    with store.transaction():
        row=store.db.execute("SELECT * FROM captcha_handoffs WHERE application_id=? AND state='waiting'",(wait['application_id'],)).fetchone()
        clock=time.time()
        if not row or clock>=row['expires']:raise ValueError('The handoff expired')
        if action=='claim':
            if row['controller_hash']:raise ValueError('This challenge already has a controller')
            token=secrets.token_urlsafe(32)
            store.db.execute('UPDATE captcha_handoffs SET controller_hash=?,heartbeat=? WHERE id=?',
                (hashlib.sha256(token.encode()).hexdigest(),clock,row['id']))
            return {'controller':token,'expires_at':row['expires'],'heartbeat_seconds':5}
        token=data.get('controller','')
        if (not isinstance(token,str) or len(token)>100 or not row['controller_hash']
                or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(),row['controller_hash'])
                or clock-row['heartbeat']>LEASE_SECONDS):raise ValueError('The controller disconnected or is unauthorized')
        if action=='disconnect':
            store.db.execute("UPDATE captcha_handoffs SET state='disconnected' WHERE id=?",(row['id'],))
            return {'disconnected':True}
        if action not in {'heartbeat','frame','click'}:raise ValueError('Unsupported challenge action')
        state=_frame_state(row['result'])
        if action=='click':
            if row['command']:raise ValueError('Wait for the previous challenge interaction')
            xy=[data.get('x'),data.get('y')]
            if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<v<1 for v in xy):raise ValueError('Use coordinates within the challenge image')
            sequence=data.get('sequence');previous=state.get('sequence',0)
            if type(sequence) is not int or sequence!=previous+1:raise ValueError('Replayed challenge interaction')
            revision=data.get('frame_revision')
            if not isinstance(revision,str):raise ValueError('Provide the displayed challenge image revision')
            if revision!=state.get('revision'):
                state['stale_click']=True
                store.db.execute('UPDATE captcha_handoffs SET heartbeat=?,result=? WHERE id=?',(clock,json.dumps(state),row['id']))
                return {'active':True,'accepted':False,'stale_click':True}
            state.update(sequence=sequence,stale_click=False)
            store.db.execute('UPDATE captcha_handoffs SET command=?,result=? WHERE id=?',
                (json.dumps({'xy':xy,'revision':revision}),json.dumps(state),row['id']))
        store.db.execute('UPDATE captcha_handoffs SET heartbeat=? WHERE id=?',(clock,row['id']))
        result={'active':True,'expires_at':row['expires'],'next_sequence':state.get('sequence',0)+1,'interaction_pending':bool(row['command']),
            'frame_revision':state.get('revision'),'stale_click':bool(state.get('stale_click'))}
        if action=='frame':
            path=_image(store,row['id'])
            ready=bool(state.get('revision') and not path.is_symlink() and path.is_file() and path.stat().st_size<=2*1024*1024)
            result['ready']=ready
            if ready:result['png']=base64.b64encode(path.read_bytes()).decode()
        return result


class Handoff:
    def __init__(self,browser,expires):
        self.browser=browser;self.store=browser.store;self.identity=uuid.uuid4().hex;self.last_frame=0
        try:
            self.store.db.execute('INSERT INTO captcha_handoffs VALUES(?,?,?,?,?,?,?,?,?)',
                (self.identity,browser.aid,getattr(self.store,'active_run_id','') or '',expires,'',0,'waiting','',''))
        except sqlite3.IntegrityError:
            raise Blocked('captcha_blocked','A challenge handoff already ended for this attempt; it cannot be reopened') from None
        from .backup_agent import _notify
        try:_notify()
        except Exception:pass

    def pump(self,grant):
        self.browser.captcha_command_active=True
        try:self._pump(grant)
        finally:
            self.browser.captcha_command_active=False
            pending=getattr(self.browser,'captcha_deferred_route',None)
            self.browser.captcha_deferred_route=None
            if pending:
                route,ashby,overrides=pending
                self.browser._continue_route(route,_ashby_draft_write=ashby,**overrides)

    def _pump(self,grant):
        row=self.store.db.execute('SELECT * FROM captcha_handoffs WHERE id=?',(self.identity,)).fetchone()
        dispatched=lambda:bool(getattr(grant,'used',grant))
        if dispatched():
            self.close();return
        if (not row or row['state']!='waiting' or time.time()>=row['expires']
                or row['controller_hash'] and time.time()-row['heartbeat']>LEASE_SECONDS):
            raise Blocked('captcha_blocked','The authenticated challenge handoff expired or disconnected; this attempt remains protected')
        frames=self.browser.page.locator(FRAMES)
        visible=[]
        for index in range(frames.count()):
            frame=frames.nth(index)
            if frame.is_visible():visible.append(frame)
        challenges=[frame for frame in visible if 'frame=challenge' in (frame.get_attribute('src') or '')]
        if len(challenges)==1:visible=challenges
        if len(visible)!=1:
            if row['command']:raise Blocked('captcha_blocked','The challenge changed before the interaction')
            return
        frame=visible[0]
        from playwright.sync_api import Error as BrowserError
        if row['command']:
            # Clear first. A crash after the click can never replay it.
            self.store.db.execute("UPDATE captcha_handoffs SET command='' WHERE id=?",(self.identity,))
            command=json.loads(row['command']);x,y=command['xy'];box=frame.bounding_box()
            if not box:raise Blocked('captcha_blocked')
            if dispatched():self.close();return
            try:current=frame.screenshot(type='png',timeout=1000)
            except BrowserError:
                with self.store.transaction():
                    latest=_frame_state(self.store.db.execute('SELECT result FROM captcha_handoffs WHERE id=?',(self.identity,)).fetchone()[0])
                    latest['stale_click']=True
                    self.store.db.execute('UPDATE captcha_handoffs SET result=? WHERE id=?',(json.dumps(latest),self.identity))
                self.last_frame=0
                return
            box=frame.bounding_box()
            state=_frame_state(row['result'])
            unchanged=(hashlib.sha256(current).hexdigest()==command['revision'] and box==state.get('box'))
            if dispatched():self.close();return
            if unchanged:self.browser.page.mouse.click(box['x']+x*box['width'],box['y']+y*box['height'])
            else:
                with self.store.transaction():
                    latest=_frame_state(self.store.db.execute('SELECT result FROM captcha_handoffs WHERE id=?',(self.identity,)).fetchone()[0])
                    latest['stale_click']=True
                    self.store.db.execute('UPDATE captcha_handoffs SET result=? WHERE id=?',(json.dumps(latest),self.identity))
                self.last_frame=0
        if dispatched():self.close();return
        if time.monotonic()-self.last_frame>=1:
            path=_image(self.store,self.identity)
            if path.is_symlink():raise Blocked('captcha_blocked')
            try:data=frame.screenshot(type='png',timeout=1000)
            except BrowserError:return
            box=frame.bounding_box()
            if not box or dispatched():return
            import os
            with self.store.transaction():
                with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as output:
                    temporary=output.name
                    try:
                        os.fchmod(output.fileno(),0o600);output.write(data);output.flush()
                        os.replace(temporary,path)
                    finally:
                        if os.path.exists(temporary):os.unlink(temporary)
                state=_frame_state(self.store.db.execute('SELECT result FROM captcha_handoffs WHERE id=?',(self.identity,)).fetchone()[0])
                state.update(revision=hashlib.sha256(data).hexdigest(),box=box)
                self.store.db.execute('UPDATE captcha_handoffs SET result=? WHERE id=?',(json.dumps(state),self.identity))
            self.last_frame=time.monotonic()

    def close(self):
        self.store.db.execute("UPDATE captcha_handoffs SET state='closed',command='' WHERE id=?",(self.identity,))
        _image(self.store,self.identity).unlink(missing_ok=True)
