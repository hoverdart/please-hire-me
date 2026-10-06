from pathlib import Path
from hireme.reports import queue_report,flush_reports
from hireme.worker import cycle
from hireme.util import Blocked


def finished(store):
    store.db.execute("INSERT INTO runs VALUES('run-one','2026-01-01T00:00:00Z','2026-01-01T00:00:05Z','finished',0,'{}')")
    queue_report(store,'run-one')


def test_each_batch_has_one_durable_report_even_when_disabled(store):
    finished(store);queue_report(store,'run-one')
    assert store.db.execute('SELECT count(*) FROM report_outbox').fetchone()[0]==1
    assert store.db.execute('SELECT state FROM report_outbox').fetchone()[0]=='disabled'


def test_mail_setup_failure_keeps_report_pending_without_application_retry(store):
    store.update_settings({'gmail_reports':True});finished(store)
    def missing(s):raise Blocked('gmail_not_connected')
    assert flush_reports(store,missing)['sent']==0
    assert store.db.execute('SELECT state FROM report_outbox').fetchone()[0]=='pending'
    assert not store.db.execute('SELECT * FROM applications').fetchone()


def test_ambiguous_mail_send_is_not_automatically_repeated(store):
    store.update_settings({'gmail_reports':True});finished(store)
    class Client:
        email='test@candidate.invalid';calls=0
        def __init__(self,s):pass
        def send_report(self,*args):Client.calls+=1;raise TimeoutError()
    flush_reports(store,Client);flush_reports(store,Client)
    assert Client.calls==1 and store.db.execute('SELECT state FROM report_outbox').fetchone()[0]=='uncertain'


def test_paused_cycle_still_records_batch_summary(store):
    store.update_settings({'live_enabled':False})
    import pytest
    with pytest.raises(Blocked,match='paused'):cycle(store,Path('.'),discover=False)
    row=store.db.execute('SELECT * FROM report_outbox').fetchone()
    assert 'paused' in row['body'] and 'Confirmed submissions: 0' in row['body']


def test_batch_email_links_include_only_browser_attempts(store,job,monkeypatch):
    from hireme.util import digest
    jobs = []
    for outcome in ('confirmed', 'blocked', 'screened'):
        url = job['url'] + '-' + outcome
        item = {**job, 'id': digest(url), 'url': url, 'company': outcome}
        store.upsert_job(item)
        jobs.append(item)

    def screen(item,*args):
        if item['company'] == 'screened':
            raise Blocked('location_mismatch', 'Review the location before applying')
        return 1, []

    class Browser:
        def __init__(self,s):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def apply(self,item,live=True):
            if item['company'] == 'blocked':
                raise Blocked('captcha', 'Complete the employer CAPTCHA')
            return 'confirmed'

    monkeypatch.setattr('hireme.worker.eligible',screen)
    cycle(store,Path('.'),discover=False,browser_factory=Browser,job_ids=[j['id'] for j in jobs])
    body = store.db.execute('SELECT body FROM report_outbox').fetchone()[0]
    for item in jobs[:2]:
        assert body.count(item['url']) == 1
    assert jobs[2]['url'] not in body
    assert 'Applied successfully' in body
    assert 'Blocked — review or apply manually' in body
    assert 'location_mismatch' not in body and 'Complete the employer CAPTCHA' in body
    assert 'choose Applied manually' in body
    assert 'check with the employer before applying again' in body
    assert store.db.execute('SELECT count(*) FROM applications').fetchone()[0] == 0


def test_report_does_not_include_other_runs_job_links(store,job):
    store.event('job_screening_blocked',job['id'],{'run_id':'another-run','outcome':'blocked','reason':'location_mismatch'})
    finished(store)
    body = store.db.execute('SELECT body FROM report_outbox').fetchone()[0]
    assert job['url'] not in body


def test_blocked_questions_are_listed_one_per_line_with_reasons(store,job):
    compound=('Do you have:\na) any Personal/Familial Relationships (current Acme employees); b) any Outside Business Activities '
              'that you wish to continue; c) any investment that is greater than 5% of the outstanding shares of a publicly-traded company?*')
    essay='Tell us about a technical project you are most proud of. What did you build, and what impact did it have?'
    store.ask(job['id'],job['host'],compound,['Yes','No'],'missing_fact')
    store.ask(job['id'],job['host'],essay,[],'writing_unsupported')
    store.event('writing_reviewed',job['id'],{'question':essay,'supported':False,'reason':'The impact metric is not in any cited source.'})
    store.event('application_finished',job['id'],{'run_id':'run-one','outcome':'blocked','reason':'missing_answers','detail':compound+'; '+essay})
    finished(store)
    body=store.db.execute('SELECT body FROM report_outbox').fetchone()[0]
    lines=[line for line in body.splitlines() if line.startswith('  • ')]
    assert len(lines)==2
    assert lines[0].startswith('  • Do you have: a) any Personal/Familial') and '…' in lines[0] and lines[0].endswith('A personal answer is missing')
    assert 'did not pass its source check (review: The impact metric is not in any cited source.)' in lines[1]
    assert '; Tell us' not in body


def test_pending_batches_are_sent_as_one_email(store):
    store.update_settings({'gmail_reports':True})
    for i,(mode,submitted) in enumerate((('prepare',0),('prepare',0),('live',2))):
        store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(f'run-{i}',f'2026-01-01T0{i}:00:00Z',f'2026-01-01T0{i}:05:00Z','finished',submitted,'{"mode":"%s"}'%mode))
        queue_report(store,f'run-{i}')
    class Client:
        email='test@candidate.invalid';sent=[]
        def __init__(self,s):pass
        def send_report(self,subject,body,message_id):Client.sent.append((subject,body));return 'one'
    assert flush_reports(store,Client)=={'sent':1,'enabled':True,'batches':3}
    (subject,body),=Client.sent
    assert subject=='Application batches: 2 confirmed across 3 batches'
    assert all(f'Application batch run-{i}' in body for i in range(3))
    assert body.count('Open Application desk on the Pi')==1
    assert {r[0] for r in store.db.execute('SELECT state FROM report_outbox')}=={'sent'}
    assert flush_reports(store,Client)['sent']==0 and len(Client.sent)==1


def test_failed_combined_send_marks_every_batch_uncertain(store):
    store.update_settings({'gmail_reports':True})
    for i in range(2):
        store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(f'run-{i}',f'2026-01-01T0{i}:00:00Z',f'2026-01-01T0{i}:05:00Z','finished',0,'{}'))
        queue_report(store,f'run-{i}')
    class Client:
        email='test@candidate.invalid';calls=0
        def __init__(self,s):pass
        def send_report(self,*args):Client.calls+=1;raise TimeoutError()
    flush_reports(store,Client);flush_reports(store,Client)
    assert Client.calls==1
    assert {r[0] for r in store.db.execute('SELECT state FROM report_outbox')}=={'uncertain'}
