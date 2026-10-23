"""Posting-specific resumes assembled from applicant-confirmed history, without rewriting claims."""
from __future__ import annotations

import hashlib
import io
import json
import re
from html import escape

from .util import Blocked, digest, now, private_dir, safe_document, write_private_blob

FORMAT_VERSION = 1
CONTACT_KEYS = ('full_name', 'email', 'phone', 'location', 'linkedin', 'github', 'website')
STOP_WORDS = {'and', 'the', 'with', 'for', 'our', 'you', 'your', 'will', 'from', 'that', 'this', 'have', 'work', 'role'}


def _terms(text):
    return set(re.findall(r'[a-z][a-z0-9+#.]{1,}', text.casefold())) - STOP_WORDS


def _sources(store, job):
    facts = store.facts()
    if not facts.get('full_name') or not facts.get('email'):
        raise Blocked('resume_facts_required', 'Confirm your full name and email in Your facts.')
    history = store.history_entries()
    employment = [row for row in history if row['kind'] == 'employment']
    if not employment or not any(row['values'].get('description') for row in employment):
        raise Blocked('resume_history_required', 'Add confirmed employment entries and experience descriptions in Your facts before tailoring a resume.')
    if len(history) > 20:
        raise Blocked('resume_history_too_large', 'Resume assembly supports at most 20 confirmed history entries.')
    original = store.db.execute("SELECT hash,filename FROM documents WHERE kind='resume'").fetchone()
    if not original:
        raise Blocked('missing_document', 'Upload your original resume first.')
    path = safe_document(store.root / 'documents' / original['filename'], store.root / 'documents')
    if hashlib.sha256(path.read_bytes()).hexdigest() != original['hash']:
        raise Blocked('document_tampered')
    return {'facts': facts, 'history': history, 'original_resume': dict(original),
            'job': {key: job.get(key, '') for key in ('id', 'url', 'company', 'title', 'description')},
            'enabled': store.settings()['tailored_resumes'], 'format': FORMAT_VERSION}


def _date_range(values):
    start = values.get('start', '')
    end = values.get('end', '')
    if values.get('current') is True:
        end = 'Present'
    elif values.get('expected_end'):
        end = 'Expected ' + values['expected_end']
    return ' - '.join(value for value in (start, end) if value)


def assemble(sources):
    """Select exact description lines within their original employer/role context."""
    facts, history, job = sources['facts'], sources['history'], sources['job']
    terms = _terms(job['title'] + ' ' + job['description'])
    rank = lambda text: len(terms & _terms(text))
    sections = []
    education = [row for row in history if row['kind'] == 'education']
    if education:
        sections.append(('Education', []))
        for row in education:
            values = row['values']
            sections[-1][1].append({'heading': values['institution'], 'lines': [value for value in
                (values.get('degree'), values.get('field_of_study'), values.get('location'), _date_range(values),
                 'GPA: ' + values['gpa'] if values.get('gpa') else '') if value], 'source_id': row['id']})
    employment = [row for row in history if row['kind'] == 'employment']
    employment.sort(key=lambda row: (-rank(' '.join(str(value) for value in row['values'].values())), row['id']))
    sections.append(('Experience', []))
    for row in employment:
        values = row['values']
        lines = list(dict.fromkeys(line.strip() for line in values.get('description', '').splitlines() if line.strip()))
        # Pick relevant complete lines, then retain their original reading order.
        selected = sorted(sorted(range(len(lines)), key=lambda i: (-rank(lines[i]), i))[:4])
        sections[-1][1].append({'heading': values['title'] + ' | ' + values['organization'],
            'lines': [value for value in (values.get('location'), _date_range(values)) if value] + [lines[i] for i in selected],
            'source_id': row['id']})
    skills = facts.get('skills', {}).get('value', '')
    if skills:
        items = list(dict.fromkeys(item.strip() for item in skills.split(',') if item.strip()))
        items.sort(key=lambda item: -rank(item))
        sections.append(('Skills', [{'heading': '', 'lines': [', '.join(items)], 'source_id': 'fact:skills'}]))
    contact = [facts[key]['value'] for key in CONTACT_KEYS[1:] if key in facts]
    return {'name': facts['full_name']['value'], 'contact': contact, 'sections': sections}


