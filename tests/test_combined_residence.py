"""Compound residence questions must retain both city and state."""
import pytest

from hireme.answers import field_key, resolve, validate_package
from hireme.util import Blocked, digest


@pytest.mark.parametrize('label', [
    'If located in the US, in what city and state do you reside?',
    'Please enter the city and state where you currently live.',
    'Current city/state*',
    'City, province',
])
def test_combined_residence_uses_full_confirmed_location(store, job, label):
    store.put_facts({'location': 'Seattle, WA', 'state': 'WA'})
    field = {'label': label, 'type': 'text', 'options': [], 'required': True, 'maxlength': 255, 'value': ''}
    assert field_key(label) == 'location'
    answer = resolve(store, job['host'], field, context=job)
    assert answer['value'] == 'Seattle, WA'
    assert answer['provenance'] == {'fact_key': 'location', 'revision': store.facts()['location']['revision']}


def test_state_only_and_preferred_office_are_separate_concepts():
    assert field_key('Please indicate the state in which you currently reside.') == 'state'
    assert field_key('Which city and state would you prefer to work in?') is None


def test_combined_residence_does_not_use_only_state(store, job):
    store.db.execute("DELETE FROM facts WHERE key='location'")
    store.put_facts({'state': 'WA'})
    field = {'label': 'In what city and state do you reside?', 'type': 'text', 'options': [], 'required': True, 'maxlength': 255, 'value': ''}
    with pytest.raises(Blocked, match='missing_fact'):
        resolve(store, job['host'], field, context=job)


def test_previously_prepared_state_only_answer_is_rejected(store, job, package):
    store.put_facts({'state': 'CA'})
    field = {'label': 'In what city and state do you reside?', 'type': 'text', 'options': [], 'required': True, 'maxlength': 255, 'value': 'CA'}
    package['answers'].append({'field': field, 'value': 'CA', 'provenance': {'fact_key': 'state', 'revision': store.facts()['state']['revision']}})
    package['steps'][0]['fields'].append(field)
    package['facts_hash'] = digest(store.facts())
    with pytest.raises(Blocked): validate_package(store, job, package)
