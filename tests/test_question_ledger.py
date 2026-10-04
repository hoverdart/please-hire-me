import pytest

from hireme.question_ledger import search_questions
from hireme.ledger import summary


def test_questions_search_full_history_unicode_literal_wildcards_and_context(store, job):
    for index in range(62):
        store.ask(job['id'], job['host'], f'Question {index}', [])
    special = store.ask(job['id'], job['host'], 'Café 100%_ question', [])
    resolved = store.ask(job['id'], job['host'], 'Previously answered', [])
    store.answer_question(resolved, 'Synthetic confirmed response')
    page = search_questions(store)
    assert page['total'] == 63 and len(page['questions']) == 25
    last = search_questions(store, offset=999)
    assert last['offset'] == 50 and len(last['questions']) == 13
    assert last['questions'][-1]['id'] == special
    match = search_questions(store, search='CAFÉ 100%_')
    assert match['total'] == 1 and match['questions'][0]['company'] == 'Acme'
    assert match['questions'][0]['title'] == job['title'] and match['questions'][0]['url'] == job['url']
    assert search_questions(store, search='Acme')['total'] == 63
    assert search_questions(store, search='Previously answered')['total'] == 0
    assert len(store.snapshot(question_limit=50)['questions']) == 50
    assert len(store.snapshot()['questions']) == 63
    assert summary(store)['question_count'] == 63


def test_question_without_job_remains_reviewable(store):
    qid = store.ask('missing-job', 'fixture.invalid', 'Orphaned question', [])
    row = search_questions(store)['questions'][0]
    assert row['id'] == qid and row['company'] is None


@pytest.mark.parametrize('kwargs', [{'search': 'x' * 201}, {'search': None}, {'offset': True},
                                  {'offset': -1}, {'offset': 1000001}, {'limit': 0}, {'limit': 101}])
def test_question_page_validates_input(store, kwargs):
    with pytest.raises(ValueError): search_questions(store, **kwargs)
