"""Free-form notes become reviewed facts and Basic context, never unreviewed claims."""
import pytest

from hireme.answers import resolve
from hireme.context_inbox import apply, open_needs, organize
from hireme.materials import basic_context

NOTES = "i took data structures and algorithms last fall. if not the bay area id pick durham nc. i'd relocate for the summer"


class Organizer:
    def __init__(self, reply): self.reply = reply; self.seen = None
    def organize_context(self, text, catalog, current, questions):
        self.seen = questions
        return self.reply


def reply(**extra):
    return {'facts': [{'key': 'relocate', 'value': 'Yes', 'quote': "i'd relocate for the summer"}],
            'notes': [{'topic': 'Coursework', 'statement': 'Took Data Structures and Algorithms', 'quote': 'took data structures and algorithms last fall'},
                      {'topic': 'Office preferences', 'statement': 'Outside the Bay Area, prefers Durham, NC', 'quote': 'id pick durham nc'}],
            'unclear': ['Which term did you take it?'], 'covers': [0], **extra}


def test_organized_notes_are_grounded_and_validated(store, job):
    store.ask(job['id'], job['host'], 'Have you completed the course: Data Structures & Algorithms?*', ['Yes', 'No'], 'missing_fact')
    model = Organizer(reply(facts=[
        {'key': 'relocate', 'value': 'Yes', 'quote': "i'd relocate for the summer"},
        {'key': 'gpa', 'value': '4.0', 'quote': 'straight As'},                       # not in the notes
        {'key': 'onsite', 'value': 'Sure', 'quote': 'if not the bay area'},          # invalid Yes/No value
    ], notes=reply()['notes'] + [{'topic': 'Awards', 'statement': 'Won a hackathon', 'quote': 'won a hackathon'}]))
    draft = organize(store, NOTES, model)
    assert model.seen == ['Have you completed the course: Data Structures & Algorithms?']
    assert [f['key'] for f in draft['facts']] == ['relocate']
    assert draft['notes'] == ['Coursework: Took Data Structures and Algorithms.', 'Office preferences: Outside the Bay Area, prefers Durham, NC.']
    assert draft['covers'] == ['Have you completed the course: Data Structures & Algorithms?']
    assert 'Which term did you take it?' in draft['unclear'] and any('Yes or No' in x for x in draft['unclear'])
    assert 'relocate' not in store.facts() and not basic_context(store)['text']


def test_reviewed_notes_are_saved_once_and_used_for_answers(store, job):
    draft = organize(store, NOTES, Organizer(reply()))
    with pytest.raises(ValueError):apply(store, {}, '\n'.join(draft['notes']), 0, False)
    assert apply(store, {'relocate': 'Yes'}, '\n'.join(draft['notes']), 0, True) == {'facts': ['relocate'], 'notes': 2}
    context = basic_context(store)
    assert context['confirmed'] and context['text'].splitlines() == draft['notes']
    assert apply(store, {}, draft['notes'][0] + '\nAvailability: Free from May 2027.', context['revision'], True)['notes'] == 1
    assert basic_context(store)['text'].count('Coursework:') == 1
    field = {'label': 'Are you willing to relocate?*', 'type': 'select', 'required': True, 'options': ['Yes', 'No'], 'maxlength': -1}
    assert resolve(store, job['host'], field, context=job)['value'] == 'Yes'


def test_stale_context_revision_and_invalid_facts_save_nothing(store):
    with pytest.raises(ValueError):apply(store, {'relocate': 'Maybe'}, 'Coursework: Took compilers.', 0, True)
    with pytest.raises(ValueError):apply(store, {}, 'Coursework: Took compilers.', 5, True)
    assert 'relocate' not in store.facts() and not basic_context(store)['text']


def test_needs_list_recurring_information_not_model_failures(store, job):
    store.ask(job['id'], job['host'], 'Which office do you prefer?*', ['A', 'B'], 'missing_fact')
    store.ask('other-job', job['host'], 'Which office do you prefer?*', ['A', 'B', 'C'], 'option_mismatch')
    store.ask(job['id'], job['host'], 'Why us?', [], 'provider_timeout')
    assert open_needs(store) == [{'label': 'Which office do you prefer?', 'jobs': 2}]
