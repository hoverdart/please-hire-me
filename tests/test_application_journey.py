import json

import pytest

from hireme.application_journey import journey
from hireme import recruiting_feedback, tailored_resumes
from hireme.document_downloads import recorded_pdf
from hireme.util import digest
from tests.test_recruiting_feedback import message


def attempted(store,job,package,state='unknown'):
    identity=store.prepare(job,package);store.begin_submit(identity)
    store.finish(identity,state,'Synthetic receipt or unresolved response')
    return identity


def reviewed(store,job,identity,category='interview',message_id='one'):
    assert recruiting_feedback.ingest(store,message(job,identity=message_id))
    row=next(row for row in recruiting_feedback.listing(store)['messages'] if row['message_id']==message_id)
    recruiting_feedback.review(store,{'id':row['id'],'revision':row['revision'],'state':'confirmed',
                                      'category':category,'application_id':identity})


def test_attempt_keeps_its_posting_identity_and_exact_documents_after_feed_refresh(store,job,package):
    identity=attempted(store,job,package)
    record=store.application_record(identity);changes=store.db.total_changes
    view=journey(store,identity)
    assert store.db.total_changes==changes and store.application_record(identity)==record
    assert view['posting_context_source']=='submit_intent'
    assert view['posting']['company']=='Acme' and view['posting']['source']=='test'
    assert view['package_hash']==digest(package) and view['package_integrity']=='verified'
    assert view['documents'][0]['hash']==package['documents'][0]['hash']
    assert view['submission']['confirmation']==record['confirmation']
    store.upsert_job({**job,'company':'Renamed Company','title':'Another role','source':'another-feed'})
    assert journey(store,identity)['posting']==view['posting']
    assert 'answers' not in view and 'facts' not in view


def test_context_is_captured_again_at_final_intent_without_changing_the_package(store,job,package):
    identity=store.prepare(job,package)
    assert journey(store,identity)['posting_context_source']=='prepared'
    store.upsert_job({**job,'source':'updated-source'})
    assert store.begin_submit(identity)==package
    view=journey(store,identity)
    assert view['posting']['source']=='updated-source'
    assert view['posting_context_source']=='submit_intent'
    assert json.loads(store.application_record(identity)['package'])==package


def test_old_attempt_labels_current_metadata_and_missing_identity_honestly(store,job,package):
    identity=attempted(store,job,package)
    store.db.execute("DELETE FROM events WHERE subject=? AND kind IN ('prepared','submit_intent')",(identity,))
    view=journey(store,identity)
    assert view['posting_context_source']=='current_job_metadata' and view['posting_context_captured'] is None
    store.db.execute('DELETE FROM jobs WHERE id=?',(job['id'],))
    view=journey(store,identity)
    assert view['posting_context_source']=='unavailable' and 'company' not in view['posting']
    assert view['submission']['state']=='unknown'


def test_resume_revision_is_the_recorded_artifact_even_after_sources_change(store,job,package):
    store.update_settings({'tailored_resumes':True})
    history=store.put_history_entry('employment',{'organization':'Previous Employer','title':'Intern',
        'description':'Built Python services.'},source='Applicant confirmed record',confirmed=True)
    first=tailored_resumes.generate(store,job)
    package['documents']=[{**first,'field':package['documents'][0]['field']}]
    identity=attempted(store,job,package)
    store.put_history_entry('employment',{**history['values'],'description':'Built Python services.\nTested distributed systems.'},
        source='Applicant correction',confirmed=True,entry_id=history['id'])
    second=tailored_resumes.generate(store,job)
    assert first['artifact_id']!=second['artifact_id']
    view=journey(store,identity)
    assert view['documents'][0]['artifact_id']==first['artifact_id'] and view['documents'][0]['revision']==1
    assert recorded_pdf(store,identity,0,first['hash'])[0].startswith(b'%PDF-')


