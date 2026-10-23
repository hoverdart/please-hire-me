import json
import zipfile
from pathlib import Path
import pytest
from hireme.backup import create_backup,restore_backup
from hireme.store import Store
from hireme.materials import import_material,review_material


def test_backup_restores_identity_documents_and_history_paused_without_tokens(store,job,package,tmp_path):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed','Application received')
    source=import_material(store,b'I built Python services for operational workflows.','sample.txt','context')
    review_material(store,source['id'],source['text'],'personal',True)
    from hireme.util import atomic_json
    atomic_json(store.root/'integrations/gmail-token.json',{'token':'synthetic-secret'})
    archive=tmp_path/'backup.zip';result=create_backup(store,archive)
    assert not result['credentials_included'] and store.settings()['live_enabled']
    with zipfile.ZipFile(archive) as z:assert not any('integrations' in name for name in z.namelist())
    root=tmp_path/'restored';restore_backup(archive,root)
    restored=Store(root)
    assert restored.facts()==store.facts() and not restored.settings()['live_enabled']
    assert restored.db.execute('SELECT state FROM applications').fetchone()[0]=='confirmed'
    assert restored.db.execute('SELECT text FROM materials').fetchone()[0]==source['text']
    doc=restored.db.execute('SELECT filename FROM documents').fetchone()[0]
    assert (root/'documents'/doc).is_file()
    assert not (root/'integrations/gmail-token.json').exists()
    restored.close()
    with pytest.raises(ValueError):restore_backup(archive,root)
    with pytest.raises(ValueError):create_backup(store,archive)


def test_backup_tampering_or_path_traversal_is_rejected(store,tmp_path):
    archive=tmp_path/'backup.zip';create_backup(store,archive)
    bad=tmp_path/'tampered.zip'
    with zipfile.ZipFile(archive) as src,zipfile.ZipFile(bad,'w') as dst:
        for name in src.namelist():dst.writestr(name,b'changed' if name=='ledger.sqlite3' else src.read(name))
    with pytest.raises(ValueError):restore_backup(bad,tmp_path/'bad-root')
    assert not (tmp_path/'bad-root').exists()
    traversal=tmp_path/'traversal.zip'
    with zipfile.ZipFile(traversal,'w') as z:
        z.writestr('../outside','bad');z.writestr('manifest.json',json.dumps({'version':1,'files':{'../outside':{'bytes':3,'sha256':'bad'}}}))
    with pytest.raises(ValueError):restore_backup(traversal,tmp_path/'traversal-root')
    assert not (tmp_path/'outside').exists()


def test_backup_refuses_missing_referenced_document(store,tmp_path):
    name=store.db.execute('SELECT filename FROM documents').fetchone()[0]
    (store.root/'documents'/name).unlink()
    with pytest.raises(ValueError,match='missing or changed'):create_backup(store,tmp_path/'incomplete.zip')
    assert not (tmp_path/'incomplete.zip').exists()


def test_backup_and_restore_validate_replaced_documents_in_attempt_history(store,job,package,tmp_path):
    import hashlib
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed')
    old=package['documents'][0]['filename']
    data=b'%PDF-1.4\nSynthetic replacement resume';h=hashlib.sha256(data).hexdigest()
    (store.root/'documents'/(h+'.pdf')).write_bytes(data)
    store.db.execute("UPDATE documents SET hash=?,filename=? WHERE kind='resume'",(h,h+'.pdf'))
    archive=tmp_path/'complete.zip';create_backup(store,archive)
    # A self-consistent manifest can still omit a historical file. The ledger
    # references must independently reject that incomplete restore.
    incomplete=tmp_path/'missing-history.zip'
    with zipfile.ZipFile(archive) as source,zipfile.ZipFile(incomplete,'w') as target:
        manifest=json.loads(source.read('manifest.json'));manifest['files'].pop('documents/'+old)
        for name in source.namelist():
            if name=='documents/'+old:continue
            target.writestr(name,json.dumps(manifest) if name=='manifest.json' else source.read(name))
    with pytest.raises(ValueError,match='missing or changed'):restore_backup(incomplete,tmp_path/'incomplete-restore')
    assert not (tmp_path/'incomplete-restore').exists()
    (store.root/'documents'/old).unlink()
    with pytest.raises(ValueError,match='missing or changed'):create_backup(store,tmp_path/'missing-history-backup.zip')
    assert not (tmp_path/'missing-history-backup.zip').exists()


