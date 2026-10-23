import hashlib
import io
import json

import pytest

from hireme import history_import,tailored_resumes
from hireme.util import Blocked
from tests.test_history_api import history_server,request

TEXT = [
    'Test Person','EXPERIENCE',
    'Previous Employer | Software Intern | May 2025 - August 2025',
    '- Built Python services for scheduling.',
    '- Tested distributed systems.',
    'Another Employer | Research Intern | June 2024 - August 2024',
    '- Evaluated image processing pipelines.',
    'EDUCATION',
    'Example University | B.S. | Computer Science | Expected May 2028',
    'SKILLS','Python, TypeScript',
]


def select_pdf(store,lines=TEXT):
    from reportlab.pdfgen.canvas import Canvas
    stream=io.BytesIO();canvas=Canvas(stream,pagesize=(612,792))
    for index,line in enumerate(lines):canvas.drawString(40,750-index*18,line)
    canvas.save();data=stream.getvalue();checksum=hashlib.sha256(data).hexdigest()
    (store.root/'documents'/(checksum+'.pdf')).write_bytes(data)
    store.db.execute("INSERT OR REPLACE INTO documents VALUES('resume',?,?)",(checksum,checksum+'.pdf'))
    return checksum


@pytest.fixture
def source(store):
    select_pdf(store)
    return history_import.preview(store)


def payload(source,index=0,**changes):
    proposal=source['candidates'][index]
    return {'resume_hash':source['resume_hash'],**{key:proposal[key] for key in
        ('kind','start_line','end_line','values','excerpt_hash')},'confirmed':True,**changes}


def test_preview_is_local_read_only_and_proposes_exact_excerpts_without_confirming(store,source):
    before=store.snapshot();changes=store.db.total_changes
    result=history_import.preview(store)
    assert result==source and store.snapshot()==before and store.db.total_changes==changes
    assert len(source['candidates'])==3
    first=source['candidates'][0]
    assert first['values']=={'organization':'Previous Employer','title':'Software Intern','start':'2025-05',
        'end':'2025-08','description':'- Built Python services for scheduling.\n- Tested distributed systems.'}
    assert source['candidates'][2]['values']['expected_end']=='2028-05'
    assert first['excerpt']=='\n'.join(source['lines'][first['start_line']-1:first['end_line']])
    assert not store.history_entries() and not store.db.execute('SELECT 1 FROM model_requests').fetchone()


def test_confirm_preserves_original_pdf_and_records_source_and_reviewed_corrections(store,source):
    original=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    data=payload(source);data['values']={**data['values'],'description':'Built Python services for scheduling.\nTested distributed systems.'}
    result=history_import.confirm(store,data);row=store.history_entries()[0]
    assert row['id']==result['entry_id'] and row['confirmed'] and row['values']==data['values']
    proof=json.loads(store.db.execute("SELECT detail FROM events WHERE kind='history_import_confirmed'").fetchone()[0])
    assert proof['excerpt']==source['candidates'][0]['excerpt'] and proof['values']==data['values']
    assert proof['resume_hash']==source['resume_hash']
    assert dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())==original
    assert source['resume_hash'] in row['source']


def test_repeating_confirmation_is_idempotent_and_never_overwrites_manual_history(store,source):
    existing=store.put_history_entry('employment',source['candidates'][0]['values'],source='Original applicant record',confirmed=True)
    first=history_import.confirm(store,payload(source));changes=store.db.total_changes
    assert first=={'entry_id':existing['id'],'existing':True}
    assert history_import.confirm(store,payload(source))==first
    assert store.history_entries()[0]==existing and store.db.total_changes==changes


@pytest.mark.parametrize('change',['edit','withdraw'])
def test_reimport_does_not_overwrite_or_revive_a_changed_entry(store,source,change):
    result=history_import.confirm(store,payload(source));entry=store.history_entries()[0]
    if change=='withdraw':store.withdraw_history_entry(result['entry_id'])
    else:store.put_history_entry('employment',{**entry['values'],'title':'Applicant correction'},source='User correction',confirmed=True,entry_id=entry['id'])
    before=store.history_entries(confirmed_only=False)
    with pytest.raises(ValueError,match='changed or was withdrawn'):history_import.confirm(store,payload(source))
    assert store.history_entries(confirmed_only=False)==before


@pytest.mark.parametrize('change',['replacement','tamper','excerpt','lines'])
def test_import_rejects_changed_or_unreviewed_sources(store,source,change):
    data=payload(source)
    if change=='replacement':select_pdf(store,['Test Person','EXPERIENCE','Changed Employer | Intern | May 2025 - June 2025'])
    elif change=='tamper':(store.root/'documents'/(source['resume_hash']+'.pdf')).write_bytes(b'%PDF- changed')
    elif change=='excerpt':data['excerpt_hash']='a'*64
    else:data['end_line']+=1
    with pytest.raises(ValueError):history_import.confirm(store,data)
    assert not store.history_entries()


