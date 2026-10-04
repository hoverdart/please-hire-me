import io
import json
import zipfile

import pytest

from hireme.backup import create_backup
from hireme.backup_inspection import inspect_backup, inspect_upload, MAX_UPLOAD


def test_check_restores_complete_history_without_changing_active_workspace(store, job, package, tmp_path):
    aid = store.prepare(job, package)
    store.begin_submit(aid)
    store.finish(aid, 'unknown')
    store.db.execute("INSERT INTO employer_accounts VALUES(?,?,?,?,?)", ('synthetic', 'https://careers.invalid', 'Synthetic', 'uncertain', '2026-01-01'))
    for index in range(620):
        store.db.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,0)', (str(index), job['id'], job['host'], 'Synthetic question', '[]', 'Fixture'))
    archive = tmp_path / 'history.zip'
    create_backup(store, archive)
    before = store.snapshot()
    changes = store.db.total_changes
    result = inspect_upload(io.BytesIO(archive.read_bytes()), archive.stat().st_size, store.root)
    assert result['owner'] == {'full_name': 'Test Person', 'email': 'test@candidate.invalid'}
    assert result['counts']['unanswered_questions'] == 620
    assert result['counts']['outcomes_to_review'] == 1
    assert result['counts']['accounts_to_review'] == 1
    assert result['counts']['pdf_files'] == 1
    assert result['restores_paused'] and not result['credentials_included']
    assert store.snapshot() == before and store.db.total_changes == changes
    assert store.settings()['live_enabled']
    assert not list(store.root.glob('.backup-check-*'))


def test_check_does_not_present_unconfirmed_identity_as_owner(store, tmp_path):
    store.db.execute("UPDATE facts SET confirmed=0 WHERE key IN ('email','full_name')")
    archive = tmp_path / 'history.zip'
    create_backup(store, archive)
    assert inspect_backup(archive, store.root)['owner'] == {}
    assert not list(store.root.glob('.backup-check-*'))


@pytest.mark.parametrize('corruption', ['checksum', 'historical-reference', 'invalid-zip'])
def test_check_rejects_damaged_backups_and_removes_temporary_copies(store, job, package, tmp_path, corruption):
    store.prepare(job, package)
    archive = tmp_path / 'history.zip'
    create_backup(store, archive)
    bad = tmp_path / 'bad.zip'
    if corruption == 'invalid-zip':
        bad.write_bytes(b'Not a backup')
    else:
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(bad, 'w') as target:
            manifest = json.loads(source.read('manifest.json'))
            missing = 'documents/' + package['documents'][0]['filename']
            if corruption == 'historical-reference': manifest['files'].pop(missing)
            for name in source.namelist():
                if corruption == 'historical-reference' and name == missing: continue
                data = source.read(name)
                if name == 'manifest.json': data = json.dumps(manifest)
                if corruption == 'checksum' and name == 'ledger.sqlite3': data = b'changed'
                target.writestr(name, data)
    before = store.snapshot()
    with pytest.raises(ValueError): inspect_upload(io.BytesIO(bad.read_bytes()), bad.stat().st_size, store.root)
    assert store.snapshot() == before
    assert not list(store.root.glob('.backup-check-*'))


def test_upload_is_bounded_and_rejects_truncated_or_oversized_bodies(store, tmp_path):
    archive = tmp_path / 'history.zip'
    create_backup(store, archive)
    class BoundedStream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 65536
            return super().read(size)
    payload = archive.read_bytes()
    inspect_upload(BoundedStream(payload), len(payload), store.root)
    with pytest.raises(ValueError, match='interrupted'):
        inspect_upload(BoundedStream(payload), len(payload) + 1, store.root)
    with pytest.raises(ValueError, match='1 GiB'):
        inspect_upload(BoundedStream(payload), MAX_UPLOAD + 1, store.root)
    assert not list(store.root.glob('.backup-check-*'))
