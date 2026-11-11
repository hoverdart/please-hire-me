"""Writing-voice profile, persistent preferences, bounded voice repair and the capability matrix."""
import json
import re
from pathlib import Path

import pytest

from hireme import voice
from hireme.capabilities import COLUMNS, LEVELS, MATRIX, markdown, matrix
from hireme.materials import import_material, review_material, writing_context_hash
from hireme.provider import ClaudeProvider
from tests.dashboard_evidence import open_view
from tests.test_history_api import history_server, request

SAMPLE = ('''I build small tools that other people end up depending on. Last summer I wrote a scheduler for my lab. It ran every night and nobody noticed, which was the point.

I like work where the result is easy to check. If a job got faster, I can say by how much. If it didn't, I say that too.

Most of what I know came from fixing my own mistakes; the rest came from reading other people's code. I don't mind being wrong in a pull request. I would rather find out early.''')


def approve(store, text=SAMPLE, role='style', kind='writing_sample', name='sample.txt'):
    material = import_material(store, text.encode(), name, kind)
    review_material(store, material['id'], text, role, True)
    return material['id']


def test_profile_measures_voice_from_approved_samples_only_and_contains_no_sample_text(store):
    empty = voice.profile(store)
    assert empty['sample_count'] == 0 and empty['measured'] is None and 'Not enough approved writing' in empty['summary'][0]
    unapproved = import_material(store, b'Unreviewed text that is long enough to be a sample but was never approved by the applicant.', 'draft.txt', 'writing_sample')
    approve(store, 'A reference document written by someone else entirely, with very long and ornate sentences throughout! ' * 12,
            role='reference', kind='context', name='reference.txt')
    sample = approve(store)
    described = voice.profile(store)
    assert [source['id'] for source in described['sources']] == [sample] and unapproved['id'] != sample
    measured = described['measured']
    assert 6 <= measured['median_sentence_words'] <= 14 and measured['exclamations_per_100_sentences'] == 0
    assert measured['contractions_per_100_words'] > 0.8 and measured['first_person_opening_share'] >= 0.4
    assert 'You use contractions.' in described['summary'] and any('no exclamation marks' in line for line in described['summary'])
    serialized = json.dumps({key: value for key, value in described.items() if key != 'preferences'})
    assert 'scheduler' not in serialized and 'pull request' not in serialized
    guide = voice.guidance(store)
    assert guide['use_contractions'] is True and guide['allow_exclamation_marks'] is False and 'scheduler' not in json.dumps(guide)


def test_preferences_persist_validate_and_invalidate_cached_writing(store, job):
    before = writing_context_hash(store, job)
    for bad in ({'avoid_phrases': 'excited'}, {'exclamations': 'always'}, {'max_sentence_words': 5}, {'notes': 'x' * 700}, {'unknown': 1},
                {'avoid_phrases': ['x']}, {'max_sentence_words': 20.5}):
        with pytest.raises(ValueError):
            voice.save_preferences(store, bad, 0)
    assert writing_context_hash(store, job) == before
    saved = voice.save_preferences(store, {'avoid_phrases': ['circle back', ' circle  back '], 'exclamations': 'never', 'max_sentence_words': 24,
                                           'notes': 'I open with the work.'}, 0)
    assert saved['revision'] == 1 and saved['preferences']['avoid_phrases'] == ['circle back']
    assert writing_context_hash(store, job) != before
    with pytest.raises(ValueError, match='changed elsewhere'):
        voice.save_preferences(store, {'notes': 'stale write'}, 0)
    guide = voice.guidance(store)
    assert guide['avoid_phrases'] == ['circle back'] and guide['longest_sentence_words'] == 24 and guide['applicant_notes'] == 'I open with the work.'
    assert voice.profile(store)['preferences_revision'] == 1


def test_installations_without_saved_preferences_keep_their_existing_writing_hashes(store, job):
    from hireme.util import digest
    revisions = [tuple(r) for r in store.db.execute('SELECT id,revision,role,kind FROM materials WHERE confirmed=1 ORDER BY id')]
    legacy = digest({'grounding_version': 6, 'posting': {k: job.get(k, '') for k in ('company', 'title', 'description')},
                     'materials': revisions, 'facts': {k: (v['value'], v['revision']) for k, v in store.facts().items()}})
    assert writing_context_hash(store, job) == legacy


@pytest.mark.parametrize('text,expected', [
    ('Dear Hiring Team,\n\nI built the scheduler.\n\nSincerely,\nTest Person', 'I built the scheduler.'),
    ('Hello,\nI built the scheduler.\nBest regards,', 'I built the scheduler.'),
    ('I built the scheduler. Thanks to my lab for the data.', 'I built the scheduler. Thanks to my lab for the data.'),
    ('Best practices mattered here.\nI wrote tests first.', 'Best practices mattered here.\nI wrote tests first.'),
])
def test_greetings_and_signoffs_are_removed_only_as_whole_lines(text, expected):
    assert voice.strip_letter_frame(text, 'Why do you want this role?') == expected


