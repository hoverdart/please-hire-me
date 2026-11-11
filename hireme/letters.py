"""Employer-scoped cover letters with source revisions and verified PDF output."""
from __future__ import annotations

import hashlib
import io
import json
import re
from html import escape
from pathlib import Path

from .answers import _validate_writing, resolve
from .materials import writing_context_hash
from .util import Blocked, digest, now, private_dir, safe_document, write_private_blob

FORMAT_VERSION = 2


def render_letter(name, contact, company, title, body, style=None):
    """One page in the applicant's chosen resume style, so both documents read as a set."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import HRFlowable, SimpleDocTemplate, Paragraph, Spacer
    from pypdf import PdfReader
    from .resume_styles import _fonts, _styles, link_target, require_glyphs, validate_style

    style = validate_style(style)
    styles = _styles(style)
    require_glyphs([name, contact, company, title, body, 'Dear Hiring Team, Sincerely,|'], style['font'], 'cover_letter_font_unsupported')
    # Letters are prose: never smaller than 10 pt, with a comfortable measure.
    size = max(10.0, float(style['body_size']))
    normal = ParagraphStyle('letter-body', parent=styles['body'], fontSize=size, leading=size * max(1.4, float(style['line_spacing'])),
                            spaceAfter=size * 1.0)
    margin = max(54.0, float(style['margin']))
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=(612, 792), leftMargin=margin - 6, rightMargin=margin - 6,
                            topMargin=margin - 6, bottomMargin=margin - 6, title=f'Cover letter — {company}', author=name,
                            creator='HireMeMaybe', initialFontName=_fonts(style['font'])[0])
    story = [Paragraph(escape(name), styles['name'])]
    if contact:
        parts = []
        for value in contact.split(' | '):
            target = link_target(value) if '@' in value and ' ' not in value else None
            parts.append(f'<link href="{escape(target, quote=True)}">{escape(value)}</link>' if target else escape(value))
        story.append(Paragraph(' | '.join(parts), styles['contact']))
    if style['rule']:
        story.append(HRFlowable(width=612 - 2 * margin, thickness=0.6, color=styles['accent'], spaceBefore=2, spaceAfter=2, hAlign='LEFT'))
    story.extend([Spacer(1, 14), Paragraph(escape(company), normal),
                  Paragraph(escape(title), normal), Spacer(1, 5), Paragraph('Dear Hiring Team,', normal)])
    for paragraph in re.split(r'\n\s*\n', body.strip()):
        story.append(Paragraph(escape(paragraph).replace('\n', ' '), normal))
    story.extend([Paragraph('Sincerely,', normal), Paragraph(escape(name), normal)])
    doc.build(story)
    data = stream.getvalue()
    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) != 1:
        raise Blocked('cover_letter_too_long', 'Generated letter must fit on one page')
    text = reader.pages[0].extract_text()
    words = lambda s: re.sub(r'\s+', ' ', s).strip()
    if any(words(value) not in words(text) for value in (name, company, title)):
        raise Blocked('cover_letter_render_failed')
    # Verify body words survived layout/font encoding before any upload.
    if words(body) not in words(text):
        raise Blocked('cover_letter_render_failed', 'PDF text differs from the reviewed draft')
    return data


def _fingerprint(store, job):
    return digest({'context': writing_context_hash(store, job), 'facts': store.facts(),
                   'words': store.settings()['cover_letter_words'], 'enabled':store.settings()['cover_letters'],
                   'templates': sorted((t['id'],t['revision']) for t in store.templates()),'format': FORMAT_VERSION,
                   'style': store.settings()['resume_style']})


def generate_cover_letter(store, job, provider):
    store.checkpoint()
    if not store.settings()['cover_letters'] or not store.settings()['tailored_writing']:
        raise Blocked('cover_letter_not_enabled', 'Enable cover letters and tailored writing in Preferences')
    fingerprint = _fingerprint(store, job)
    row = store.db.execute('SELECT * FROM generated_documents WHERE job_id=? AND kind=?',
                           (job['id'], 'cover_letter')).fetchone()
    if row and row['fingerprint'] == fingerprint:
        document = {'kind': 'cover_letter', 'hash': row['hash'], 'filename': row['filename'],
                    'generated': True, 'job_id': job['id']}
        validate_generated_document(store, job, document)
        from .application_artifacts import record_cover_letter
        record_cover_letter(store,job,document,json.loads(row['provenance']),fingerprint)
        return document
    facts = store.facts()
    name = facts.get('full_name', {}).get('value', '')
    if not name:
        raise Blocked('missing_fact', 'Confirm your full name before generating a cover letter')
    contact = ' | '.join(facts[k]['value'] for k in ('email', 'phone', 'location') if k in facts)
    field = {'label': f"Why do you want to work at {job['company']} as {job['title']}? "
             f"Write the body of a cover letter in 3 short paragraphs, at most {store.settings()['cover_letter_words']} words. "
             "Connect your documented experience to this posting. Omit greetings, signatures and contact details.",
             'type': 'textarea', 'required': True, 'options': [], 'maxlength': 4000}
    answer = resolve(store, job.get('answer_scope', job['host']), field, provider, context=job)
    if not answer['provenance'].get('tailored'):
        raise Blocked('cover_letter_draft_failed', 'No grounded tailored draft was produced')
    data = render_letter(name, contact, job['company'], job['title'], answer['value'], store.settings()['resume_style'])
    store.checkpoint()
    if fingerprint != _fingerprint(store, job):
        raise Blocked('cover_letter_sources_changed')
    h = hashlib.sha256(data).hexdigest()
    dest = private_dir(store.root / 'documents') / (h + '.pdf')
    write_private_blob(dest,data)
    with store.transaction():
        store.db.execute('INSERT INTO generated_documents VALUES(?,?,?,?,?,?,?) '
                         'ON CONFLICT(job_id,kind) DO UPDATE SET hash=excluded.hash,filename=excluded.filename,'
                         'fingerprint=excluded.fingerprint,provenance=excluded.provenance,created=excluded.created',
                         (job['id'], 'cover_letter', h, dest.name, fingerprint, json.dumps(answer), now()))
        store.event('cover_letter_generated', job['id'], {'hash': h, 'words': len(answer['value'].split())})
        from .application_artifacts import record_cover_letter
        record_cover_letter(store,job,{'hash':h,'filename':dest.name},answer,fingerprint)
    return {'kind': 'cover_letter', 'hash': h, 'filename': dest.name, 'generated': True, 'job_id': job['id']}


def validate_generated_document(store, job, document):
    row = store.db.execute('SELECT * FROM generated_documents WHERE job_id=? AND kind=?',
                           (job['id'], document['kind'])).fetchone()
    if not row or document.get('job_id') != job['id'] or row['fingerprint'] != _fingerprint(store, job):
        raise Blocked('cover_letter_stale')
    if row['hash'] != document['hash'] or row['filename'] != document['filename']:
        raise Blocked('document_changed')
    answer = json.loads(row['provenance'])
    _validate_writing(store, answer)
    if answer['provenance'].get('context_hash') != writing_context_hash(store, job):
        raise Blocked('cover_letter_stale')
    path = safe_document(store.root / 'documents' / row['filename'], store.root / 'documents')
    if hashlib.sha256(path.read_bytes()).hexdigest() != row['hash']:
        raise Blocked('document_tampered')
