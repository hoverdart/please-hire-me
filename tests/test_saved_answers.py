import json

import pytest

from hireme.answers import resolve, validate_package
from hireme.saved_answers import list_answers, revoke_answer
from hireme.util import Blocked


def test_withdrawing_answer_invalidates_preparation_but_preserves_attempt_evidence(store, job, package):
    field = package['answers'][0]['field']
    qid = store.ask(job['id'], job['host'], field['label'], [])
    store.answer_question(qid, store.facts()['email']['value'], 'email')
    package['answers'][0] = resolve(store, job['host'], field)
    assert package['answers'][0]['provenance']['answer_id'] == qid
    aid = store.prepare(job, package)
    revoke_answer(store, qid)
    assert not store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()
    assert store.saved_answer(job['host'], field['label'], []) is None
    with pytest.raises(Blocked): validate_package(store, job, package)
    assert json.loads((store.root / 'config/answers.json').read_text()) == []
    store.answer_question(qid, store.facts()['email']['value'], 'email')
    package['answers'][0] = resolve(store, job['host'], field)
    aid = store.prepare(job, package); store.begin_submit(aid); store.finish(aid, 'unknown')
    evidence = dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone())
    revoke_answer(store, qid)
    assert dict(store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()) == evidence
    with pytest.raises(Blocked): store.prepare(job, package)


def test_saved_answer_search_paginates_unicode_and_literal_wildcards(store, job):
    for index in range(30):
        qid = store.ask(job['id'], job['host'], f'Fixture question {index}', [])
        store.answer_question(qid, 'Synthetic answer')
    qid = store.ask(job['id'], job['host'], 'Café 100%_ question', [])
    store.answer_question(qid, 'Special synthetic answer')
    page = list_answers(store)
    assert page['total'] == 31 and len(page['answers']) == 25
    second = list_answers(store, offset=25)
    assert len(second['answers']) == 6
    assert not {row['id'] for row in page['answers']} & {row['id'] for row in second['answers']}
    assert list_answers(store, search='CAFÉ 100%_')['total'] == 1
    assert list_answers(store, search='special synthetic')['answers'][0]['id'] == qid
    assert list_answers(store, offset=1000)['offset'] == 25


@pytest.mark.parametrize('arguments', [{'search': 'x' * 201}, {'offset': -1}, {'offset': True}, {'limit': 0}])
def test_saved_answer_search_rejects_unbounded_inputs(store, arguments):
    with pytest.raises(ValueError): list_answers(store, **arguments)


def test_withdrawing_missing_answer_does_not_clear_prepared_work(store, job, package):
    aid = store.prepare(job, package)
    with pytest.raises(ValueError): revoke_answer(store, 'missing')
    assert store.db.execute('SELECT * FROM applications WHERE id=?', (aid,)).fetchone()
