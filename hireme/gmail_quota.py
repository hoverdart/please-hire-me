"""Shared Gmail quota pacing; retain no queries, message IDs or credentials.

Current Google quotas charge 60 units for full message reads and allow newer
projects 6,000 units/user/minute. Use 75 units/second without a burst allowance.
https://developers.google.com/workspace/gmail/api/reference/quota
"""
from __future__ import annotations

import math
import time
from urllib.parse import parse_qs,urlsplit

from .util import Blocked

UNITS_PER_SECOND=75


def units_for(method_id,uri):
    if method_id in {'gmail.users.messages.get','gmail.users.drafts.get'}:
        mode=parse_qs(urlsplit(uri).query).get('format',['full'])
        return 20 if mode in (['metadata'],['minimal']) else 60
    return {
        'gmail.users.getProfile':1,'gmail.users.history.list':2,
        'gmail.users.messages.list':5,'gmail.users.drafts.list':5,
        'gmail.users.labels.list':1,'gmail.users.labels.get':1,
        'gmail.users.messages.attachments.get':20,
        'gmail.users.messages.send':100,'gmail.users.drafts.send':100,
    }.get(method_id,100)


def reserve(store,units):
    if type(units) is not int or not 1<=units<=10000:raise ValueError('Invalid Gmail quota reservation')
    store.checkpoint()
    if store.db.in_transaction:raise Blocked('gmail_budget_in_transaction','Gmail transport requires committed local work')
    with store.transaction():
        current=time.time()
        row=store.db.execute('SELECT next_allowed FROM gmail_api_quota WHERE id=1').fetchone()
        previous=row['next_allowed'] if row else current
        if not math.isfinite(previous) or previous<0:raise Blocked('gmail_budget_invalid')
        scheduled=max(current,previous)
        store.db.execute('INSERT OR REPLACE INTO gmail_api_quota VALUES(1,?)',
            (scheduled+units/UNITS_PER_SECOND,))
    while True:
        store.checkpoint()
        remaining=scheduled-time.time()
        if remaining<=0:return
        time.sleep(min(.25,remaining))


def request_builder(store):
    # Gmail is optional. Import its library only when constructing a real client.
    from googleapiclient.http import HttpRequest
    class QuotaRequest(HttpRequest):
        def execute(self,http=None,num_retries=0):
            reserve(store,units_for(self.methodId,self.uri)*(1+num_retries))
            return super().execute(http=http,num_retries=num_retries)
    return QuotaRequest
