"""Immutable, source-bound application writing and narrative PDFs."""
from __future__ import annotations

import hashlib
import io
import json
import re
from html import escape

from .answers import resolve, _validate_writing
from .materials import writing_context_hash
from .util import Blocked, digest, now, private_dir, safe_document, write_private_blob

KINDS = {'introduction', 'supplemental_response', 'profile_suggestion', 'cover_letter'}


def record_cover_letter(store, job, document, answer, source_fingerprint):
    """Keep the existing cover-letter contract, with an immutable revision index."""
    with store.transaction():
        row = store.db.execute("SELECT * FROM application_artifacts WHERE job_id=? AND requirement_key='cover_letter' ORDER BY revision DESC LIMIT 1", (job['id'],)).fetchone()
        if row and row['hash'] == document['hash'] and row['fingerprint'] == source_fingerprint:
            return public_artifact(row)
        revision = row['revision'] + 1 if row else 1
        identity = digest([job['id'], 'cover_letter', revision, document['hash'], source_fingerprint])
        provenance = {'answer':answer, 'prompt':answer['field']['label'], 'context':job}
        store.db.execute('INSERT INTO application_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (identity,job['id'],'cover_letter','cover_letter',revision,document['hash'],document['filename'],answer['value'],json.dumps(provenance),source_fingerprint,now()))
        return public_artifact(store.db.execute('SELECT * FROM application_artifacts WHERE id=?',(identity,)).fetchone())


def fingerprint(store, job, kind, prompt):
    return digest({'context': writing_context_hash(store, job), 'facts': store.facts(),
        'documents': [tuple(row) for row in store.db.execute('SELECT kind,hash FROM documents ORDER BY kind')],
        'kind': kind, 'prompt': prompt, 'format': 1,
        'tailored_writing': store.settings()['tailored_writing']})


def render_response(name, company, title, prompt, body):
    from reportlab import __file__ as reportlab_file
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from pypdf import PdfReader
    from pathlib import Path
    if 'HiremeResponseVera' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('HiremeResponseVera', str(Path(reportlab_file).parent / 'fonts' / 'Vera.ttf')))
    supported = pdfmetrics.getFont('HiremeResponseVera').face.charToGlyph
    if any(ord(c) not in supported for c in name + company + title + prompt + body if not c.isspace()):
        raise Blocked('document_font_unsupported', 'This response contains unsupported PDF characters.')
    style = ParagraphStyle('response', fontName='HiremeResponseVera', fontSize=10.5, leading=15, spaceAfter=12)
    heading = ParagraphStyle('title', parent=style, fontSize=14, leading=19)
    stream = io.BytesIO()
    document = SimpleDocTemplate(stream, pagesize=(612, 792), leftMargin=54, rightMargin=54,
        topMargin=48, bottomMargin=48, title=f'Application response — {company}', author=name)
    story = [Paragraph(escape(name), heading), Paragraph(escape(company + ' · ' + title), style), Spacer(1, 10),
             Paragraph(escape(prompt), style)]
    story.extend(Paragraph(escape(p).replace('\n', '<br/>'), style) for p in body.split('\n\n') if p.strip())
    document.build(story)
    data = stream.getvalue()
    reader = PdfReader(io.BytesIO(data))
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    if len(reader.pages) != 1 or ' '.join(body.split()) not in ' '.join(text.split()):
        raise Blocked('document_render_failed', 'The response must fit on one page without losing text.')
    if len(data) > 1024 * 1024:
        raise Blocked('document_too_large', 'Handshake documents must be at most 1 MB.')
    return data


def validate_links(answer, store=None):
    # A model's factual review cannot authorize a new project URL.
    links = lambda text: set(re.findall(r'https?://[^\s<>"\']+',text))
    normalize = lambda values: {value.rstrip('.,;:!?)') for value in values}
    supported = normalize(links(' '.join(part.get('text','') for part in answer.get('provenance',{}).get('sample_parts',[]))))
    if store is not None:
        provenance=answer.get('provenance',{})
        fact=store.facts().get(provenance.get('fact_key'))
        if fact and fact['revision']==provenance.get('revision'):
            supported |= normalize(links(fact['value']))
    if not normalize(links(answer['value'])) <= supported:
        raise Blocked('writing_link_unverified','Confirm project links in an approved factual source before including them in application materials.')


