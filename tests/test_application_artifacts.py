import hashlib
import io
import pytest
from pypdf import PdfReader
from hireme import application_artifacts as artifacts
from hireme.util import Blocked
from tests.test_letters import setup_letter

PROMPT='Tell us about your experience building Python software and why this role interests you.'


def test_introduction_grounded_cached_and_source_revisions_preserved(store,job):
    model=setup_letter(store)
    first=artifacts.generate(store,job,'introduction',PROMPT,model)
    assert artifacts.generate(store,job,'introduction',PROMPT,model)==first and model.calls==1
    assert artifacts.validate(store,job,first).decode()==first['content']
    store.put_facts({'full_name':'Updated Person'})
    with pytest.raises(Blocked,match='stale_writing_context'):artifacts.validate(store,job,first)
    second=artifacts.generate(store,job,'introduction',PROMPT,model)
    assert second['revision']==2 and second['id']!=first['id']
    assert (store.root/'documents'/first['filename']).read_text()==first['content']
    assert artifacts.library(store,job['id'])['total']==2


def test_response_pdf_content_bounds_and_historical_download(store,job):
    first=artifacts.generate(store,job,'supplemental_response',PROMPT,setup_letter(store))
    data=artifacts.validate(store,job,first)
    assert data.startswith(b'%PDF-') and len(data)<=1024*1024
    text=PdfReader(io.BytesIO(data)).pages[0].extract_text()
    assert 'Test Person' in text and 'Acme' in text and ' '.join(first['content'].split()) in ' '.join(text.split())
    import fitz
    pdf=fitz.open(stream=data,filetype='pdf')
    for block in pdf[0].get_text('blocks'):assert 48<=block[0]<block[2]<=564 and 42<=block[1]<block[3]<=750
    pdf[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save('/tmp/hireme-supplemental-response.png')
    store.put_facts({'full_name':'Updated Person'})
    path,name=artifacts.download(store,first['id'],first['hash'])
    assert hashlib.sha256(path).hexdigest()==first['hash'] and name.endswith('.pdf')


@pytest.mark.parametrize('body,reason',[('unsupported 漢字','font_unsupported'),('long response '*2000,'render_failed')])
def test_pdf_unsupported_characters_and_overflow_stop(body,reason):
    with pytest.raises(Blocked,match=reason):artifacts.render_response('Applicant','Company','Role','Prompt',body)


@pytest.mark.parametrize('prompt',['Provide an official transcript.','Attach your work sample.','Complete this coding assessment.'])
def test_official_and_substantive_materials_require_originals(store,job,prompt):
    setup_letter(store)
    with pytest.raises(Blocked,match='supplied_document_required'):artifacts.generate(store,job,'supplemental_response',prompt)
    assert artifacts.library(store)['total']==0


def test_disabled_writing_missing_sources_tampering_and_wrong_job(store,job):
    with pytest.raises(Blocked,match='writing_upgrade_needed'):artifacts.generate(store,job,'introduction',PROMPT)
    model=setup_letter(store);first=artifacts.generate(store,job,'introduction',PROMPT,model)
    with pytest.raises(Blocked,match='artifact_changed'):artifacts.validate(store,{**job,'id':'other'},first)
    (store.root/'documents'/first['filename']).write_text('changed')
    with pytest.raises(Blocked,match='tampered'):artifacts.validate(store,job,first)


def test_generation_refuses_sources_changed_by_model(store,job):
    model=setup_letter(store);original=model.draft_answer
    def draft(*args):
        result=original(*args);store.put_facts({'skills':'Python, Rust'});return result
    model.draft_answer=draft
    with pytest.raises(Blocked):artifacts.generate(store,job,'introduction',PROMPT,model)
    assert artifacts.library(store)['total']==0


def test_backup_restores_artifact_revisions_but_not_platform_sessions(store,job,tmp_path):
    from hireme.backup import create_backup,restore_backup
    from hireme.store import Store
    from hireme import platform_connections as pc
    model=setup_letter(store)
    intro=artifacts.generate(store,job,'introduction',PROMPT,model)
    cover=artifacts.generate(store,job,'cover_letter',None,model)
    pc.configure(store,'handshake',{'enabled':True,'native_apply_enabled':True})
    pc.profile_path(store,'handshake')
    archive=tmp_path/'history.zip';create_backup(store,archive)
    destination=tmp_path/'restored';restore_backup(archive,destination)
    restored=Store(destination)
    try:
        assert artifacts.library(restored)['total']==2
        assert artifacts.validate(restored,job,intro).decode()==intro['content']
        assert artifacts.download(restored,cover['id'],cover['hash'])[0].startswith(b'%PDF-')
        assert not pc.status(restored,'handshake')['enabled'] and pc.profile_binding(restored,'handshake') is None
    finally:restored.close()


def test_project_links_require_exact_approved_sources_and_extract_from_pdf():
    url='https://github.com/applicant/verified-project'
    answer={'value':'I built this project: '+url,'provenance':{'sample_parts':[{'text':'Project: '+url}]}}
    artifacts.validate_links(answer)
    data=artifacts.render_response('Applicant','Employer','Role','Share a project link.',answer['value'])
    assert url in PdfReader(io.BytesIO(data)).pages[0].extract_text()
    with pytest.raises(Blocked,match='writing_link_unverified'):
        artifacts.validate_links({**answer,'value':'My project: https://github.com/applicant/invented-project'})


def test_template_fallback_preserves_confirmed_project_links(store,job):
    store.update_settings({'tailored_writing':True})
    body='I built a Python project: https://github.com/applicant/verified-project'
    tid=store.put_template('project',body)
    class AbstainingDraft:
        def draft_answer(self,*args,**kwargs):return {}
    artifact=artifacts.generate(store,job,'supplemental_response','Tell us about a project.',AbstainingDraft())
    assert artifact['content']==body
    data=artifacts.validate(store,job,artifact)
    assert 'https://github.com/applicant/verified-project' in PdfReader(io.BytesIO(data)).pages[0].extract_text()
    import json
    provenance=json.loads(store.db.execute('SELECT provenance FROM application_artifacts WHERE id=?',(artifact['id'],)).fetchone()[0])['answer']
    assert provenance['provenance']['template_id']==tid
    with pytest.raises(Blocked,match='writing_link_unverified'):
        artifacts.validate_links({**provenance,'value':'https://github.com/applicant/invented-project'},store)
    store.put_template('project','I built a different Python project.',tid)
    with pytest.raises(Blocked,match='writing_link_unverified'):
        artifacts.validate_links(provenance,store)
    with pytest.raises(Blocked,match='writing_link_unverified'):
        artifacts.validate(store,job,artifact)
