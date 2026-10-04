import pytest

from hireme.answers import resolve, validate_package
from hireme.materials import import_material, review_material
from hireme.util import Blocked


def test_edit_approved_template_invalidates_old_package_and_prepared_draft(store, job, package):
    tid = store.put_template('project', 'I built a Python service and tested its deployment.')
    field = {'label': 'Describe a project you built', 'type': 'textarea', 'required': True, 'options': [], 'maxlength': -1}
    package['answers'] = [resolve(store, job['host'], field, context=job)]; package['steps'] = []
    store.prepare(job, package)
    store.edit_template(tid, 'project', 'I built a TypeScript service and tested its deployment.')
    assert store.templates()[0]['revision'] == 2
    assert not store.db.execute("SELECT * FROM applications WHERE state='prepared'").fetchone()
    with pytest.raises(Blocked): validate_package(store, job, package)


def test_revocation_removes_source_without_erasing_submission_history(store, job, package):
    tid = store.put_template('experience', 'I built a Python service and tested its deployment.')
    application = store.prepare(job, package); store.begin_submit(application)
    store.finish(application, 'unknown', 'Held outcome')
    store.revoke_template(tid)
    assert not store.templates()
    assert store.db.execute('SELECT state FROM applications WHERE id=?', (application,)).fetchone()[0] == 'unknown'
    assert store.settings()['live_enabled']


def test_imported_source_cannot_be_edited_or_revoked_outside_its_review_flow(store):
    source = import_material(store, b'I built a Python service and tested its deployment.', 'work.txt', 'context')
    review_material(store, source['id'], source['text'], 'personal', True)
    tid = 'material:' + source['id']
    with pytest.raises(ValueError, match='Writing & context'):
        store.edit_template(tid, 'experience', 'I changed the claims without reviewing their source.')
    with pytest.raises(ValueError, match='Writing & context'): store.revoke_template(tid)
    assert store.templates()[0]['body'] == source['text']


def test_invalid_template_edits_do_not_change_existing_text_or_drafts(store, job, package):
    tid = store.put_template('project', 'I built a Python service and tested its deployment.')
    application = store.prepare(job, package)
    with pytest.raises(ValueError): store.edit_template(tid, 'project', 'short')
    assert store.templates()[0]['revision'] == 1
    assert store.db.execute('SELECT * FROM applications WHERE id=?', (application,)).fetchone()
    with pytest.raises(ValueError): store.revoke_template('unknown-template')