def test_complete_letter_requests_keep_their_greeting():
    letter = 'Dear Hiring Team,\n\nI built the scheduler.\n\nSincerely,\nTest Person'
    assert voice.strip_letter_frame(letter, 'Please paste your cover letter.') == letter


def test_review_notes_cover_stock_phrases_copying_repetition_and_preferences():
    guide = {'avoid_phrases': ['circle back'], 'avoid_stock_phrases': list(voice.GENERIC), 'allow_exclamation_marks': False,
             'use_contractions': False, 'longest_sentence_words': 14}
    posting = 'You will design, build and operate reliable distributed data pipelines for our global logistics network.'
    draft = ("I am passionate about logistics! I can't wait to circle back. I would design, build and operate reliable distributed data pipelines "
             'for our global logistics network every day. I built a scheduler. I wrote tests. I measured it.')
    notes = ' '.join(voice.review(draft, guide, posting=posting, previous=['I built a scheduler that ran every single night.']))
    for fragment in ('circle back', 'stock phrases', 'exclamation', 'contractions', 'longer than 14', 'start with “I”', 'word for word'):
        assert fragment in notes
    clean = 'The scheduler I wrote for my lab ran every night. It cut the batch from four hours to thirty-five minutes.'
    assert voice.review(clean, guide, posting=posting) == []
    assert 'repeats an earlier answer' in ' '.join(voice.review(clean, guide, previous=[clean]))


class Scripted(ClaudeProvider):
    """Replays model replies; records each request. No process is started."""
    def __init__(self, replies):
        self.replies = list(replies);self.requests = [];self.observer = None

    def request(self, instruction, data, schema):
        self.requests.append((instruction, data))
        return self.replies.pop(0)


CHOICES = [{'id': 's1', 'text': 'I wrote a scheduler for my lab that ran every night.'}]
STYLE = {'description': ['Short sentences.'], 'avoid_phrases': [], 'avoid_stock_phrases': list(voice.GENERIC), 'allow_exclamation_marks': False,
         'use_contractions': True, 'longest_sentence_words': None}


def test_voice_repair_runs_once_and_never_rejects_a_supported_answer():
    stock = {'answer': 'Dear Hiring Team,\nI am passionate about scheduling!\nSincerely,', 'sentence_ids': ['s1']}
    still = {'answer': 'I am passionate about the scheduler I wrote for my lab.', 'sentence_ids': ['s1']}
    model = Scripted([stock, still, {'supported': True, 'reason': ''}])
    events = [];model.observer = lambda kind, detail: events.append(kind)
    draft = model.draft_answer('Why this role?', CHOICES, {'description': 'Build schedulers.', 'style_profile': STYLE})
    # One voice repair, then the factual review decides. A remaining stock phrase does not block the answer.
    assert draft['answer'] == still['answer'] and len(model.requests) == 3 and events.count('writing_voice_repair') == 1
    repair = model.requests[1][1]
    assert repair['repair_feedback'].startswith('Voice only; keep every fact') and repair['previous_draft']['answer'] == 'I am passionate about scheduling!'
    assert model.requests[0][1]['style_profile'] == STYLE and 'style_profile' not in model.requests[0][1]['posting']


def test_unsupported_claims_are_still_rejected_after_a_voice_pass():
    good_voice = {'answer': 'I wrote a scheduler that saved my lab $2 million.', 'sentence_ids': ['s1']}
    model = Scripted([good_voice, {'supported': False, 'reason': 'The saving is not in a source.'}] * 3)
    draft = model.draft_answer('Why this role?', CHOICES, {'style_profile': STYLE})
    assert draft['answer'] == '' and draft['rejected'] == 'The saving is not in a source.'


