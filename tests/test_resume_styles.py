"""Resume templates, bounded style settings, reference measurement and grounded rewriting."""
import base64
import io
import json

import pytest
from pypdf import PdfReader

from hireme import tailored_resumes
from hireme.application_artifacts import download
from hireme.resume_styles import TEMPLATES, render_resume, style_from_reference, validate_style
from hireme.util import Blocked
from tests.dashboard_evidence import open_view
from tests.test_history_api import history_server, request


UNICODE_LINE = 'Implemented a Σ-solver with ≤ 2 % gap for Zoë’s résumé parser — “naïve” baseline.'


def content(lines=3, roles=2):
    sample = tailored_resumes.sample_content()
    for name, entries in sample['sections']:
        if name == 'experience':
            entries[:] = [{'heading': f'Engineer | The Very Long Named International Institute for Applied Research {i}',
                           'meta': 'Sample City, ST', 'dates': 'Jun 2025 – Aug 2025', 'bulleted': True,
                           'lines': [UNICODE_LINE] + [f'Confirmed line {i}-{n} about verified work.' for n in range(lines - 1)]}
                          for i in range(roles)]
    sample['contact'] = ['sample@example.invalid', 'github.com/sample/a-deliberately-long-repository-name-that-must-wrap-inside-the-page-margins-without-overflowing']
    return sample


@pytest.fixture
def experience(store):
    store.update_settings({'tailored_resumes': True})
    return store.put_history_entry('employment', {
        'organization': 'Previous Employer', 'title': 'Software Intern', 'start': '2025-06', 'end': '2025-08',
        'description': 'Built Python services for scheduling 40 nightly jobs.\nMaintained TypeScript interfaces.\n'
                       'Wrote SQL migration tooling.\nDocumented team workflows.\nImplemented image processing in Rust.'
    }, source='Applicant reviewed employment history', confirmed=True)


def test_style_validation_expands_templates_and_enforces_readable_bounds():
    assert validate_style(None)['template'] == 'ats' and validate_style({'template': 'compact'})['font'] == 'serif'
    assert validate_style({'template': 'modern', 'body_size': 11})['body_size'] == 11
    for bad in ({'body_size': 7}, {'body_size': 14}, {'margin': 10}, {'line_spacing': 0.9}, {'max_bullets': 0},
                {'max_pages': 3}, {'accent': '#ffff00'}, {'accent': 'red'}, {'font': 'comic'}, {'template': 'fancy'},
                {'section_order': ['experience']}, {'section_order': ['experience', 'experience', 'skills', 'projects']},
                {'template': 'reference'}, {'unknown': 1}, {'mode': 'invent'}, {'max_bullets': 2.5}, {'rule': 'yes'}):
        with pytest.raises(ValueError):
            validate_style(bad)
    with pytest.raises(ValueError):
        validate_style('ats')


@pytest.mark.parametrize('style', [
    {'template': []}, {'font': {}}, {'section_order': ['education', None, 'projects', 'skills']},
])
def test_malformed_style_values_produce_validation_errors(style):
    with pytest.raises(ValueError):
        validate_style(style)


def test_all_supported_ats_can_be_selected_together(store):
    names = ['lever', 'ashby', 'greenhouse', 'workday', 'icims', 'other']
    assert store.update_settings({'allowed_application_ats': names})['allowed_application_ats'] == names
    with pytest.raises(ValueError):
        store.update_settings({'allowed_application_ats': ['lever', 'lever']})


@pytest.mark.parametrize('template', sorted(TEMPLATES))
def test_each_template_embeds_fonts_and_keeps_every_line_selectable_in_reading_order(template):
    import pymupdf
    sample = content()
    data, text, measured = render_resume(sample, {'template': template, 'max_pages': 2})
    document = pymupdf.open(stream=data, filetype='pdf')
    fonts = {font[3] for page in document for font in page.get_fonts()}
    # Subset-embedded DejaVu faces only: no reliance on fonts installed on the computer.
    assert fonts and all('+DejaVu' in name for name in fonts)
    extracted = ''.join(''.join(page.get_text().split()) for page in document)
    position = 0
    for line in text.splitlines():
        found = extracted.find(''.join(line.split()), position)
        assert found >= 0, line
        position = found
    assert UNICODE_LINE.replace(' ', '') in extracted
    links = {link.get('uri') for page in document for link in page.get_links()}
    assert 'mailto:sample@example.invalid' in links and any(uri.startswith('https://github.com/sample/') for uri in links)
    # Nothing is drawn outside the page margins, including the long link and organization name.
    margin = validate_style({'template': template})['margin']
    for page in document:
        for block in page.get_text('blocks'):
            assert block[0] >= margin - 1 and block[2] <= 612 - margin + 1, block
    assert measured['pages'] == len(document) <= 2
    assert all(size >= 9 for page in document for block in page.get_text('dict')['blocks'] for line in block.get('lines', [])
               for span in line['spans'] for size in [span['size']] if span['text'].strip() and span['text'] != '•')