@pytest.mark.parametrize('changes',[{'confirmed':False},{'confirmed':1},{'values':{'organization':'X'}},
    {'values':{'organization':'X','title':'Intern','start':'2025','end':'2024-01'}},
    {'values':{'organization':'X','title':'Intern','current':True,'end':'2025-01'}},{'unknown':'value'}])
def test_import_requires_explicit_confirmation_and_valid_history_fields(store,source,changes):
    with pytest.raises(ValueError):history_import.confirm(store,payload(source,**changes))
    assert not store.history_entries()


def test_unknown_layout_and_year_only_dates_allow_manual_review_without_inventing_months(store):
    select_pdf(store,['Test Person','Unusual layout','Acme | Intern | 2024 - 2025','Built a service.'])
    source=history_import.preview(store);assert not source['candidates']
    selected={'resume_hash':source['resume_hash'],'kind':'employment','start_line':3,'end_line':4}
    proposal=history_import.suggest(store,selected)
    assert 'start' not in proposal['values'] and 'end' not in proposal['values'] and 'current' not in proposal['values']
    result=history_import.confirm(store,{**selected,'excerpt_hash':proposal['excerpt_hash'],'values':{'organization':'Acme','title':'Intern'},'confirmed':True})
    assert result['entry_id'] and store.history_entries()[0]['values']=={'organization':'Acme','title':'Intern'}


def test_separate_company_title_and_date_lines_keep_original_employer_in_the_excerpt():
    lines=['EXPERIENCE','Previous Employer','Software Intern','May 2025 - August 2025','- Built services.']
    proposal=history_import.candidates(lines)[0]
    assert proposal['start_line']==2 and proposal['values']['organization']=='Previous Employer'
    assert proposal['values']['title']=='Software Intern'


def test_year_only_roles_are_separate_and_cross_employer_descriptions_are_not_proposed():
    lines=['EXPERIENCE','Employer A | Intern | 2024 - 2025','- Built system A.',
           'Employer B | Researcher | 2022 - 2023','- Built system B.']
    proposals=history_import.candidates(lines)
    assert len(proposals)==2
    assert proposals[0]['values']['description']=='- Built system A.'
    assert proposals[1]['values']['description']=='- Built system B.'
    assert 'start' not in proposals[0]['values']
    assert history_import._values(lines[1:],'employment')=={}


def test_resume_change_during_confirmation_is_checked_again_before_saving(store,source,monkeypatch):
    original=history_import._selection
    def replace(*args):
        excerpt=original(*args);select_pdf(store,['Test Person','EXPERIENCE','Different Employer | Intern'])
        return excerpt
    monkeypatch.setattr(history_import,'_selection',replace)
    with pytest.raises(ValueError,match='selected resume changed'):history_import.confirm(store,payload(source))
    assert not store.history_entries()


def test_history_import_changes_future_tailoring_authority_but_preserves_attempts(store,source,job,package):
    first=history_import.confirm(store,payload(source));store.update_settings({'tailored_resumes':True})
    document=tailored_resumes.generate(store,job)
    package['documents']=[{**document,'field':package['documents'][0]['field']}]
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    original=store.application_record(aid)
    history_import.confirm(store,payload(source,1))
    with pytest.raises(Blocked,match='stale'):tailored_resumes.validate(store,job,document)
    assert store.application_record(aid)==original
    store.withdraw_history_entry(first['entry_id'])
    assert store.application_record(aid)==original


def test_import_provenance_survives_private_backup(store,source,tmp_path):
    from hireme.backup import create_backup,restore_backup
    from hireme.store import Store
    result=history_import.confirm(store,payload(source));archive=tmp_path/'history.zip'
    create_backup(store,archive);restore_backup(archive,tmp_path/'restored');restored=Store(tmp_path/'restored')
    try:
        proof=json.loads(restored.db.execute("SELECT detail FROM events WHERE kind='history_import_confirmed'").fetchone()[0])
        assert proof['entry_id']==result['entry_id'] and proof['excerpt']==source['candidates'][0]['excerpt']
    finally:restored.close()


def test_import_endpoints_are_authenticated_and_source_bound(store,source,history_server):
    assert request(history_server,'/api/history-import',headers={'X-Hireme-Token':''})[0]==403
    assert request(history_server,'/api/history-import/confirm',payload(source),headers={'X-Hireme-Token':''})[0]==403
    assert request(history_server,'/api/history-import')[1]['resume_hash']==source['resume_hash']
    assert request(history_server,'/api/history-import/confirm',payload(source,confirmed=False))[0]==400
    assert request(history_server,'/api/history-import/confirm',payload(source))[0]==200
