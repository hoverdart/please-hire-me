"""Durable operation evidence, without retaining native bodies or credentials.

The random MAC key exists only in this live process. Persisted commitments cannot
be used to guess low entropy answers. Acknowledgements never reopen a guest draft.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid

from .util import Blocked, now


class DraftJournal:
    def __init__(self, store, job):
        self.store, self.job = store, job
        self.session = uuid.uuid4().hex
        self.secret = secrets.token_bytes(32)
        self.keys = {}

    def commitment(self, value):
        return hmac.new(self.secret, json.dumps(value, sort_keys=True, separators=(',', ':')).encode(), hashlib.sha256).hexdigest()

    def intent(self, keys, operation, target, package):
        keys = set(keys)
        if not keys or keys.intersection(self.keys):
            raise Blocked('ashby_draft_uncertain', 'Ambiguous draft request correlation')
        if self.store.db.in_transaction:
            raise Blocked('ashby_draft_uncertain', 'Cannot commit an operation before transport')
        record = uuid.uuid4().hex
        try:correlation=sorted(json.dumps(key,sort_keys=True) for key in keys)
        except (ValueError,TypeError):raise Blocked('ashby_draft_uncertain','Unsupported request correlation identity') from None
        revisions={'commitment':self.commitment(package),
            'facts':sorted((key,value.get('revision')) for key,value in self.store.facts().items()),
            'answers':[{key:answer.get('provenance',{}).get(key) for key in
                ('fact_key','revision','template_id','history_entry_id','history_revision')}
                for answer in package.get('answers',[])],
            'documents':[{key:document.get(key) for key in ('kind','hash')} for document in package.get('documents',[])]}
        with self.store.transaction():
            self.store.db.execute('INSERT INTO draft_operations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (record,self.job['id'],self.session,operation,json.dumps(target),
                 self.commitment(correlation),json.dumps(revisions,sort_keys=True),'intent',now(),now(),''))
        for key in keys:
            self.keys[key] = record
        return record

    def acknowledge(self, key):
        record = self.keys.get(key)
        if not record or self.store.db.in_transaction:
            raise Blocked('ashby_draft_uncertain', 'The operation acknowledgement cannot be committed')
        with self.store.transaction():
            updated = self.store.db.execute("UPDATE draft_operations SET state='acknowledged',updated=?,acknowledgement='native_validated' WHERE id=? AND session=? AND state='intent'",
                (now(),record,self.session)).rowcount
            if updated != 1:
                raise Blocked('ashby_draft_uncertain', 'Duplicate or foreign operation acknowledgement')
        return record