def test_unsupported_characters_block_instead_of_dropping_text():
    sample = content()
    sample['name'] = '山田 太郎'
    with pytest.raises(Blocked, match='resume_font_unsupported') as error:
        render_resume(sample, {'template': 'ats'})
    assert '山' in error.value.args[1] if len(error.value.args) > 1 else True


def test_content_beyond_the_page_limit_is_refused_not_trimmed():
    with pytest.raises(Blocked, match='resume_too_long'):
        render_resume(content(lines=8, roles=8), {'template': 'compact', 'max_pages': 1})
    data, text, measured = render_resume(content(lines=8, roles=8), {'template': 'compact', 'max_pages': 2})
    assert measured['pages'] == 2 and text.count('Confirmed line') == 56


def test_changing_the_saved_style_builds_a_new_revision_and_keeps_the_old_pdf(store, job, experience):
    first = tailored_resumes.generate(store, job)
    original = tailored_resumes.validate(store, job, first)
    store.update_settings({'resume_style': {'template': 'modern', 'accent': '#1f4d3a'}})
    with pytest.raises(Blocked, match='tailored_resume_stale'):
        tailored_resumes.validate(store, job, first)
    second = tailored_resumes.generate(store, job)
    assert second['hash'] != first['hash']
    rows = list(store.db.execute("SELECT revision,provenance FROM application_artifacts WHERE kind='resume' ORDER BY revision"))
    assert [row['revision'] for row in rows] == [1, 2]
    assert [json.loads(row['provenance'])['style']['template'] for row in rows] == ['ats', 'modern']
    assert download(store, first['artifact_id'], first['hash'])[0] == original
    with pytest.raises(ValueError):
        store.update_settings({'resume_style': {'template': 'ats', 'body_size': 6}})


def test_selection_record_explains_included_and_omitted_lines(store, job, experience):
    document = tailored_resumes.generate(store, {**job, 'description': 'Rust image processing'})
    record = tailored_resumes.explain(store, document['artifact_id'])
    role = next(entry for entry in record['selection'] if entry['section'] == 'experience')
    by_text = {line['text']: line for line in role['lines']}
    assert by_text['Implemented image processing in Rust.']['selected'] and 'rust' in by_text['Implemented image processing in Rust.']['matched']
    assert by_text['Documented team workflows.']['selected'] is False and 'Left out' in by_text['Documented team workflows.']['reason']
    assert record['method'] == 'confirmed_history_selection' and record['rewriting']['status'] == 'not_enabled'
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
    with pytest.raises(ValueError):
        tailored_resumes.explain(store, 'missing')


def test_confirmed_projects_form_their_own_section_and_stay_out_of_employment(store, job, experience):
    with pytest.raises(ValueError):
        store.put_history_entry('project', {'name': 'Solver', 'description': 'Built it.', 'url': 'http://insecure.invalid'}, source='Mine', confirmed=True)
    with pytest.raises(ValueError):
        store.put_history_entry('project', {'name': 'Solver'}, source='Mine', confirmed=True)
    store.put_history_entry('project', {'name': 'Timetable Solver', 'role': 'Author', 'url': 'https://github.com/sample/solver',
                                        'start': '2025-01', 'description': 'Built a constraint solver in Rust.\nPublished documentation.'},
                            source='My repository', confirmed=True)
    store.put_history_entry('project', {'name': 'Unconfirmed Project', 'description': 'Must never appear.'}, source='Draft')
    document = tailored_resumes.generate(store, {**job, 'description': 'Rust constraint solver'})
    text = store.db.execute('SELECT content FROM application_artifacts WHERE id=?', (document['artifact_id'],)).fetchone()[0]
    assert 'Projects' in text.title() and 'Timetable Solver | Author' in text and 'github.com/sample/solver' in text
    assert 'Unconfirmed Project' not in text
    assert [row['kind'] for row in store.history_entries('employment')] == ['employment']


