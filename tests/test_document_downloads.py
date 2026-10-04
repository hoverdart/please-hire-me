import json
import pytest

from hireme.document_downloads import recorded_pdf


def test_download_uses_recorded_pdf_after_current_upload_changes(store,job,package):
    aid=store.prepare(job,package); store.begin_submit(aid); store.finish(aid,'unknown')
    original=package['documents'][0]
    expected=(store.root/'documents'/original['filename']).read_bytes()
    store.db.execute("DELETE FROM documents WHERE kind='resume'")
    changes=store.db.total_changes; before=dict(store.application_record(aid))
    data,name=recorded_pdf(store,aid,0,original['hash'])
    assert data==expected and name=='resume.pdf'
    assert store.application_record(aid)==before and store.db.total_changes==changes


@pytest.mark.parametrize('state',['prepared','confirmed','awaiting_verification'])
def test_recorded_pdf_available_without_changing_submission_state(store,job,package,state):
    aid=store.prepare(job,package)
    if state!='prepared': store.begin_submit(aid); store.finish(aid,state)
    before=store.snapshot(); changes=store.db.total_changes
    data,name=recorded_pdf(store,aid,0,package['documents'][0]['hash'])
    assert data.startswith(b'%PDF-') and name=='resume.pdf'
    assert store.snapshot()==before and store.db.total_changes==changes


@pytest.mark.parametrize('damage',['missing','corrupt','link','directory','oversize'])
def test_document_download_rejects_unavailable_or_modified_original(store,job,package,tmp_path,damage):
    aid=store.prepare(job,package); document=package['documents'][0]
    path=store.root/'documents'/document['filename']
    if damage=='missing': path.unlink()
    elif damage=='corrupt': path.write_bytes(b'%PDF- changed synthetic bytes')
    elif damage=='link':
        outside=tmp_path/'outside.pdf'; outside.write_bytes(path.read_bytes())
        path.unlink(); path.symlink_to(outside)
    elif damage=='directory': path.unlink(); path.mkdir()
    else:
        with path.open('wb') as target: target.truncate(21*1024*1024)
    with pytest.raises(ValueError): recorded_pdf(store,aid,0,document['hash'])


@pytest.mark.parametrize('index,hash_value',[(-1,'a'*64),(True,'a'*64),(100,'a'*64),(0,'wrong'),(0,'b'*64)])
def test_document_download_requires_exact_recorded_index_and_hash(store,job,package,index,hash_value):
    aid=store.prepare(job,package)
    with pytest.raises(ValueError): recorded_pdf(store,aid,index,hash_value)


@pytest.mark.parametrize('package_value',['broken','[]','{"documents": [null]}','{"documents": "broken"}'])
def test_document_download_reports_malformed_recorded_packages(store,job,package,package_value):
    aid=store.prepare(job,package)
    store.db.execute('UPDATE applications SET package=? WHERE id=?',(package_value,aid))
    with pytest.raises(ValueError): recorded_pdf(store,aid,0,package['documents'][0]['hash'])


def test_document_download_rejects_untrusted_paths_and_unsupported_kinds(store,job,package):
    aid=store.prepare(job,package); h=package['documents'][0]['hash']
    for document in ({'kind':'resume','hash':h,'filename':'../integrations/provider-key.json'}, {'kind':'other','hash':h,'filename':h+'.pdf'}):
        store.db.execute('UPDATE applications SET package=? WHERE id=?',(json.dumps({'documents':[document]}),aid))
        with pytest.raises(ValueError): recorded_pdf(store,aid,0,h)
    with pytest.raises(ValueError): recorded_pdf(store,'missing',0,h)


@pytest.mark.parametrize('kind,name',[('resume','resume.pdf'),('transcript','transcript.pdf'),('cover_letter','cover-letter.pdf')])
def test_download_names_are_fixed_and_generated_history_does_not_use_current_cache(store,job,package,kind,name):
    document={**package['documents'][0],'kind':kind,'generated':kind=='cover_letter'}
    aid=store.prepare(job,package)
    # Synthetic historical metadata: the current document/cache may be absent.
    store.db.execute('UPDATE applications SET package=? WHERE id=?',(json.dumps({**package,'documents':[document]}),aid))
    data,download_name=recorded_pdf(store,aid,0,document['hash'])
    assert data.startswith(b'%PDF-') and download_name==name


