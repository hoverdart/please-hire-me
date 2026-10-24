import io
import json

import pytest
from pypdf import PdfReader

from hireme import tailored_resumes
from hireme.application_artifacts import download
from hireme.answers import validate_package
from hireme.util import Blocked
from tests.test_browser import ats,local_job


@pytest.fixture
def experience(store):
    store.update_settings({'tailored_resumes':True})
    return store.put_history_entry('employment',{
        'organization':'Previous Employer', 'title':'Software Intern', 'start':'2025-06', 'end':'2025-08',
        'description':'Built Python services for scheduling.\nMaintained TypeScript interfaces.\nWrote SQL migration tooling.\nDocumented team workflows.\nImplemented image processing in Rust.'
    },source='Applicant reviewed employment history',confirmed=True)


def test_resume_selects_exact_confirmed_experience_for_the_posting_without_model_calls(store,job,experience):
    first=tailored_resumes.generate(store,{**job,'description':'Rust image processing'})
    row=store.db.execute('SELECT * FROM application_artifacts WHERE id=?',(first['artifact_id'],)).fetchone()
    assert 'Implemented image processing in Rust.' in row['content']
    assert 'Documented team workflows.' not in row['content']
    assert 'Previous Employer' in row['content'] and job['company'] not in row['content']
    data=tailored_resumes.validate(store,{**job,'description':'Rust image processing'},first)
    reader=PdfReader(io.BytesIO(data))
    assert 1<=len(reader.pages)<=2
    assert 'Implemented image processing in Rust.' in '\n'.join(page.extract_text() for page in reader.pages)
    second=tailored_resumes.generate(store,{**job,'description':'Documented team workflows'})
    row2=store.db.execute('SELECT * FROM application_artifacts WHERE id=?',(second['artifact_id'],)).fetchone()
    assert 'Documented team workflows.' in row2['content'] and row2['revision']==2
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


def test_generated_resume_is_used_in_a_valid_package_and_reuses_unchanged_sources(store,job,package,experience):
    original=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    document=tailored_resumes.generate(store,job)
    assert tailored_resumes.generate(store,job)==document
    package['documents']=[{**document,'field':package['documents'][0]['field']}]
    validate_package(store,job,package)
    assert dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())==original
    assert store.db.execute("SELECT COUNT(*) FROM application_artifacts WHERE kind='resume'").fetchone()[0]==1


@pytest.mark.parametrize('change',['withdraw','edit','fact','posting','disabled','tamper','format'])
def test_resume_loses_submission_authority_when_inputs_change_but_historical_download_survives(store,job,experience,change,monkeypatch):
    document=tailored_resumes.generate(store,job)
    original=tailored_resumes.validate(store,job,document)
    if change=='withdraw':store.withdraw_history_entry(experience['id'])
    elif change=='edit':store.put_history_entry('employment',{**experience['values'],'title':'Revised title'},source='Applicant correction',confirmed=True,entry_id=experience['id'])
    elif change=='fact':store.put_facts({'phone':'5557654321'})
    elif change=='posting':job={**job,'description':'A changed official posting'}
    elif change=='disabled':store.update_settings({'tailored_resumes':False})
    elif change=='tamper':(store.root/'documents'/document['filename']).write_bytes(b'%PDF-tampered')
    elif change=='format':monkeypatch.setattr(tailored_resumes,'FORMAT_VERSION',tailored_resumes.FORMAT_VERSION+1)
    with pytest.raises(Blocked):tailored_resumes.validate(store,job,document)
    if change=='tamper':
        with pytest.raises(ValueError):download(store,document['artifact_id'],document['hash'])
    else:
        data,name=download(store,document['artifact_id'],document['hash'])
        assert data==original and name=='tailored-resume.pdf'


def test_resume_requires_explicit_opt_in_and_confirmed_history(store,job):
    with pytest.raises(Blocked,match='tailored_resumes_disabled'):tailored_resumes.generate(store,job)
    store.update_settings({'tailored_resumes':True})
    store.put_history_entry('employment',{'organization':'Unconfirmed Employer','title':'Invented role','description':'Must never appear in a resume.'},source='Unreviewed input')
    with pytest.raises(Blocked,match='resume_history_required'):tailored_resumes.generate(store,job)
    assert not store.db.execute('SELECT 1 FROM application_artifacts').fetchone()


def test_resume_never_infers_current_employment_or_missing_dates(store,job,experience):
    store.put_history_entry('employment',{'organization':'Undated Employer','title':'Past Role','description':'Recorded experience with no confirmed dates.'},source='Applicant confirmed',confirmed=True)
    document=tailored_resumes.generate(store,job)
    text=store.db.execute('SELECT content FROM application_artifacts WHERE id=?',(document['artifact_id'],)).fetchone()[0]
    assert 'Present' not in text and 'Undated Employer' in text