def render(content):
    from pathlib import Path
    from reportlab import __file__ as reportlab_file
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from pypdf import PdfReader
    if 'HiremeResume' not in pdfmetrics.getRegisteredFontNames():
        directory = Path(reportlab_file).parent / 'fonts'
        pdfmetrics.registerFont(TTFont('HiremeResume', str(directory / 'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('HiremeResumeBold', str(directory / 'VeraBd.ttf')))
    plain = [content['name'], *content['contact']]
    for name, entries in content['sections']:
        plain.append(name)
        for entry in entries:
            plain.extend([entry['heading'], *entry['lines']])
    supported = pdfmetrics.getFont('HiremeResume').face.charToGlyph
    if any(ord(char) not in supported for text in plain for char in text if not char.isspace()):
        raise Blocked('resume_font_unsupported', 'A confirmed value contains characters unsupported by this resume font. Use your original resume.')
    body = ParagraphStyle('resume-body', fontName='HiremeResume', fontSize=10, leading=14, spaceAfter=5)
    heading = ParagraphStyle('resume-heading', parent=body, fontName='HiremeResumeBold', fontSize=11, spaceBefore=10)
    title = ParagraphStyle('resume-name', parent=heading, fontSize=17, leading=22)
    story = [Paragraph(escape(content['name']), title)]
    story.extend(Paragraph(escape(line), body) for line in content['contact'])
    for name, entries in content['sections']:
        story.append(Paragraph(escape(name), heading))
        for entry in entries:
            if entry['heading']: story.append(Paragraph(escape(entry['heading']), heading))
            story.extend(Paragraph(escape(line), body) for line in entry['lines'])
            story.append(Spacer(1, 5))
    stream = io.BytesIO()
    SimpleDocTemplate(stream, pagesize=(612, 792), leftMargin=48, rightMargin=48, topMargin=42,
        bottomMargin=42, author=content['name'], title='Resume').build(story)
    data = stream.getvalue()
    reader = PdfReader(io.BytesIO(data))
    extracted = ' '.join((' '.join(page.extract_text().split()) for page in reader.pages))
    if not 1 <= len(reader.pages) <= 2 or any(' '.join(text.split()) not in extracted for text in plain if text):
        raise Blocked('resume_render_failed', 'The confirmed content must fit within two pages without losing text. Shorten the experience descriptions or use your original resume.')
    return data, '\n'.join(text for text in plain if text)


def generate(store, job):
    if not store.settings()['tailored_resumes']:
        raise Blocked('tailored_resumes_disabled', 'Enable tailored resumes in Preferences after confirming your employment and education history.')
    store.checkpoint()
    sources = _sources(store, job)
    fingerprint = digest(sources)
    row = store.db.execute("SELECT * FROM application_artifacts WHERE job_id=? AND requirement_key='tailored_resume' ORDER BY revision DESC LIMIT 1", (job['id'],)).fetchone()
    if row and row['fingerprint'] == fingerprint:
        document = _document(row)
        validate(store, job, document)
        return document
    data, text = render(assemble(sources))
    checksum = hashlib.sha256(data).hexdigest()
    with store.transaction():
        store.checkpoint()
        if digest(_sources(store, job)) != fingerprint:
            raise Blocked('resume_sources_changed', 'Your history or facts changed while the resume was being built. Generate it again.')
        write_private_blob(private_dir(store.root / 'documents') / (checksum + '.pdf'), data)
        revision = store.db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM application_artifacts WHERE job_id=? AND requirement_key='tailored_resume'", (job['id'],)).fetchone()[0]
        identity = digest([job['id'], 'tailored_resume', revision, checksum, fingerprint])
        provenance = {'method': 'confirmed_history_selection', 'sources': sources}
        store.db.execute('INSERT INTO application_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (identity, job['id'], 'tailored_resume', 'resume', revision, checksum, checksum + '.pdf', text,
             json.dumps(provenance), fingerprint, now()))
        store.event('tailored_resume_generated', job['id'], {'artifact_id': identity, 'hash': checksum, 'revision': revision})
    return _document(store.db.execute('SELECT * FROM application_artifacts WHERE id=?', (identity,)).fetchone())


def _document(row):
    return {'kind': 'resume', 'artifact_id': row['id'], 'hash': row['hash'], 'filename': row['filename'],
            'generated': True, 'job_id': row['job_id']}


def validate(store, job, document):
    row = store.db.execute('SELECT * FROM application_artifacts WHERE id=?', (document.get('artifact_id', document.get('id')),)).fetchone()
    if (not row or row['kind'] != 'resume' or document.get('kind') != 'resume' or row['job_id'] != job['id']
            or document.get('job_id') != job['id'] or row['hash'] != document.get('hash') or row['filename'] != document.get('filename')):
        raise Blocked('resume_artifact_changed')
    if not store.settings()['tailored_resumes'] or row['fingerprint'] != digest(_sources(store, job)):
        raise Blocked('tailored_resume_stale', 'Your confirmed history, facts or posting changed. Prepare the application again.')
    data = safe_document(store.root / 'documents' / row['filename'], store.root / 'documents').read_bytes()
    if hashlib.sha256(data).hexdigest() != row['hash']:
        raise Blocked('document_tampered')
    return data