def test_real_generated_letter_remains_downloadable_after_current_cache_is_removed(store,job,package):
    from hireme.letters import generate_cover_letter
    store.update_settings({'tailored_writing':True,'cover_letters':True})
    store.put_template('experience','I built Python services for operational workflows and tested them carefully.')
    class SyntheticModel:
        def draft_answer(self,label,choices,context,maxlength):
            return {'answer':'I enjoy building Python services for operational workflows.\n\nI test those services carefully because teams depend on them.\n\nI would like to bring that experience to this role.', 'sentence_ids':[choices[0]['id']]}
    letter=generate_cover_letter(store,job,SyntheticModel())
    letter['field']={'label':'Cover letter','type':'file','required':True}
    aid=store.prepare(job,{**package,'documents':package['documents']+[letter]})
    store.begin_submit(aid); store.finish(aid,'confirmed')
    original=(store.root/'documents'/letter['filename']).read_bytes()
    store.db.execute('DELETE FROM generated_documents')
    changes=store.db.total_changes
    data,name=recorded_pdf(store,aid,1,letter['hash'])
    assert data==original and name=='cover-letter.pdf' and store.db.total_changes==changes
    from pypdf import PdfReader
    import io
    text=PdfReader(io.BytesIO(data)).pages[0].extract_text()
    assert 'Test Person' in text and 'Acme' in text


@pytest.mark.parametrize('kind',['resume','transcript'])
def test_selected_pdf_download_uses_current_selection_without_writes(store,kind):
    from hireme.document_downloads import selected_pdf
    doc=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    if kind=='transcript': store.db.execute('INSERT INTO documents VALUES(?,?,?)',(kind,doc['hash'],doc['filename']))
    before=store.snapshot(); changes=store.db.total_changes
    data,name=selected_pdf(store,kind,doc['hash'])
    assert data==(store.root/'documents'/doc['filename']).read_bytes() and name==kind+'.pdf'
    assert store.snapshot()==before and store.db.total_changes==changes


def test_selected_pdf_rejects_stale_selection_and_withdrawn_transcript(store,monkeypatch):
    from hireme.document_downloads import selected_pdf
    def forbidden(*args): raise AssertionError('Read a stale file')
    monkeypatch.setattr('hireme.document_downloads._read_pdf_bytes',forbidden)
    with pytest.raises(ValueError,match='changed'): selected_pdf(store,'resume','a'*64)
    with pytest.raises(ValueError,match='No selected'): selected_pdf(store,'transcript','a'*64)
    with pytest.raises(ValueError): selected_pdf(store,'cover_letter','a'*64)


def test_selected_pdf_rechecks_selection_after_capturing_bytes(store,monkeypatch):
    from hireme import document_downloads
    doc=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    read=document_downloads._read_pdf_bytes
    def changing(path):
        data=read(path)
        store.db.execute("DELETE FROM documents WHERE kind='resume'")
        return data
    monkeypatch.setattr(document_downloads,'_read_pdf_bytes',changing)
    with pytest.raises(ValueError,match='selected PDF changed'): document_downloads.selected_pdf(store,'resume',doc['hash'])


def test_selected_pdf_rejects_modified_bytes_and_bad_storage_reference(store):
    from hireme.document_downloads import selected_pdf
    doc=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    path=store.root/'documents'/doc['filename']; path.write_bytes(b'%PDF- changed synthetic file')
    with pytest.raises(ValueError,match='missing or changed'): selected_pdf(store,'resume',doc['hash'])
    store.db.execute("UPDATE documents SET filename='../integrations/provider-key.json' WHERE kind='resume'")
    with pytest.raises(ValueError): selected_pdf(store,'resume',doc['hash'])