def test_backup_retains_withdrawn_transcript_evidence_and_checks_missing_screenshots(store,job,package,tmp_path):
    from hireme.document_controls import withdraw_transcript
    resume=package['documents'][0]
    store.db.execute('INSERT INTO documents VALUES(?,?,?)',('transcript',resume['hash'],resume['filename']))
    aid=store.prepare(job,{**package,'documents':[*package['documents'],{**resume,'kind':'transcript'}]})
    store.begin_submit(aid)
    directory=store.root/'screenshots';directory.mkdir()
    name=aid+'-after.jpg';(directory/name).write_bytes(b'Synthetic screenshot bytes')
    store.finish(aid,'unknown',screenshot=name)
    withdraw_transcript(store)
    archive=tmp_path/'withdrawn-history.zip';create_backup(store,archive)
    restored_root=tmp_path/'withdrawn-restored';restore_backup(archive,restored_root)
    restored=Store(restored_root)
    try:
        assert not restored.db.execute("SELECT 1 FROM documents WHERE kind='transcript'").fetchone()
        assert (restored.root/'documents'/resume['filename']).is_file()
        assert (restored.root/'screenshots'/name).is_file()
        assert restored.db.execute('SELECT state FROM applications').fetchone()[0]=='unknown'
    finally:restored.close()
    (directory/name).unlink()
    with pytest.raises(ValueError,match='screenshot is missing'):create_backup(store,tmp_path/'missing-shot.zip')
    assert not (tmp_path/'missing-shot.zip').exists()


def test_backup_rejects_referenced_document_types_the_archive_cannot_preserve(store,tmp_path):
    import hashlib
    data=b'Synthetic unsupported upload';h=hashlib.sha256(data).hexdigest()
    (store.root/'documents'/(h+'.docx')).write_bytes(data)
    store.db.execute("UPDATE documents SET hash=?,filename=? WHERE kind='resume'",(h,h+'.docx'))
    with pytest.raises(ValueError,match='Invalid document reference'):create_backup(store,tmp_path/'unsupported-document.zip')
    assert not (tmp_path/'unsupported-document.zip').exists()


def test_backup_preserves_unreadable_legacy_package_text_for_recovery(store,job,package,tmp_path):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'confirmed')
    raw='Synthetic unreadable legacy evidence text'
    store.db.execute('UPDATE applications SET package=? WHERE id=?',(raw,aid))
    archive=tmp_path/'legacy-recovery.zip';create_backup(store,archive)
    restored_root=tmp_path/'legacy-restored';restore_backup(archive,restored_root)
    restored=Store(restored_root)
    try:assert restored.db.execute('SELECT package FROM applications WHERE id=?',(aid,)).fetchone()[0]==raw
    finally:restored.close()


