import pytest

from hireme.application_evidence import snapshot
from hireme.draft_journal import DraftJournal
from hireme.rollout import commission
from hireme.util import application_identity,digest,now


@pytest.mark.parametrize('mutation',['none','wrong_job','wrong_operation','unknown_save','no_ack','wrong_request','wrong_owner','wrong_posting','duplicate_proof','no_intent'])
def test_workday_rollout_requires_bound_owner_receipt_and_acknowledged_final_save(store,job,package,mutation):
    wd={**job,'id':digest('workday-evidence'),'host':'synthetic.wd12.myworkdayjobs.com',
        'url':'https://synthetic.wd12.myworkdayjobs.com/External/job/California/Intern_JR123'}
    store.upsert_job(wd)
    aid=store.prepare(wd,{**package,'job_id':wd['id'],'url':wd['url']})
    if mutation!='no_intent':store.begin_submit(aid)
    journal=DraftJournal(store,wd)
    operation=journal.intent(['final'],'ui:submit',{'host':wd['host']},package)
    journal.acknowledge('final')
    row=store.db.execute('SELECT * FROM draft_operations WHERE id=?',(operation,)).fetchone()
    proof={'platform':'workday','evidence_version':1,'posting_identity':list(application_identity(wd['url'])),
        'operation_id':operation,'request_hash':row['request_commitment'],'response_sha256':digest('reply'),
        'receipt_sha256':digest('fresh UI receipt'),'owner_verified':True,'fresh':True,
        'evidence_hash':digest('exact config readback'),'remote_application_hash':digest('remote')}
    if mutation=='wrong_job':store.db.execute('UPDATE draft_operations SET job_id=? WHERE id=?',('another',operation))
    if mutation=='wrong_operation':store.db.execute('UPDATE draft_operations SET operation=? WHERE id=?',('ui:next',operation))
    if mutation=='unknown_save':store.db.execute('UPDATE draft_operations SET state=? WHERE id=?',('unknown',operation))
    if mutation=='no_ack':store.db.execute('UPDATE draft_operations SET acknowledgement=? WHERE id=?',('',operation))
    if mutation=='wrong_request':proof['request_hash']=digest('another')
    if mutation=='wrong_owner':proof['owner_verified']=False
    if mutation=='wrong_posting':proof['posting_identity']=['url','https://other.invalid/']
    store.event('workday_ui_submission_verified',aid,proof)
    if mutation=='duplicate_proof':store.event('workday_ui_submission_verified',aid,proof)
    if mutation!='no_intent':store.finish(aid,'confirmed','Synthetic employer receipt')
    else:store.db.execute("UPDATE applications SET state='confirmed',updated=? WHERE id=?",(now(),aid))
    bundle=snapshot(store,aid)
    store.update_settings({'allowed_application_ats':['workday'],'stop_on_uncertain_outcome':True,
                           'target_per_cycle':3,'target_per_day':10,
                           'max_per_cycle':3,'max_per_day':10,'max_attempts_per_cycle':8})
    if mutation=='none':
        assert bundle['verification_method']=='verified_ui_application_readback'
        assert commission(store,'restricted')['proofs']=={'workday':aid}
    else:
        assert bundle['verification_method']!='verified_ui_application_readback'
        with pytest.raises(ValueError,match='verified native'):commission(store,'restricted')
