import pytest

from hireme.util import Blocked
from hireme.worker import cycle
from pathlib import Path


@pytest.mark.parametrize('damage',['missing','empty','not_pdf','file_link','directory_link'])
def test_unavailable_resume_blocks_setup_and_worker_before_discovery(store,tmp_path,monkeypatch,damage):
    document=store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
    directory=store.root/'documents';path=directory/document['filename']
    assert store.document_available('resume') and not store.missing_setup()
    if damage=='missing':path.unlink()
    elif damage=='empty':path.write_bytes(b'')
    elif damage=='not_pdf':path.write_bytes(b'Synthetic non-PDF content')
    elif damage=='file_link':
        outside=tmp_path/'outside.pdf';outside.write_bytes(path.read_bytes());path.unlink();path.symlink_to(outside)
    else:
        outside=tmp_path/'moved-documents';directory.rename(outside);directory.symlink_to(outside,target_is_directory=True)
    assert not store.document_available('resume') and 'resume' in store.missing_setup()
    assert store.snapshot()['documents'][0]['available'] is False
    monkeypatch.setattr('hireme.worker.sweep_lists',lambda s:pytest.fail('Unavailable resume must stop before discovery'))
    with pytest.raises(Blocked,match='setup_incomplete'):cycle(store,Path('.'))
    assert not store.db.execute('SELECT * FROM applications').fetchone()
    assert not store.db.execute('SELECT * FROM model_requests').fetchone()


def test_optional_transcript_availability_does_not_make_required_setup_incomplete(store):
    resume=store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
    store.db.execute('INSERT INTO documents VALUES(?,?,?)',('transcript','0'*64,'0'*64+'.pdf'))
    assert not store.document_available('transcript') and not store.missing_setup()
    documents={row['kind']:row for row in store.snapshot()['documents']}
    assert documents['resume']['available'] and not documents['transcript']['available']