@pytest.mark.parametrize('manifest', [
    'null', '[]', '{"version":true,"files":{}}', '{"version":1.0,"files":{}}',
    '{"version":1,"files":[]}', '{"version":1,"files":{"ledger.sqlite3":null}}',
    '{"version":1,"files":{"ledger.sqlite3":{"bytes":true,"sha256":"'+'a'*64+'"}}}',
    '{"version":1,"files":{"ledger.sqlite3":{"bytes":-1,"sha256":"'+'a'*64+'"}}}',
    '{"version":1,"files":{"ledger.sqlite3":{"bytes":1,"sha256":"invalid"}}}',
    '{"version":1,"version":1,"files":{}}',
    '{"version":1,"files":{},"files":{}}',
    '{"version":1,"files":{"ledger.sqlite3":{"bytes":1,"bytes":2,"sha256":"'+'a'*64+'"}}}',
    '['*1500 + '0' + ']'*1500,
    b'\xff',
])
def test_malformed_manifest_is_rejected_cleanly_before_exposing_a_restore(tmp_path, manifest):
    archive = tmp_path / 'malformed.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('manifest.json', manifest)
        output.writestr('ledger.sqlite3', b'not a ledger')
    with pytest.raises(ValueError, match='manifest'):
        restore_backup(archive, tmp_path / 'destination')
    assert not (tmp_path / 'destination').exists()
    assert not list(tmp_path.glob('.restore-*'))


@pytest.mark.parametrize('invalid_zip', ['encrypted', 'unsupported-compression'])
def test_zip_format_errors_are_actionable_and_leave_no_restore(store, tmp_path, invalid_zip):
    import struct
    archive = tmp_path / 'original.zip'
    create_backup(store, archive)
    data = bytearray(archive.read_bytes())
    # Set the format fields on every local/central header while preserving
    # manifest bytes. No actual credentials or encrypted owner data are used.
    with zipfile.ZipFile(archive) as source:
        local_offsets = [entry.header_offset for entry in source.infolist()]
    central_offsets = []
    offset = data.find(b'PK\x01\x02')
    while offset != -1:
        central_offsets.append(offset)
        lengths = struct.unpack_from('<HHH', data, offset + 28)
        offset += 46 + sum(lengths)
        if data[offset:offset+4] != b'PK\x01\x02': break
    for offset in local_offsets:
        struct.pack_into('<H', data, offset + (6 if invalid_zip == 'encrypted' else 8), 1 if invalid_zip == 'encrypted' else 99)
    for offset in central_offsets:
        struct.pack_into('<H', data, offset + (8 if invalid_zip == 'encrypted' else 10), 1 if invalid_zip == 'encrypted' else 99)
    bad = tmp_path / 'unsupported.zip'; bad.write_bytes(data)
    with pytest.raises(ValueError, match='Encrypted ZIP|Cannot read this backup ZIP'):
        restore_backup(bad, tmp_path / 'destination')
    assert not (tmp_path / 'destination').exists()
    assert not list(tmp_path.glob('.restore-*'))


def test_cli_reports_invalid_backup_without_initializing_destination(tmp_path, capsys):
    from hireme.cli import main
    archive = tmp_path / 'wrong-file.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('manifest.json', 'null')
    destination = tmp_path / 'new-private'
    assert main(['--data-dir', str(destination), 'restore', str(archive)]) == 2
    result = capsys.readouterr()
    assert 'Unsupported backup manifest' in result.err and not result.out
    assert not destination.exists() and not list(tmp_path.glob('.restore-*'))


def test_large_ledger_manifest_allowed_but_document_limit_remains():
    from hireme.backup import _manifest
    info={'bytes':823656448,'sha256':'a'*64}
    assert _manifest(json.dumps({'version':1,'files':{'ledger.sqlite3':info}}))['files']['ledger.sqlite3']==info
    with pytest.raises(ValueError):_manifest(json.dumps({'version':1,'files':{'documents/'+'a'*64+'.pdf':info}}))

def test_ledger_restore_streams_beyond_ordinary_member_limit(store,tmp_path,monkeypatch):
    from hireme import backup
    archive=tmp_path/'streamed.zip';backup.create_backup(store,archive)
    monkeypatch.setattr(backup,'MAX_MEMBER',1024)
    original=zipfile.ZipExtFile.read;calls=[]
    def bounded(self,n=-1):
        if self.name=='ledger.sqlite3':
            assert n==64*1024;calls.append(n)
        return original(self,n)
    monkeypatch.setattr(zipfile.ZipExtFile,'read',bounded)
    restored=tmp_path/'streamed-restore';backup.restore_backup(archive,restored)
    assert len(calls)>1 and (restored/'ledger.sqlite3').stat().st_size>1024


