import pytest

from hireme.backup import create_backup, restore_backup
from hireme.job_notes import NoteConflict, get_note, save_note
from hireme.store import Store


def save(store, job, body, revision=0):
    return save_note(store, {'job_id': job['id'], 'body': body, 'revision': revision})


def test_private_notes_do_not_change_application_sources_or_outcomes(store, job, package):
    aid = store.prepare(job, package)
    store.begin_submit(aid); store.finish(aid, 'unknown')
    before = store.snapshot(); changes = store.db.total_changes
    assert get_note(store, job['id']) == {'job_id': job['id'], 'body': '', 'revision': 0, 'updated': None}
    assert store.db.total_changes == changes
    result = save(store, job, 'Ask about the team.\n<script>literal private text</script>')
    assert result['changed'] and result['note']['revision'] == 1
    assert get_note(store, job['id']) == result['note']
    assert store.snapshot() == before
    event = dict(store.db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 1').fetchone())
    assert 'private text' not in str(event) and event['kind'] == 'opportunity_note_saved'
    assert not store.db.execute('SELECT * FROM model_requests').fetchone()
    assert save(store, job, result['note']['body']) == {'changed': False, 'note': result['note']}


def test_conflicts_and_cleared_revision_protect_notes_from_stale_tabs(store, job):
    save(store, job, 'Original note')
    updated = save(store, job, 'Saved from another tab', 1)['note']
    with pytest.raises(NoteConflict, match='another tab'):
        save(store, job, 'Stale replacement', 1)
    assert get_note(store, job['id']) == updated
    cleared = save(store, job, '', 2)['note']
    assert cleared['body'] == '' and cleared['revision'] == 3
    with pytest.raises(NoteConflict): save(store, job, 'Stale recreation', 0)
    with pytest.raises(NoteConflict): save(store, job, 'Stale recreation', 2)
    assert save(store, job, ' \n\t', 2) == {'note': cleared, 'changed': False}
    assert save(store, job, 'A fresh note', 3)['note']['revision'] == 4


def test_empty_note_is_read_only_and_exact_length_is_supported(store, job):
    changes = store.db.total_changes
    assert not save(store, job, ' \n ')['changed']
    assert store.db.total_changes == changes
    assert save(store, job, 'é' * 4000)['note']['body'] == 'é' * 4000


def test_event_failure_rolls_back_the_note_and_revision(store, job, monkeypatch):
    original = save(store, job, 'Original')['note']
    def fail(*args, **kwargs): raise RuntimeError('Synthetic event failure')
    monkeypatch.setattr(store, 'event', fail)
    with pytest.raises(RuntimeError): save(store, job, 'Should roll back', 1)
    assert get_note(store, job['id']) == original


def test_notes_travel_in_private_backup_and_existing_ledgers_upgrade(store, job, tmp_path):
    original = save(store, job, 'Private migration reminder')['note']
    archive = tmp_path / 'history.zip'; create_backup(store, archive)
    root = tmp_path / 'restored'; restore_backup(archive, root)
    restored = Store(root)
    try:
        assert get_note(restored, job['id']) == original
        assert not restored.settings()['live_enabled']
        restored.db.execute('DROP TABLE job_notes')
    finally: restored.close()
    upgraded = Store(root)
    try: assert get_note(upgraded, job['id'])['revision'] == 0
    finally: upgraded.close()


@pytest.mark.parametrize('changes', [
    {'body': None}, {'body': []}, {'body': 'x' * 4001}, {'body': 'bad\0text'},
    {'revision': None}, {'revision': True}, {'revision': -1}, {'revision': 1.5},
    {'revision': '0'}, {'revision': 2147483648}, {'job_id': ''}, {'job_id': []},
    {'job_id': '../unknown'}, {'job_id': 'x' * 129},
])
def test_invalid_notes_leave_all_existing_data_unchanged(store, job, changes):
    original = save(store, job, 'Original')['note']
    before = store.snapshot(); event_count = store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    with pytest.raises(ValueError):
        save_note(store, {'job_id': job['id'], 'body': 'Replacement', 'revision': 1, **changes})
    assert get_note(store, job['id']) == original and store.snapshot() == before
    assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == event_count


@pytest.mark.parametrize('data', [None, [], 'invalid'])
def test_invalid_note_envelopes_are_rejected(store, data):
    with pytest.raises(ValueError): save_note(store, data)
