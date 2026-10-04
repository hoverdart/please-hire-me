import pytest

from hireme.document_controls import withdraw_transcript
from hireme.store import worker_lock
from hireme.util import Blocked, digest


@pytest.mark.parametrize('state', ['confirmed', 'unknown', 'awaiting_verification'])
def test_withdrawal_invalidates_only_transcript_drafts_and_retains_attempted_evidence(store, job, package, state):
    resume=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    store.db.execute('INSERT INTO documents VALUES(?,?,?)',('transcript',resume['hash'],resume['filename']))
    transcript={**package['documents'][0],'kind':'transcript'}
    with_transcript={**package,'documents':[*package['documents'],transcript]}
    aid=store.prepare(job,with_transcript);store.begin_submit(aid);store.finish(aid,state,'Synthetic evidence')
    attempted=dict(store.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone())
    ids=[]
    for index,include in enumerate([True,False]):
        candidate={**job,'id':digest(index),'url':job['url']+str(index),'company':'Other '+str(index)}
        store.upsert_job(candidate)
        selected=with_transcript if include else package
        draft=store.prepare(candidate,{**selected,'job_id':candidate['id'],'url':candidate['url']})
        store.db.execute("UPDATE jobs SET status='prepared' WHERE id=?",(candidate['id'],))
        ids.append((candidate['id'],draft))
    settings=store.settings()
    assert withdraw_transcript(store)=={'removed':True,'drafts_removed':1}
    assert not store.db.execute("SELECT 1 FROM documents WHERE kind='transcript'").fetchone()
    assert (store.root/'documents'/resume['filename']).is_file()
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone())==attempted
    assert not store.db.execute('SELECT 1 FROM applications WHERE id=?',(ids[0][1],)).fetchone()
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(ids[1][1],)).fetchone()[0]=='prepared'
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(ids[0][0],)).fetchone()[0]=='discovered'
    assert store.settings()==settings
    assert withdraw_transcript(store)=={'removed':False,'drafts_removed':0}


def test_transcript_withdrawal_refuses_an_active_worker_without_mutation(store):
    resume=store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
    store.db.execute('INSERT INTO documents VALUES(?,?,?)',('transcript',resume['hash'],resume['filename']))
    with worker_lock(store.root):
        with pytest.raises(Blocked):withdraw_transcript(store)
    assert store.db.execute("SELECT 1 FROM documents WHERE kind='transcript'").fetchone()


def test_transcript_withdrawal_handles_a_small_sql_parameter_limit(store,job,package):
    import sqlite3
    resume=package['documents'][0]
    store.db.execute('INSERT INTO documents VALUES(?,?,?)',('transcript',resume['hash'],resume['filename']))
    for index in range(20):
        candidate={**job,'id':digest(index),'url':job['url']+str(index),'company':'Other '+str(index)}
        store.upsert_job(candidate)
        store.prepare(candidate,{**package,'job_id':candidate['id'],'url':candidate['url'],
                                 'documents':[*package['documents'],{**resume,'kind':'transcript'}]})
        store.db.execute("UPDATE jobs SET status='prepared' WHERE id=?",(candidate['id'],))
    previous=store.db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,10)
    try:assert withdraw_transcript(store)=={'removed':True,'drafts_removed':20}
    finally:store.db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER,previous)
    assert not store.db.execute('SELECT * FROM applications').fetchone()
    assert not store.db.execute("SELECT 1 FROM jobs WHERE status='prepared'").fetchone()