def generate(store, job, kind, prompt, provider=None, requirement_key=None):
    if kind == 'cover_letter':
        from .letters import generate_cover_letter
        if provider is None:
            from .provider import ManagedProvider
            provider = ManagedProvider(store,store.settings()['model_timeout_seconds'])
        generate_cover_letter(store,job,provider)
        return public_artifact(store.db.execute("SELECT * FROM application_artifacts WHERE job_id=? AND kind='cover_letter' ORDER BY revision DESC LIMIT 1",(job['id'],)).fetchone())
    if not isinstance(kind,str) or kind not in KINDS or not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000:
        raise ValueError('Choose an application writing purpose and a prompt up to 4,000 characters')
    if not store.settings()['tailored_writing']:
        raise Blocked('writing_upgrade_needed', 'Enable source-grounded tailored writing in Preferences.')
    if kind == 'supplemental_response' and any(word in prompt.casefold() for word in
        ('official transcript', 'test solution', 'coding assessment', 'work sample', 'certificate', 'government id')):
        raise Blocked('supplied_document_required', 'Provide the requested original document or work sample.')
    store.checkpoint()
    requirement_key = requirement_key or kind
    fp = fingerprint(store, job, kind, prompt)
    row = store.db.execute('SELECT * FROM application_artifacts WHERE job_id=? AND requirement_key=? ORDER BY revision DESC LIMIT 1',
                           (job['id'], requirement_key)).fetchone()
    if row and row['fingerprint'] == fp:
        result = public_artifact(row)
        validate(store, job, result)
        return result
    if provider is None:
        from .provider import ManagedProvider
        provider = ManagedProvider(store, store.settings()['model_timeout_seconds'])
    field = {'label': prompt, 'type': 'textarea', 'required': True, 'options': [], 'maxlength': 4000}
    try:
        answer = resolve(store, job.get('answer_scope', job['host']), field, provider, context=job)
    except Blocked as error:
        if not job['id'].startswith('profile:'):
            store.ask(job['id'], job.get('answer_scope', job['host']), field['label'], [], error.reason, field=field, context=job)
        raise
    if not answer.get('provenance'):
        raise Blocked('writing_unsupported', 'No supported answer was produced.')
    validate_links(answer,store)
    content = answer['value']
    data = render_response(store.facts()['full_name']['value'], job['company'], job['title'], prompt, content) if kind == 'supplemental_response' else content.encode()
    h = hashlib.sha256(data).hexdigest()
    filename = h + ('.pdf' if kind == 'supplemental_response' else '.txt')
    store.checkpoint()
    if fingerprint(store, job, kind, prompt) != fp:
        raise Blocked('artifact_sources_changed', 'Your sources changed during generation. Generate again.')
    write_private_blob(private_dir(store.root / 'documents') / filename, data)
    with store.transaction():
        latest = store.db.execute('SELECT MAX(revision) FROM application_artifacts WHERE job_id=? AND requirement_key=?',
                                  (job['id'], requirement_key)).fetchone()[0] or 0
        revision = latest + 1
        identity = digest([job['id'], requirement_key, revision, h, fp])
        provenance = {'answer': answer, 'prompt': prompt, 'context': job}
        store.db.execute('INSERT INTO application_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (identity, job['id'], requirement_key, kind, revision, h, filename, content, json.dumps(provenance), fp, now()))
        store.event('application_artifact_generated', job['id'], {'artifact_id': identity, 'kind': kind, 'hash': h})
    return public_artifact(store.db.execute('SELECT * FROM application_artifacts WHERE id=?', (identity,)).fetchone())


def public_artifact(row):
    return {key: row[key] for key in ('id', 'job_id', 'requirement_key', 'kind', 'revision', 'hash', 'filename', 'content', 'created')}


def validate(store, job, artifact):
    row = store.db.execute('SELECT * FROM application_artifacts WHERE id=?', (artifact.get('artifact_id', artifact.get('id')),)).fetchone()
    if not row or row['job_id'] != job['id'] or row['hash'] != artifact.get('hash') or row['filename'] != artifact.get('filename'):
        raise Blocked('artifact_changed')
    provenance = json.loads(row['provenance'])
    if row['kind'] == 'cover_letter':
        from .letters import validate_generated_document
        validate_generated_document(store,job,{'job_id':job['id'],'kind':'cover_letter','hash':row['hash'],'filename':row['filename']})
        return safe_document(store.root / 'documents' / row['filename'],store.root / 'documents').read_bytes()
    if row['fingerprint'] != fingerprint(store, job, row['kind'], provenance['prompt']):
        raise Blocked('stale_writing_context')
    answer = provenance['answer']
    validate_links(answer,store)
    if 'sample_parts' in answer['provenance']:
        _validate_writing(store, answer)
    else:
        if resolve(store, job.get('answer_scope', job['host']), answer['field'], context=job) != answer:
            raise Blocked('unsupported_or_stale_answer')
    directory = store.root / 'documents'
    path = directory / row['filename']
    if row['kind'] == 'supplemental_response':
        data = safe_document(path, directory).read_bytes()
    else:
        if path.is_symlink() or not re.fullmatch(r'[a-f0-9]{64}\.txt', path.name) or path.resolve().parent != directory.resolve():
            raise Blocked('document_tampered')
        data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row['hash']:
        raise Blocked('document_tampered')
    return data


def library(store, job_id=None, offset=0):
    if type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid material page')
    clause, args = ('WHERE a.job_id=?', [job_id]) if job_id else ('', [])
    total = store.db.execute('SELECT COUNT(*) FROM application_artifacts a ' + clause, args).fetchone()[0]
    rows = store.db.execute('SELECT a.*,j.company,j.title FROM application_artifacts a LEFT JOIN jobs j ON j.id=a.job_id ' + clause + ' ORDER BY a.created DESC,a.id LIMIT 25 OFFSET ?', [*args, offset])
    return {'artifacts': [{**public_artifact(row), 'company': row['company'], 'title': row['title']} for row in rows],
            'total': total, 'offset': offset, 'limit': 25}


def download(store, artifact_id, expected_hash):
    from .document_downloads import _verified_pdf
    row = store.db.execute('SELECT * FROM application_artifacts WHERE id=?', (artifact_id,)).fetchone()
    if not row or row['hash'] != expected_hash or row['kind'] not in {'supplemental_response','cover_letter'}:
        raise ValueError('Choose an existing response PDF with its content reference')
    # Historical downloads validate the recorded bytes, not today's source revisions.
    return _verified_pdf(store, row['filename'], expected_hash), 'cover-letter.pdf' if row['kind']=='cover_letter' else 'application-response.pdf'