@pytest.mark.parametrize('category',['assessment','interview','rejection','offer'])
def test_reviewed_feedback_never_overrides_an_uncertain_submission_or_its_next_action(store,job,package,category):
    identity=attempted(store,job,package)
    reviewed(store,job,identity,category)
    record=store.application_record(identity);changes=store.db.total_changes
    view=journey(store,identity)
    assert view['recruiting_stage']==category
    assert view['next_action']['kind']=='verify_submission'
    assert view['timeline'][-1]['kind']=='recruiting_feedback'
    assert store.application_record(identity)==record and store.db.total_changes==changes


def test_pending_messages_require_review_and_missing_feedback_is_not_rejection(store,job,package):
    identity=attempted(store,job,package,'confirmed')
    assert journey(store,identity)['next_action']['kind']=='await_feedback'
    recruiting_feedback.ingest(store,message(job))
    view=journey(store,identity)
    assert view['recruiting_stage'] is None and len(view['pending_feedback'])==1
    assert view['next_action']['kind']=='review_feedback'
    reviewed(store,job,identity,'interview','reviewed-message')
    view=journey(store,identity)
    assert view['recruiting_stage']=='interview' and view['next_action']['kind']=='review_feedback'


def test_acknowledgement_does_not_erase_a_later_stage_and_other_owner_mail_is_excluded(store,job,package):
    identity=attempted(store,job,package,'confirmed')
    reviewed(store,job,identity,'interview','first')
    reviewed(store,job,identity,'acknowledgement','second')
    assert journey(store,identity)['recruiting_stage']=='interview'
    assert journey(store,identity)['next_action']['kind']=='interview'
    store.db.execute("UPDATE recruiting_feedback SET owner_hash='another-owner'")
    view=journey(store,identity)
    assert not view['reviewed_feedback'] and view['recruiting_stage'] is None


def test_archived_attempt_and_its_context_survive_private_backup(store,job,package,tmp_path):
    from hireme.backup import create_backup,restore_backup
    from hireme.store import Store,APPLICATION_COLUMNS
    identity=attempted(store,job,package)
    reviewed(store,job,identity)
    store.db.execute(f"INSERT INTO archived_applications SELECT {APPLICATION_COLUMNS},'fixture' FROM applications WHERE id=?",(identity,))
    store.db.execute('DELETE FROM applications WHERE id=?',(identity,))
    before=journey(store,identity)
    archive=tmp_path/'history.zip';create_backup(store,archive)
    restore_backup(archive,tmp_path/'restored');restored=Store(tmp_path/'restored')
    try:
        after=journey(restored,identity)
        for key in ('posting','posting_context_source','documents','submission','reviewed_feedback'):
            assert after[key]==before[key]
    finally:restored.close()


@pytest.mark.parametrize('package_value',['invalid','[]','{}'])
def test_corrupt_package_is_unavailable_without_hiding_submission_state(store,job,package,package_value):
    identity=attempted(store,job,package,'confirmed')
    store.db.execute('UPDATE applications SET package=? WHERE id=?',(package_value,identity))
    view=journey(store,identity)
    assert view['package_integrity']!='verified' and not view['documents']
    assert view['submission']['state']=='confirmed' and view['next_action']['kind']=='inspect_evidence'


def test_read_view_uses_no_writer_lock_and_preserves_an_enclosing_transaction(store,job,package):
    identity=store.prepare(job,package);statements=[]
    store.db.set_trace_callback(statements.append)
    journey(store,identity)
    assert 'BEGIN' in statements and 'BEGIN IMMEDIATE' not in statements
    with store.transaction():
        store.db.execute("UPDATE jobs SET score=72 WHERE id=?",(job['id'],))
        assert journey(store,identity)['submission']['state']=='prepared'
        assert store.db.in_transaction
    assert store.db.execute('SELECT score FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]==72


@pytest.mark.parametrize('identity',[None,True,'','a'*201])
def test_invalid_attempt_ids_are_rejected(store,identity):
    with pytest.raises(ValueError):journey(store,identity)
