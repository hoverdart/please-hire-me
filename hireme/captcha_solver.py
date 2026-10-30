"""Owned-fixture-only solver prototype. Never imported by employer adapters."""
from dataclasses import dataclass
import math
import time
import queue
import threading
import json
from pathlib import Path
from urllib.parse import urlsplit

from .util import Blocked,atomic_json,private_dir,digest
from .store import worker_lock


@dataclass(frozen=True)
class FixtureChallenge:
    origin: str
    site_key: str
    nonce: str


class FixtureSolver:
    def __init__(self,provider,*,enabled=False,authorized_fixture=False,max_seconds=30,max_cost=0.01,journal_root=None):
        if (type(max_seconds) not in (int,float) or not math.isfinite(max_seconds) or not 0<max_seconds<=120
                or type(max_cost) not in (int,float) or not math.isfinite(max_cost) or not 0<max_cost<=0.10):
            raise ValueError('Use bounded fixture time and cost limits')
        self.provider=provider;self.enabled=enabled;self.authorized=authorized_fixture
        self.max_seconds=max_seconds;self.max_cost=max_cost;self.used=set();self.spent=0
        self.journal_root=Path(journal_root) if journal_root is not None else None

    def _reserve(self,nonce):
        if self.journal_root is None:raise Blocked('captcha_blocked','An owned-fixture experiment needs a private durable budget journal')
        root=private_dir(self.journal_root)
        if root.is_symlink():raise Blocked('captcha_blocked','Unsafe fixture journal')
        with worker_lock(root,'fixture-solver'):
            path=root/'solver-budget.json'
            if path.is_symlink():raise Blocked('captcha_blocked','Unsafe fixture journal')
            state=json.loads(path.read_text()) if path.exists() else {'ceiling':self.max_cost,'spent':0,'nonces':[]}
            key=digest(nonce)
            if state['ceiling']!=self.max_cost or key in state['nonces'] or state['spent']>=self.max_cost:
                raise Blocked('captcha_blocked','The fixture challenge was used or its durable budget is exhausted')
            allowance=self.max_cost-state['spent'];state['spent']=self.max_cost;state['nonces'].append(key)
            atomic_json(path,state)
            return allowance

    def _settle(self,allowance,cost):
        with worker_lock(self.journal_root,'fixture-solver'):
            path=self.journal_root/'solver-budget.json';state=json.loads(path.read_text())
            state['spent']-=allowance-cost;atomic_json(path,state);self.spent=state['spent']

    def solve(self,challenge,*,final_dispatched=False):
        if not self.enabled or not self.authorized or final_dispatched:
            raise Blocked('captcha_blocked','The fixture solver is disabled or the final request was already dispatched')
        parsed=urlsplit(challenge.origin)
        if (parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost'} or parsed.username
                or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment
                or not challenge.nonce or challenge.nonce in self.used or self.spent>=self.max_cost):
            raise Blocked('captcha_blocked','Only a fresh challenge on an owned loopback fixture is allowed')
        allowance=self._reserve(challenge.nonce)
        self.used.add(challenge.nonce)
        started=time.monotonic()
        # Provider contract receives only public fixture coordinates and limits;
        # no store, browser, cookies, credentials or application package.
        self.spent+=allowance
        completed=queue.Queue(maxsize=1)
        def invoke():
            try:completed.put((True,self.provider(challenge,timeout=self.max_seconds,max_cost=allowance)))
            except Exception:completed.put((False,None))
        threading.Thread(target=invoke,daemon=True).start()
        try:success,result=completed.get(timeout=self.max_seconds)
        except queue.Empty:raise Blocked('captcha_blocked','The fixture provider timed out; its reserved cost remains held') from None
        if not success:raise Blocked('captcha_blocked','The fixture provider is unavailable')
        elapsed=time.monotonic()-started
        if not isinstance(result,dict):raise Blocked('captcha_blocked','Invalid fixture solution')
        cost=result.get('cost');issued=result.get('issued_at');token=result.get('token')
        if (type(cost) not in (int,float) or not math.isfinite(cost) or not 0<=cost<=allowance):
            raise Blocked('captcha_blocked','Invalid fixture cost evidence')
        self._settle(allowance,cost)
        if (elapsed>self.max_seconds or type(issued) not in (int,float) or not math.isfinite(issued)
                or not 0<=time.time()-issued<120 or result.get('nonce')!=challenge.nonce
                or not isinstance(token,str) or not 0<len(token)<=8192):
            raise Blocked('captcha_blocked','The fixture solution is invalid, expired or late')
        return {'token':token,'metrics':{'seconds':elapsed,'cost':cost,'fixture_only':True}}