def test_current_education_keeps_confirmed_expected_graduation_in_pdf(store,job,experience):
    store.put_history_entry('education',{'institution':'Example University','degree':'B.S.',
        'field_of_study':'Computer Science','start':'2025-08','expected_end':'2028-05','current':True},
        source='Applicant confirmed education',confirmed=True)
    document=tailored_resumes.generate(store,job)
    data=tailored_resumes.validate(store,job,document)
    text=' '.join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
    assert '2025-08 - Expected 2028-05' in text


def test_resume_keeps_job_heading_dates_and_experience_on_the_same_page():
    heading='Software Intern | Second Employer'
    dates='2025-06 - 2025-08'
    description='Built verified scheduling tools for the team.'
    content={'name':'Example Applicant','contact':[],'sections':[('Experience',[
        {'heading':'Earlier Role | First Employer','lines':[f'Verified work item {i}.' for i in range(29)]},
        {'heading':heading,'lines':['Remote',dates,description,'Maintained the existing service.']}
    ])]}
    data,_=tailored_resumes.render(content)
    pages=[page.extract_text() for page in PdfReader(io.BytesIO(data)).pages]
    assert len(pages)==2
    assert any(all(value in page for value in (heading,dates,description)) for page in pages)


def test_resume_compacts_contacts_without_losing_verified_content():
    contacts=['candidate@example.invalid','555-0100','Example City, CA',
        'https://www.example.invalid/profile','https://github.com/example',
        'https://www.example.invalid/portfolio','https://www.example.invalid/projects']
    content={'name':'Example Applicant','contact':contacts,'sections':[
        ('Education',[{'heading':'Example University','lines':['B.S.','Computer Science','2025-08 - Expected 2028-05','GPA: 3.8']}]),
        ('Experience',[{'heading':f'Software Intern | Employer {i}',
            'lines':['Remote','2025-06 - 2025-08','Built verified data services.','Maintained existing tooling.']} for i in range(4)])]}
    data,_=tailored_resumes.render(content)
    pages=PdfReader(io.BytesIO(data)).pages
    assert len(pages)==1
    text=' '.join(pages[0].extract_text().split())
    assert all(value in text for value in contacts)
    assert all(f'Software Intern | Employer {i}' in text for i in range(4))


def test_resume_generation_rechecks_pause_and_sources_after_rendering(store,job,experience,monkeypatch):
    original=tailored_resumes.render
    def change(content):
        result=original(content)
        store.withdraw_history_entry(experience['id'])
        return result
    monkeypatch.setattr(tailored_resumes,'render',change)
    with pytest.raises(Blocked):tailored_resumes.generate(store,job)
    assert not store.db.execute('SELECT 1 FROM application_artifacts').fetchone()


def test_tailored_resume_and_its_provenance_survive_private_backup(store,job,experience,tmp_path):
    from hireme.backup import create_backup,restore_backup
    from hireme.store import Store
    document=tailored_resumes.generate(store,job)
    archive=tmp_path/'resume.zip';create_backup(store,archive)
    root=tmp_path/'restored';restore_backup(archive,root)
    restored=Store(root)
    try:
        row=restored.db.execute('SELECT * FROM application_artifacts WHERE id=?',(document['artifact_id'],)).fetchone()
        assert json.loads(row['provenance'])['sources']['history'][0]['id']==experience['id']
        assert download(restored,document['artifact_id'],document['hash'])[0].startswith(b'%PDF-')
        assert not restored.settings()['live_enabled']
    finally:restored.close()


def test_browser_uploads_the_tailored_pdf_and_records_a_fresh_synthetic_receipt(store,ats,experience):
    from hireme.browser import Browser
    job=local_job(store,ats)
    with Browser(store,test_url=ats[0]) as browser:
        assert browser.apply(job)=='confirmed'
        with pytest.raises(Blocked):browser.apply(job)
    assert len(ats[1])==1
    record=store.db.execute('SELECT * FROM applications').fetchone()
    package=json.loads(record['package']);document=package['documents'][0]
    assert document['kind']=='resume' and document['artifact_id']
    assert document['hash']!=store.db.execute("SELECT hash FROM documents WHERE kind='resume'").fetchone()[0]
    assert document['filename'].encode() in ats[1][0]
    refreshed=json.loads(store.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()[0])
    pdf=PdfReader(io.BytesIO(tailored_resumes.validate(store,refreshed,document)))
    assert 'Previous Employer' in '\n'.join(page.extract_text() for page in pdf.pages)