def test_confirmed_projects_can_tailor_a_resume_without_employment(store, job):
    store.update_settings({'tailored_resumes': True})
    store.put_history_entry('project', {
        'name': 'Timetable Solver', 'role': 'Author', 'url': 'https://github.com/sample/solver',
        'description': 'Built a constraint solver in Python.\nPublished reproducible examples.'
    }, source='Applicant-confirmed project', confirmed=True)
    document = tailored_resumes.generate(store, job)
    data = tailored_resumes.validate(store, job, document)
    text = '\n'.join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
    assert 'Timetable Solver | Author' in text and 'Built a constraint solver in Python.' in text
    assert 'EXPERIENCE' not in text and 'Sample Applicant' not in text
    import pymupdf
    pdf = pymupdf.open(stream=data, filetype='pdf')
    assert 'https://github.com/sample/solver' in {link.get('uri') for page in pdf for link in page.get_links()}
    assert tailored_resumes.preview(store)['sample'] is False
    assert not store.history_entries('employment')


def test_reference_resume_contributes_measurements_and_none_of_its_words():
    reference, _, _ = render_resume(content(), {'template': 'compact', 'max_pages': 2})
    style, notes = style_from_reference(reference)
    assert style['template'] == 'reference' and style['font'] == 'serif' and style['body_size'] == 9.5
    assert style['date_layout'] == 'right' and style['heading_case'] == 'upper' and 38 <= style['margin'] <= 42
    assert style['section_order'] == ['education', 'experience', 'projects', 'skills']
    serialized = json.dumps([style, notes])
    for word in ('Sample', 'Institute', 'sample@example.invalid', 'Confirmed line'):
        assert word not in serialized
    # The measured style renders the applicant's own content, never the reference text.
    data, text, _ = render_resume(tailored_resumes.sample_content(), style)
    assert 'Institute for Applied Research' not in text and data.startswith(b'%PDF-')
    reordered = content()
    reordered['sections'].sort(key=lambda section: ['skills', 'experience', 'education', 'projects'].index(section[0]))
    sans, _ = style_from_reference(render_resume(reordered, {'template': 'modern', 'max_pages': 2})[0])
    assert sans['font'] == 'sans' and sans['heading_case'] == 'title'
    assert sans['section_order'] == ['skills', 'experience', 'education', 'projects']
    for bad in (b'not a pdf', b'%PDF-1.4\nbroken'):
        with pytest.raises(ValueError):
            style_from_reference(bad)


class Rewriter:
    """A synthetic model: one faithful rewrite, one that invents a number, one the reviewer rejects."""
    def __init__(self, review=()):
        self.calls = [];self.review = review

    def request(self, instruction, data, schema):
        self.calls.append((instruction, data))
        if 'Independently check' in instruction:
            return {'unsupported': [{'id': line['id'], 'reason': 'Changes the scope of the work.'}
                                    for line in data['lines'] if line['rewritten_line'] in self.review]}
        result = []
        for line in data['lines']:
            if line['confirmed_line'].startswith('Built Python'):
                result.append({'id': line['id'], 'text': 'Built Python scheduling services for 40 nightly jobs.'})
            elif line['confirmed_line'].startswith('Maintained'):
                result.append({'id': line['id'], 'text': 'Maintained 12 TypeScript interfaces used by 300 engineers.'})
            elif line['confirmed_line'].startswith('Wrote SQL'):
                result.append({'id': line['id'], 'text': 'Wrote tooling for SQL migration.'})
        return {'lines': result}


def test_rewriting_keeps_only_lines_that_pass_the_source_check_and_review(store, job, experience):
    store.update_settings({'tailored_writing': True, 'resume_style': {'template': 'ats', 'mode': 'rewrite'}})
    model = Rewriter(review={'Wrote tooling for SQL migration.'})
    document = tailored_resumes.generate(store, job, provider=model)
    record = tailored_resumes.explain(store, document['artifact_id'])
    text = store.db.execute('SELECT content FROM application_artifacts WHERE id=?', (document['artifact_id'],)).fetchone()[0]
    assert 'Built Python scheduling services for 40 nightly jobs.' in text
    assert 'Maintained TypeScript interfaces.' in text and '300 engineers' not in text
    assert 'Wrote SQL migration tooling.' in text and 'Wrote tooling for SQL migration.' not in text
    outcomes = {line['original']: line for line in record['rewriting']['lines']}
    assert outcomes['Built Python services for scheduling 40 nightly jobs.']['used'] is True
    assert 'adds a number' in outcomes['Maintained TypeScript interfaces.']['reason']
    assert outcomes['Wrote SQL migration tooling.']['reason'].startswith('Review:')
    assert record['method'] == 'grounded_rewrite' and len(model.calls) == 2
    # The posting is passed as data; the reviewer never sees the invented line that failed the source check.
    assert all(line['rewritten_line'] != 'Maintained 12 TypeScript interfaces used by 300 engineers.' for line in model.calls[1][1]['lines'])
    assert tailored_resumes.validate(store, job, document).startswith(b'%PDF-')
    assert tailored_resumes.generate(store, job, provider=model) == document and len(model.calls) == 2


