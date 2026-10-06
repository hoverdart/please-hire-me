"""Confirmed facts reach differently worded employer choices without new claims."""
import pytest

from hireme.answers import resolve, validate_package
from hireme.provider import ClaudeProvider
from hireme.util import Blocked

SPONSORSHIP = ['No, I will not require sponsorship now or in the future to work in this country',
               'Yes, I will require sponsorship now or in the future to work in this country']
LABEL = 'Will you now or in the future require employer sponsorship for work authorization in this country?*'


def field(label, options, kind='select', required=True):
    return {'label': label, 'type': kind, 'required': required, 'options': options, 'maxlength': -1}


class Translator:
    def __init__(self, choice=SPONSORSHIP[0]): self.choice = choice; self.calls = 0
    def map_option(self, field, fact):
        self.calls += 1
        assert fact == {'key': 'needs_sponsorship', 'label': fact['label'], 'value': 'No'}
        return self.choice


def test_reviewed_translation_is_cached_and_replayed_without_a_model(store, job, package):
    model = Translator()
    answer = resolve(store, job['host'], field(LABEL, SPONSORSHIP), model, context=job)
    assert answer['value'] == SPONSORSHIP[0]
    assert answer['provenance']['fact_key'] == 'needs_sponsorship' and answer['provenance']['option_mapping'] is True
    assert resolve(store, 'boards.greenhouse.io|other', field(LABEL, SPONSORSHIP), model, context=job)['value'] == SPONSORSHIP[0]
    assert model.calls == 1
    package['answers'] = [answer]; package['steps'] = []
    validate_package(store, job, package)


def test_fact_edit_or_reworded_choices_require_a_new_translation(store, job):
    model = Translator()
    resolve(store, job['host'], field(LABEL, SPONSORSHIP), model, context=job)
    resolve(store, job['host'], field(LABEL, SPONSORSHIP + ['Prefer not to say']), model, context=job)
    assert model.calls == 2
    store.put_facts({'needs_sponsorship': 'Yes'})
    with pytest.raises(AssertionError):resolve(store, job['host'], field(LABEL, SPONSORSHIP), model, context=job)


@pytest.mark.parametrize('choice', [None, 'Maybe'])
def test_declined_or_unlisted_translation_still_needs_review(store, job, choice):
    with pytest.raises(Blocked, match='option_mismatch'):
        resolve(store, job['host'], field(LABEL, SPONSORSHIP), Translator(choice), context=job)
    assert not store.db.execute('SELECT 1 FROM option_mappings').fetchone()


def test_optional_and_identity_fields_are_not_translated(store, job):
    model = Translator()
    with pytest.raises(Blocked):resolve(store, job['host'], field(LABEL, SPONSORSHIP, required=False), model, context=job)
    with pytest.raises(Blocked):resolve(store, job['host'], field('Email', ['Personal', 'Work']), model, context=job)
    assert model.calls == 0


def test_translation_requires_a_supported_review():
    provider = ClaudeProvider.__new__(ClaudeProvider); provider.observer = None
    replies = iter([{'option': 'East Asian'}, {'supported': False, 'reason': 'More specific than Asian'}])
    provider.request = lambda *args: next(replies)
    fact = {'key': 'race', 'label': 'Race', 'value': 'Asian'}
    assert provider.map_option(field('Race*', ['East Asian', 'South Asian']), fact) is None
    replies = iter([{'option': 'Man'}, {'supported': True, 'reason': 'Same'}])
    assert provider.map_option(field('Gender*', ['Man', 'Woman']), {**fact, 'key': 'gender', 'value': 'Male'}) == 'Man'


@pytest.mark.parametrize('label,options,expected', [
    ('When do you graduate?*', ['Sept - Dec 2027', 'Jan - April 2028', 'May - Aug 2028', 'Other'], 'May - Aug 2028'),
    ('Please re-confirm your expected graduation date*', ['August 2027 - December 2027', 'January 2028 - July 2028'], 'January 2028 - July 2028'),
    ('What is your anticipated graduation month/year?*', ['December 2027', 'May 2028', 'December 2028'], 'May 2028'),
])
def test_graduation_choices_use_the_confirmed_date(store, job, label, options, expected):
    assert resolve(store, job['host'], field(label, options), context=job)['value'] == expected


def test_overlapping_graduation_ranges_remain_ambiguous(store, job):
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('When do you graduate?*', ['Jan - June 2028', 'May - Aug 2028']), context=job)