def test_backup_streams_ledger_on_private_disk_and_cleans_staging(store,tmp_path,monkeypatch):
    import tempfile
    original=tempfile.TemporaryDirectory
    staging=[]
    def directory(*args,**kwargs):
        assert kwargs.get('dir')==store.root/'tmp'
        result=original(*args,**kwargs);staging.append(Path(result.name));return result
    original_read=Path.read_bytes
    def read(path):
        if path.name=='ledger.sqlite3':raise AssertionError('The ledger must be streamed, not loaded into RAM')
        return original_read(path)
    with monkeypatch.context() as patch:
        patch.setattr('hireme.backup.tempfile.TemporaryDirectory',directory)
        patch.setattr(Path,'read_bytes',read)
        create_backup(store,tmp_path/'streamed.zip')
    assert staging and all(not path.exists() for path in staging)
    assert (store.root/'tmp').stat().st_mode & 0o777==0o700
    restore_backup(tmp_path/'streamed.zip',tmp_path/'restored-stream')
    restored=Store(tmp_path/'restored-stream')
    try:
        assert restored.facts()==store.facts()
        assert not restored.settings()['live_enabled']
    finally:restored.close()


def test_backup_refuses_unrestorable_size_without_publishing_archive(store,tmp_path,monkeypatch):
    monkeypatch.setattr('hireme.backup.MAX_LEDGER',1)
    with pytest.raises(ValueError,match='supported backup size'):
        create_backup(store,tmp_path/'too-large.zip')
    assert not (tmp_path/'too-large.zip').exists()
    assert not list((store.root/'tmp').iterdir())
    assert not list(tmp_path.glob('.backup-*'))


def test_backup_cannot_publish_a_manifest_the_restore_would_reject(store,tmp_path,monkeypatch):
    monkeypatch.setattr('hireme.backup.MAX_MANIFEST',1)
    with pytest.raises(ValueError,match='manifest exceeds'):
        create_backup(store,tmp_path/'manifest-too-large.zip')
    assert not (tmp_path/'manifest-too-large.zip').exists()
    assert not list((store.root/'tmp').iterdir())


def test_backup_and_restore_hash_document_references_without_loading_files_into_ram(store,tmp_path,monkeypatch):
    source=import_material(store,b'Synthetic writing context.','sample.txt','context')
    review_material(store,source['id'],source['text'],'personal',True)
    original=Path.read_bytes
    def bounded(path):
        if path.parent.name in {'documents','materials'}:
            raise AssertionError('Document reference checks must stream file contents')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',bounded)
    archive=tmp_path/'bounded-documents.zip'
    create_backup(store,archive)
    restore_backup(archive,tmp_path/'bounded-restored')
    assert (tmp_path/'bounded-restored'/'ledger.sqlite3').is_file()


def test_backup_is_not_published_if_its_contents_cannot_be_synced(store,tmp_path,monkeypatch):
    import os
    original=os.fsync;attempted=[]
    def fail_archive(descriptor):
        inode=os.fstat(descriptor).st_ino
        if any(path.stat().st_ino==inode for path in tmp_path.glob('.backup-*')):
            attempted.append(inode)
            raise OSError('Synthetic disk synchronization failure')
        return original(descriptor)
    monkeypatch.setattr('hireme.backup.os.fsync',fail_archive)
    archive=tmp_path/'unsynced.zip'
    with pytest.raises(OSError,match='synchronization'):
        create_backup(store,archive)
    assert attempted
    assert not archive.exists() and not list(tmp_path.glob('.backup-*'))
    assert not list((store.root/'tmp').iterdir())
