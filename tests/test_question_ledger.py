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


def test_applicant_count_excludes_old_questions_and_uses_current_ats_scope(store):
    from hireme.discovery import posting
    for number, (host, status, code, field_reason) in enumerate((
        ('job-boards.greenhouse.io', 'blocked', 'missing_answers', 'missing_fact'),
        ('job-boards.greenhouse.io', 'blocked', 'missing_answers', 'provider_timeout'),
        ('job-boards.greenhouse.io', 'blocked', 'daily_limit', 'missing_fact'),
        ('job-boards.greenhouse.io', 'blocked', 'posting_changed_review', 'missing_fact'),
        ('job-boards.greenhouse.io', 'unknown', '', 'missing_fact'),
        ('jobs.lever.co', 'blocked', 'missing_answers', 'missing_fact'),
    )):
        path = f'fixture/jobs/{number}' if 'greenhouse' in host else f'fixture/req-{number}'
        application = posting(f'https://{host}/{path}', 'Fixture', f'Intern {number}', 'US', 'fixture')
        store.upsert_job(application)
        store.db.execute('UPDATE jobs SET status=?,reason=? WHERE id=?',(status,code,application['id']))
        store.ask(application['id'],host,f'Question {number}',[],field_reason)
    store.update_settings({'allowed_application_ats':['greenhouse']})
    result = summary(store)
    assert result['question_count']==result['applicant_attention_count']==1
    assert result['attention_count']>result['applicant_attention_count']
    assert [question['label'] for question in search_questions(store)['questions']]==['Question 0']
    assert store.db.execute('SELECT COUNT(*) FROM questions WHERE resolved=0').fetchone()[0]==6


@pytest.mark.parametrize('kwargs', [{'search': 'x' * 201}, {'search': None}, {'offset': True},
                                  {'offset': -1}, {'offset': 1000001}, {'limit': 0}, {'limit': 101}])
def test_question_page_validates_input(store, kwargs):
    with pytest.raises(ValueError): search_questions(store, **kwargs)