def test_rewriting_needs_writing_authorization_and_falls_back_when_the_model_is_unavailable(store, job, experience):
    store.update_settings({'resume_style': {'template': 'ats', 'mode': 'rewrite'}})
    model = Rewriter()
    first = tailored_resumes.generate(store, job, provider=model)
    assert not model.calls and tailored_resumes.explain(store, first['artifact_id'])['rewriting']['status'] == 'not_enabled'
    store.update_settings({'tailored_writing': True})

    class Unavailable:
        def request(self, *args):
            raise Blocked('provider_unavailable')
    second = tailored_resumes.generate(store, job, provider=Unavailable())
    record = tailored_resumes.explain(store, second['artifact_id'])
    assert record['rewriting']['status'] == 'model_unavailable' and record['method'] == 'confirmed_history_selection'
    text = store.db.execute('SELECT content FROM application_artifacts WHERE id=?', (second['artifact_id'],)).fetchone()[0]
    assert 'Built Python services for scheduling 40 nightly jobs.' in text


def test_voice_changes_invalidate_rewritten_resumes_and_preserve_old_revisions(store, job, experience):
    from hireme.voice import save_preferences
    store.update_settings({'tailored_writing': True, 'resume_style': {'template': 'ats', 'mode': 'rewrite'}})
    model = Rewriter()
    first = tailored_resumes.generate(store, job, provider=model)
    original = tailored_resumes.validate(store, job, first)
    save_preferences(store, {'avoid_phrases': ['circle back'], 'notes': 'Use direct verbs.'}, 0)
    with pytest.raises(Blocked, match='tailored_resume_stale'):
        tailored_resumes.validate(store, job, first)
    second = tailored_resumes.generate(store, job, provider=model)
    assert second['artifact_id'] != first['artifact_id']
    assert model.calls[-2][1]['writing_voice']['avoid_phrases'] == ['circle back']
    assert download(store, first['artifact_id'], first['hash'])[0] == original


@pytest.mark.parametrize('rewritten,refused', [
    ('Built Python scheduling services for 40 nightly jobs.', False),
    ('Built Python services that schedule 40 nightly jobs.', False),
    ('Built Python services for scheduling 400 nightly jobs.', True),
    ('Built Python and Kubernetes services for scheduling 40 nightly jobs.', True),
    ('Led a team that built Python services for scheduling 40 nightly jobs at Google.', True),
    ('Spearheaded world-class Python services for scheduling 40 nightly jobs.', True),
    ('Built Python services for scheduling 40 nightly jobs, improving reliability and reducing customer churn significantly.', True),
    ('Python.', True),
    ('Built Python services for scheduling 40 nightly jobs. See https://example.invalid/more', True),
])
def test_deterministic_rewrite_guard(rewritten, refused):
    original = 'Built Python services for scheduling 40 nightly jobs.'
    entry = json.dumps({'organization': 'Previous Employer', 'title': 'Software Intern', 'description': original})
    assert bool(tailored_resumes._grounded_rewrite(original, rewritten, entry)) is refused


def test_preview_uses_labelled_sample_text_until_history_is_confirmed(store, job):
    sample = tailored_resumes.preview(store, {'template': 'modern'})
    text = ' '.join(page.extract_text() for page in PdfReader(io.BytesIO(sample['data'])).pages)
    assert sample['sample'] is True and 'Sample Applicant' in text and 'Test Person' not in text
    store.put_history_entry('employment', {'organization': 'Previous Employer', 'title': 'Software Intern',
                                           'description': 'Built Python services.'}, source='Reviewed', confirmed=True)
    own = tailored_resumes.preview(store, {'template': 'modern'})
    text = ' '.join(page.extract_text() for page in PdfReader(io.BytesIO(own['data'])).pages)
    assert own['sample'] is False and 'Test Person' in text and 'Sample Applicant' not in text
    assert not store.db.execute('SELECT 1 FROM application_artifacts').fetchone()


