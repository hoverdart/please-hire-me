import pytest
from reportlab.pdfgen import canvas

from hireme.onboarding import import_resume
from hireme.util import Blocked,digest


def resume_pdf(path,lines):
    pdf=canvas.Canvas(str(path))
    for index,line in enumerate(lines):pdf.drawString(72,720-index*20,line)
    pdf.save();return path


def test_exact_confirmed_resume_values_keep_revisions_and_identical_import_keeps_drafts(store,job,package,tmp_path):
    path=resume_pdf(tmp_path/'resume.pdf',['Test Person','Synthetic candidate resume.'])
    before=store.facts()
    import_resume(store,path)
    assert store.facts()==before
    document=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    aid=store.prepare(job,{**package,'documents':[{**document,'field':package['documents'][0]['field']}]})
    draft=dict(store.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone())
    import_resume(store,path)
    assert store.facts()==before
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone())==draft


def test_changed_name_requires_confirmation_and_identity_rejection_does_not_select_another_pdf(store,job,package,tmp_path):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed')
    document=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone());before=store.facts(False)
    rejected=resume_pdf(tmp_path/'another-person.pdf',['Another Person','another@candidate.invalid'])
    with pytest.raises(ValueError,match='identity cannot change'):import_resume(store,rejected)
    assert dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())==document
    assert store.facts(False)==before
    accepted=resume_pdf(tmp_path/'changed-name.pdf',['Changed Person','test@candidate.invalid'])
    import_resume(store,accepted)
    assert store.facts(False)['full_name']['value']=='Changed Person'
    assert not store.facts(False)['full_name']['confirmed']
    assert store.facts()['email']==before['email']
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(aid,)).fetchone()[0]=='confirmed'


def test_document_storage_failure_rolls_back_fact_proposals_and_selected_pdf(store,job,package,tmp_path,monkeypatch):
    aid=store.prepare(job,package)
    store.db.execute("UPDATE jobs SET status='prepared' WHERE id=?",(job['id'],))
    before=store.facts(False);document=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    profile=(store.root/'config'/'profile.json').read_bytes()
    path=resume_pdf(tmp_path/'new.pdf',['Changed Person','Synthetic applicant.'])
    def fail(*args):raise OSError('Synthetic document storage failure')
    monkeypatch.setattr('hireme.onboarding.write_private_blob',fail)
    with pytest.raises(OSError):import_resume(store,path)
    assert store.facts(False)==before and (store.root/'config'/'profile.json').read_bytes()==profile
    assert dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())==document
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(aid,)).fetchone()[0]=='prepared'
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='prepared'


def test_nested_fact_transaction_rolls_back_without_export_and_cannot_reserve_model_calls(store):
    before=store.facts(False);profile=(store.root/'config'/'profile.json').read_bytes()
    with pytest.raises(ValueError):
        with store.transaction():
            store.put_facts({'full_name':'Changed Person'})
            with pytest.raises(Blocked,match='model_request_transaction'):store.reserve_model_request()
            raise ValueError('Synthetic outer rollback')
    assert store.facts(False)==before and (store.root/'config'/'profile.json').read_bytes()==profile
    assert not store.db.execute('SELECT * FROM model_requests').fetchone()


def test_replacing_a_transcript_clears_only_its_draft_and_resets_its_prepared_label(store,job,package,tmp_path):
    transcript=resume_pdf(tmp_path/'first-transcript.pdf',['Synthetic transcript: original.'])
    import_resume(store,transcript,'transcript')
    document=dict(store.db.execute("SELECT * FROM documents WHERE kind='transcript'").fetchone())
    aid=store.prepare(job,{**package,'documents':[*package['documents'],{**document,'field':package['documents'][0]['field']}]})
    store.db.execute("UPDATE jobs SET status='prepared' WHERE id=?",(job['id'],))
    second={**job,'id':digest('without-transcript'),'url':job['url']+'-second','company':'Other Company'}
    store.upsert_job(second)
    unrelated=store.prepare(second,{**package,'job_id':second['id'],'url':second['url']})
    replacement=resume_pdf(tmp_path/'second-transcript.pdf',['Synthetic transcript: updated.'])
    import_resume(store,replacement,'transcript')
    assert not store.db.execute('SELECT 1 FROM applications WHERE id=?',(aid,)).fetchone()
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='discovered'
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(unrelated,)).fetchone()[0]=='prepared'


