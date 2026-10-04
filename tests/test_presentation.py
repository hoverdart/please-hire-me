from hireme.presentation import job_display
from hireme.ledger import summary, export_csv


def test_eligibility_and_limits_do_not_become_user_tasks(store, job):
    for reason, display in [('location_mismatch', 'not_match'), ('company_same_day: already sent', 'waiting')]:
        store.block(job['id'], *reason.split(': ', 1))
        row = dict(store.db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())
        shown = job_display(row)
        assert shown['display_status'] == display and not shown['requires_attention']
        assert shown['reason_label'] and shown['next_step']
        assert summary(store)['attention_count'] == 0
        assert row['status'] == 'blocked'  # Presentation does not mutate application policy.
        assert shown['status_label'].encode() in export_csv(store)


def test_actionable_or_unrecognized_holds_remain_in_attention(store, job):
    for reason in ['captcha_blocked', 'missing_fact: choose an answer', 'new_unknown_reason']:
        store.block(job['id'], *reason.split(': ', 1))
        row = dict(store.db.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())
        shown = job_display(row)
        assert shown['requires_attention'] and shown['display_status'] == 'blocked'
        assert shown['reason_label'] and shown['next_step']
        assert summary(store)['attention_count'] == 1


def test_unanswered_question_still_counts_even_if_job_is_outside_preferences(store, job):
    store.block(job['id'], 'location_mismatch')
    store.ask(job['id'], job['host'], 'Your answer?', [])
    assert summary(store)['attention_count'] == 1


def test_uncertain_outcomes_are_always_visible_regardless_of_reason():
    shown = job_display({'status': 'unknown', 'reason': 'location_mismatch'})
    assert shown['requires_attention'] and shown['status_label'] == 'Uncertain'