def test_preview_surfaces_changed_documents_instead_of_showing_sample_content(store, experience):
    row=store.db.execute("SELECT filename FROM documents WHERE kind='resume'").fetchone()
    (store.root/'documents'/row['filename']).write_bytes(b'%PDF-1.4\nchanged resume bytes')
    with pytest.raises(Blocked,match='document_tampered'):
        tailored_resumes.preview(store)


def test_style_api_saves_previews_and_measures_a_reference_without_storing_it(store, history_server):
    import urllib.request
    base = history_server
    status, current = request(base, '/api/resume-style')
    assert status == 200 and current['style']['template'] == 'ats' and set(current['templates']) == set(TEMPLATES)
    assert request(base, '/api/resume-style', {'style': {'template': 'ats', 'body_size': 4}})[0] == 400
    assert request(base, '/api/resume-style', {'style': {'template': 'modern'}, 'extra': 1})[0] == 400
    status, saved = request(base, '/api/resume-style', {'style': {'template': 'modern', 'max_bullets': 3}})
    assert status == 200 and saved['style']['max_bullets'] == 3 and store.settings()['resume_style'] == {'template': 'modern', 'max_bullets': 3}
    status, preview = request(base, '/api/resume-preview', {'style': {'template': 'compact'}})
    assert status == 200 and preview['sample'] is True and base64.b64decode(preview['pdf']).startswith(b'%PDF-')
    reference = render_resume(content(), {'template': 'compact', 'max_pages': 2})[0]
    before = sorted(path.name for path in store.root.rglob('*') if path.is_file())
    upload = urllib.request.Request(base + '/api/resume-style-reference', data=reference,
                                    headers={'X-Hireme-Token': 'fixture-capability', 'Content-Type': 'application/pdf'})
    with urllib.request.urlopen(upload) as response:
        measured = json.load(response)
    assert measured['style']['template'] == 'reference' and measured['style']['font'] == 'serif' and measured['notes']
    after = sorted(path.name for path in store.root.rglob('*') if path.is_file() and not path.name.endswith(('-wal', '-shm')))
    assert after == [name for name in before if not name.endswith(('-wal', '-shm'))]
    assert request(base, '/api/resume-explain?id=missing')[0] == 400


@pytest.mark.parametrize('width', [360, 1280])
def test_style_controls_preview_save_and_survive_refresh(store, history_server, width):
    from playwright.sync_api import expect, sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch();page = browser.new_page(viewport={'width': width, 'height': 900});errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(history_server + '/#token=fixture-capability');open_view(page, 'materials')
        form = page.locator('#resume-style-form')
        expect(form.locator('[name=template]')).to_have_value('ats')
        expect(form.locator('[name=template] option[value=reference]')).to_have_attribute('disabled', '')
        form.locator('[name=template]').select_option('modern')
        expect(form.locator('[name=accent]')).to_have_value('#3155d9')
        expect(page.locator('#resume-section-order li').first).to_contain_text('Experience')
        page.get_by_role('button', name='Move down: Experience').click()
        expect(page.locator('#resume-section-order li').nth(1)).to_contain_text('Experience')
        form.locator('[name=max_bullets]').fill('3')
        # A background refresh must not discard the unsaved choices.
        page.evaluate('refresh()')
        expect(form.locator('[name=max_bullets]')).to_have_value('3')
        with page.expect_response('**/api/resume-preview') as response:
            page.locator('#resume-style-preview').click()
        assert response.value.status == 200
        expect(page.locator('#resume-style-preview-panel')).to_be_visible()
        expect(page.locator('#resume-style-preview-note')).to_contain_text('Sample text only')
        assert page.locator('#resume-style-preview-frame').get_attribute('src').startswith('blob:')
        assert page.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth+1")
        form.locator('[name=body_size]').fill('6')
        form.get_by_role('button', name='Save style').click()
        assert store.settings()['resume_style'] == {'template': 'ats'}
        form.locator('[name=body_size]').fill('10.5')
        with page.expect_response('**/api/resume-style') as response:
            form.get_by_role('button', name='Save style').click()
        assert response.value.status == 200
        expect(page.locator('#resume-style-status')).to_contain_text('Style saved')
        saved = store.settings()['resume_style']
        assert saved['template'] == 'modern' and saved['max_bullets'] == 3 and saved['section_order'][:2] == ['projects', 'experience']
        page.reload();open_view(page, 'materials')
        expect(page.locator('#resume-style-form [name=template]')).to_have_value('modern')
        expect(page.locator('#resume-section-order li').first).to_contain_text('Projects')
        assert not errors
        browser.close()