def test_essay_mentioning_learning_is_not_a_referral_source_question(store, job):
    label = ('Tell us about a robotics project that completely failed on the first try. What failed, '
             'how did you debug it, and what did you learn from the recovery process?')
    with pytest.raises(Blocked):
        resolve(store, 'jobs.ashbyhq.com|acme', field(label, [], 'textarea'), context={**job, 'source': 'ash:acme'})
    answer = resolve(store, 'jobs.ashbyhq.com|acme', field('How did you hear about Acme?', [], 'text'), context={**job, 'source': 'ash:acme'})
    assert answer['value'] == 'Your Ashby careers page.'


ROBINHOOD_CONFLICTS = ('Do you have:\na) any Personal/Familial Relationships (current Robinhood employees or employees of Robinhood’s vendors); '
                       'b) any Outside Business Activities that you wish to continue; c) any investment that is greater than 5% of the '
                       'outstanding shares of a publicly-traded company?*')
ROBINHOOD_OFFICIALS = ('Robinhood adheres to applicable laws and regulations in relation to government officials given inherent bribery '
                       'and/or corruption risk. a) Do you currently hold or have you held, within the last 5 years, a position as a '
                       'government official? b) Have you been referred or recommended for this position by a government official?*')
DEMOGRAPHIC_CONSENT = ('By checking this box, I consent to Robinhood collecting, storing, and processing my responses to the '
                       'demographic data surveys above.*')


@pytest.mark.parametrize('label,key,kind', [
    (ROBINHOOD_CONFLICTS, 'conflict_disclosures', 'select'),
    (ROBINHOOD_OFFICIALS, 'government_official', 'select'),
    (DEMOGRAPHIC_CONSENT, 'demographic_data_consent', 'checkbox'),
])
def test_recurring_screening_questions_use_one_confirmed_fact(store, job, label, key, kind):
    with pytest.raises(Blocked, match='missing_fact'):resolve(store, job['host'], field(label, ['Yes', 'No'], kind), context=job)
    store.put_facts({key: 'No'})
    assert resolve(store, job['host'], field(label, ['Yes', 'No'], kind), context=job)['value'] == 'No'


def test_marketing_consent_is_not_demographic_consent(store, job):
    store.put_facts({'demographic_data_consent': 'Yes'})
    with pytest.raises(Blocked):
        resolve(store, job['host'], field('I consent to Acme collecting and processing my demographic data for marketing.*', ['Yes', 'No'], 'checkbox'), context=job)


def test_military_status_uses_the_specific_veteran_fact(store, job):
    store.put_facts({'veteran': 'I have never served in the military'})
    options = ['I am on active duty', 'I have never served in the military', 'I identify as a protected veteran']
    assert resolve(store, job['host'], field('What is your military status?*', options), context=job)['value'] == options[1]


def test_rejected_drafts_are_reported_as_a_retryable_writing_outcome(store, job):
    store.update_settings({'tailored_writing': True})
    store.put_template('project', 'I built a scheduling service in Python for a campus club and maintained it for a year.')
    class Model:
        def draft_answer(self, *args): return {'answer': '', 'sentence_ids': [], 'rejected': 'Impact is unsupported'}
        def choose_answer(self, *args): return None
        def match_field(self, *args): return {'fact_key': None, 'template_id': None}
        def choose_sentences(self, *args): return []
    store.put_template('project', 'I also designed a small compiler for a course project and wrote its test suite.')
    with pytest.raises(Blocked, match='writing_unsupported'):
        resolve(store, job['host'], field('Tell us about a project you are proud of and its impact.', [], 'textarea'), Model(), context=job)
    from hireme.job_holds import category
    assert category('writing_unsupported') == 'transient'


def test_draft_gets_three_reviewed_attempts_before_reporting_the_last_reason():
    provider = ClaudeProvider.__new__(ClaudeProvider); provider.observer = None
    calls = []
    def request(instruction, data, schema):
        calls.append(schema)
        return {'answer': 'Draft', 'sentence_ids': ['a']} if 'answer' in schema['properties'] else {'supported': False, 'reason': f'reason {len(calls)}'}
    provider.request = request
    assert provider.draft_answer('Q?', [{'id': 'a', 'text': 'Source.'}]) == {'answer': '', 'sentence_ids': [], 'rejected': 'reason 6'}
    assert len(calls) == 6
