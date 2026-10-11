"""College enrollment dates do not establish high-school graduation dates."""
import pytest
from hireme.answers import field_key, resolve, validate_package
from hireme.config import validate_fact
from hireme.util import Blocked, digest


def year_field():
    return {'label': 'What year did you graduate high school?', 'type': 'select', 'options': ['2023', '2024', '2025'], 'required': True, 'maxlength': -1, 'value': ''}


def test_missing_high_school_year_requires_a_confirmed_fact_even_with_a_model(store, job):
    store.put_facts({'college_start': '2025-08', 'high_school': 'Fixture High School'})
    class NoInference:
        def __getattr__(self, name): raise AssertionError('A model must not infer this historical date')
    assert field_key(year_field()['label']) == 'high_school_graduation_year'
    with pytest.raises(Blocked, match='missing_fact'):
        resolve(store, job['host'], year_field(), NoInference(), job)


def test_explicit_high_school_year_can_differ_from_college_start(store, job):
    store.put_facts({'college_start': '2025-08', 'high_school_graduation_year': '2023'})
    answer = resolve(store, job['host'], year_field(), context=job)
    assert answer['value'] == '2023'
    assert answer['provenance']['fact_key'] == 'high_school_graduation_year'


def test_cached_inferred_year_cannot_bypass_the_fact_gate(store, job, monkeypatch):
    monkeypatch.setattr(store, 'writing_answer', lambda *a: {'value':'2025', 'provenance':{}})
    with pytest.raises(Blocked, match='missing_fact'):
        resolve(store, job['host'], year_field(), context=job)


def test_prepared_inferred_year_is_rejected_before_source_validation(store, job, package):
    package['answers'].append({'field':year_field(), 'value':'2025', 'provenance':{'sample_parts':[], 'text_hash':digest('2025')}})
    with pytest.raises(Blocked, match='unsupported_or_stale_answer'):
        validate_package(store, job, package)


@pytest.mark.parametrize('value', ['25', '2025-06', 'Not graduated', '2101'])
def test_high_school_year_requires_a_real_year(value):
    with pytest.raises(ValueError): validate_fact('high_school_graduation_year', value)