def test_voice_api_and_dashboard_save_preferences_and_show_the_profile(store, history_server):
    from playwright.sync_api import expect, sync_playwright
    approve(store)
    status, described = request(history_server, '/api/voice')
    assert status == 200 and described['sample_count'] == 1
    assert request(history_server, '/api/voice-preferences', {'preferences': {'exclamations': 'loud'}, 'revision': 0})[0] == 400
    with sync_playwright() as p:
        browser = p.chromium.launch();page = browser.new_page(viewport={'width': 390, 'height': 900});errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(history_server + '/#token=fixture-capability');open_view(page, 'materials')
        expect(page.locator('#voice-summary')).to_contain_text('You use contractions.')
        expect(page.locator('#voice-sources')).to_contain_text('1 approved sample')
        form = page.locator('#voice-form')
        form.locator('[name=avoid_phrases]').fill('circle back\nreach out')
        form.locator('[name=exclamations]').select_option('never')
        page.evaluate('refresh()')
        expect(form.locator('[name=avoid_phrases]')).to_have_value('circle back\nreach out')
        with page.expect_response('**/api/voice-preferences') as response:
            form.get_by_role('button', name='Save writing preferences').click()
        assert response.value.status == 200
        expect(page.locator('#voice-status')).to_contain_text('Preferences saved')
        assert voice.preferences(store)[0]['avoid_phrases'] == ['circle back', 'reach out']
        # A background sample refresh must not attach another tab's newer
        # revision to our unsaved, older preferences.
        form.locator('[name=notes]').fill('My unsaved preference')
        voice.save_preferences(store, {'notes': 'Saved in another window'}, 1)
        with page.expect_response('**/api/voice'):
            page.evaluate("writingVoice.render({...state, materials: [...state.materials, {id: 'refresh-fixture', revision: 1}]})")
        expect(form.locator('[name=notes]')).to_have_value('My unsaved preference')
        with page.expect_response('**/api/voice-preferences') as response:
            form.get_by_role('button', name='Save writing preferences').click()
        assert response.value.status == 400
        expect(page.locator('#voice-status')).to_contain_text('changed elsewhere')
        assert voice.preferences(store)[0]['notes'] == 'Saved in another window'
        assert page.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth+1") and not errors
        browser.close()


def test_capability_matrix_is_complete_honest_and_matches_its_documentation(store, job, package):
    assert [entry['platform'] for entry in MATRIX][:3] == ['Ashby', 'Lever', 'Greenhouse']
    for entry in MATRIX:
        assert set(entry['cells']) == set(COLUMNS)
        for column, (level, detail) in entry['cells'].items():
            assert level in LEVELS and detail
        for path in entry['cells']['fixture_coverage'][1]:
            assert Path(path).is_file(), path
    live = {entry['platform']: entry['cells']['live_validation'][0] for entry in MATRIX}
    # No platform is certified, and platforms without employer evidence say so.
    assert 'implemented' not in live.values()
    for platform in ('Lever', 'Workday (other tenants)', 'iCIMS', 'Custom employer forms', 'Handshake / Work at a Startup'):
        assert live[platform] == 'none'
    document = Path('docs/capability-matrix.md').read_text()
    table = re.search(r'<!-- capability-matrix:start -->\n(.*)\n<!-- capability-matrix:end -->', document, re.S)[1]
    assert table == markdown()
    aid = store.prepare(job, package);store.begin_submit(aid);store.finish(aid, 'unknown', 'No receipt')
    local = {row['platform']: row['this_installation'] for row in matrix(store)['platforms']}
    assert local['Lever'] == {'confirmed': 0, 'uncertain': 1, 'not_submitted': 0} and local['Ashby']['uncertain'] == 0
    from hireme.coverage import coverage
    assert coverage(store)['capability_matrix']['platforms'][1]['this_installation']['uncertain'] == 1


def test_cover_letter_pdf_follows_the_saved_resume_style(store, job):
    import pymupdf
    from hireme.letters import _fingerprint, render_letter
    body = 'I wrote a scheduler for my lab.\n\nIt ran every night.'
    before = _fingerprint(store, job)
    serif = render_letter('Test Person', 'test@candidate.invalid | Berkeley, CA', 'Acme', 'Engineer', body, {'template': 'compact'})
    sans = render_letter('Test Person', 'test@candidate.invalid | Berkeley, CA', 'Acme', 'Engineer', body, {'template': 'modern'})
    fonts = lambda data: {font[3].split('+')[1] for font in pymupdf.open(stream=data, filetype='pdf')[0].get_fonts()}
    assert fonts(serif) == {'DejaVuSerif', 'DejaVuSerif-Bold'} and fonts(sans) == {'DejaVuSans', 'DejaVuSans-Bold'}
    page = pymupdf.open(stream=sans, filetype='pdf')[0]
    assert {link['uri'] for link in page.get_links()} == {'mailto:test@candidate.invalid'}
    assert all(span['size'] >= 9 for block in page.get_text('dict')['blocks'] for line in block.get('lines', []) for span in line['spans'])
    store.update_settings({'resume_style': {'template': 'modern'}})
    assert _fingerprint(store, job) != before
    from hireme.util import Blocked
    with pytest.raises(Blocked, match='cover_letter_font_unsupported'):
        render_letter('山田', '', 'Acme', 'Engineer', body)
