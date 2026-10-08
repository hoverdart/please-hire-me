"""Employer-scoped cover letters with source revisions and verified PDF output."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from html import escape
from pathlib import Path

from .answers import _validate_writing, resolve
from .materials import writing_context_hash
from .util import Blocked, digest, now, private_dir, safe_document, write_private_blob

FORMAT_VERSION = 1


def render_letter(name, contact, company, title, body):
    from reportlab import __file__ as reportlab_file
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from pypdf import PdfReader

    font_dir = Path(reportlab_file).parent / 'fonts'
    if 'HiremeVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('HiremeVera', str(font_dir / 'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('HiremeVeraBold', str(font_dir / 'VeraBd.ttf')))
    supported=pdfmetrics.getFont('HiremeVera').face.charToGlyph
    if any(ord(c) not in supported for c in name+contact+company+title+body if not c.isspace()):
        raise Blocked('cover_letter_font_unsupported','This letter contains characters unsupported by the installed PDF font')
    normal = ParagraphStyle('body', fontName='HiremeVera', fontSize=10.5, leading=15,
                            spaceAfter=11, textColor=colors.HexColor('#182839'), alignment=TA_LEFT)
    heading = ParagraphStyle('heading', parent=normal, fontName='HiremeVeraBold', fontSize=14, leading=19)
    small = ParagraphStyle('contact', parent=normal, fontSize=9, leading=13)
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=(612, 792), leftMargin=54, rightMargin=54,
                            topMargin=48, bottomMargin=48, title=f'Cover letter — {company}', author=name)
    story = [Paragraph(escape(name), heading)]
    if contact:
        story.append(Paragraph(escape(contact), small))
    story.extend([Spacer(1, 12), Paragraph(escape(company), normal),
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
    if name not in text or company not in text or title not in text:
        raise Blocked('cover_letter_render_failed')
    # Verify body words survived layout/font encoding before any upload.
    words = lambda s: re.sub(r'\s+', ' ', s).strip()
    if words(body) not in words(text):
        raise Blocked('cover_letter_render_failed', 'PDF text differs from the reviewed draft')
    return data


def _fingerprint(store, job):
    return digest({'context': writing_context_hash(store, job), 'facts': store.facts(),
                   'words': store.settings()['cover_letter_words'], 'enabled':store.settings()['cover_letters'],
                   'templates': sorted((t['id'],t['revision']) for t in store.templates()),'format': FORMAT_VERSION})


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
    data = render_letter(name, contact, job['company'], job['title'], answer['value'])
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