def test_fact_changes_clear_prepared_labels_but_preserve_manual_holds(store,job,package):
    aid=store.prepare(job,package)
    store.db.execute("UPDATE jobs SET status='prepared' WHERE id=?",(job['id'],))
    store.put_facts({'phone':'5557654321'})
    assert not store.db.execute('SELECT 1 FROM applications WHERE id=?',(aid,)).fetchone()
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='discovered'
    fresh={**package,'facts_hash':digest(store.facts())}
    store.prepare(job,fresh);store.block(job['id'],'company_blocked')
    store.put_facts({'phone':'5557654322'})
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='blocked'


def test_import_parses_the_captured_bytes_when_the_input_changes(store,tmp_path,monkeypatch):
    from hireme import onboarding
    source=resume_pdf(tmp_path/'source.pdf',['Test Person','Original synthetic resume.'])
    original=source.read_bytes()
    replacement=resume_pdf(tmp_path/'replacement.pdf',['Another Person','Replacement synthetic resume.']).read_bytes()
    read=onboarding._read_pdf_bytes
    def changing(path):
        captured=read(path);path.write_bytes(replacement);return captured
    monkeypatch.setattr(onboarding,'_read_pdf_bytes',changing)
    result=import_resume(store,source)
    assert result['candidates']['full_name']=='Test Person' and 'Original synthetic resume' in result['text']
    assert (store.root/'documents'/(result['hash']+'.pdf')).read_bytes()==original


@pytest.mark.parametrize('state',['confirmed','unknown','awaiting_verification'])
def test_matching_pdf_import_repairs_corruption_without_changing_past_records(store,job,package,tmp_path,state):
    path=resume_pdf(tmp_path/'original.pdf',['Test Person','Synthetic original resume.'])
    first=import_resume(store,path)
    document=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    aid=store.prepare(job,{**package,'documents':[{**document,'field':package['documents'][0]['field']}]})
    store.begin_submit(aid);store.finish(aid,state)
    attempted=dict(store.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone());facts=store.facts()
    stored=store.root/'documents'/(first['hash']+'.pdf');stored.write_bytes(b'Synthetic corrupted bytes')
    assert not store.document_available('resume')
    repaired=import_resume(store,path)
    assert repaired['repaired'] and stored.read_bytes()==path.read_bytes()
    assert store.document_available('resume') and store.facts()==facts
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone())==attempted
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='document_repaired'").fetchone()[0]==1


def test_import_does_not_repair_a_linked_destination_and_rejects_oversized_input(store,tmp_path):
    source=resume_pdf(tmp_path/'source.pdf',['Test Person','Synthetic resume.'])
    result=import_resume(store,source);stored=store.root/'documents'/(result['hash']+'.pdf')
    outside=tmp_path/'outside.pdf';outside.write_bytes(source.read_bytes());stored.unlink();stored.symlink_to(outside)
    with pytest.raises(ValueError,match='Unsafe imported PDF destination'):import_resume(store,source)
    assert outside.read_bytes()==source.read_bytes()
    large=tmp_path/'large.pdf'
    with large.open('wb') as output:
        output.write(b'%PDF-');output.truncate(21*1024*1024)
    with pytest.raises(ValueError,match='exceeds 20 MiB'):import_resume(store,large)


def test_matching_pdf_import_repairs_unreadable_file_permissions(store,tmp_path):
    source=resume_pdf(tmp_path/'source.pdf',['Test Person','Synthetic resume.'])
    result=import_resume(store,source);stored=store.root/'documents'/(result['hash']+'.pdf')
    stored.chmod(0)
    try:
        assert not store.document_available('resume')
        assert import_resume(store,source)['repaired']
        assert store.document_available('resume') and stored.stat().st_mode & 0o777==0o600
    finally:stored.chmod(0o600)
