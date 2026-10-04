"""Withdraw an optional upload from future work while retaining private evidence."""
from __future__ import annotations

import json

from .store import worker_lock


def invalidate_document_drafts(store,kind):
    """Caller owns a transaction; attempted evidence and unrelated drafts stay intact."""
    drafts=[]
    for app in store.db.execute("SELECT id,job_id,package FROM applications WHERE state='prepared'"):
        try:documents=json.loads(app['package']).get('documents',[])
        except (ValueError,TypeError,AttributeError):continue
        if isinstance(documents,list) and any(isinstance(doc,dict) and doc.get('kind')==kind for doc in documents):
            drafts.append((app['id'],app['job_id']))
    return store.discard_prepared(jid for _,jid in drafts)


def withdraw_transcript(store):
    with worker_lock(store.root):
        with store.transaction():
            document=store.db.execute("SELECT * FROM documents WHERE kind='transcript'").fetchone()
            if not document:return {'removed':False,'drafts_removed':0}
            count=invalidate_document_drafts(store,'transcript')
            store.db.execute("DELETE FROM documents WHERE kind='transcript'")
            store.event('document_withdrawn','transcript',{'hash':document['hash'],'drafts_removed':count})
    return {'removed':True,'drafts_removed':count}
