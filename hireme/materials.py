"""Bounded, private applicant source ingestion; imports never confirm facts."""
from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .util import digest, now, private_dir,restore_imported_blob

KINDS = {'writing_sample', 'cover_letter', 'context'}
ROLES = {'personal', 'style', 'reference'}
MAX_FILE = 20 * 1024 * 1024
MAX_TEXT = 100_000


def extract_text(data: bytes, suffix: str) -> str:
    if not data or len(data) > MAX_FILE:
        raise ValueError('Choose a nonempty file up to 20 MiB')
    if suffix in ('.txt', '.md'):
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise ValueError('Save the text file as UTF-8') from None
    elif suffix == '.pdf':
        from pypdf import PdfReader
        if not data.startswith(b'%PDF-'):
            raise ValueError('Not a PDF')
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 50:
                raise ValueError('Choose an unencrypted PDF with at most 50 pages')
            parts=[];length=0
            for page in reader.pages:
                part=page.extract_text() or '';length+=len(part)
                if length>MAX_TEXT:raise ValueError('PDF text exceeds 100,000 characters; upload a shorter excerpt')
                parts.append(part)
            text = '\n\n'.join(parts)
        except ValueError:
            raise
        except Exception:
            raise ValueError('Cannot read this PDF; try DOCX or plain text') from None
    elif suffix in ('.docx', '.pptx'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                entries = z.infolist()
                if len(entries) > 1000 or sum(e.file_size for e in entries) > 32 * 1024 * 1024:
                    raise ValueError('Document expands beyond the supported size')
                if len({e.filename for e in entries}) != len(entries):
                    raise ValueError('Document contains duplicate entries')
                if any(e.flag_bits & 1 for e in entries):
                    raise ValueError('Encrypted documents are not supported')
                if suffix == '.docx':
                    names = ['word/document.xml']
                else:
                    # Follow presentation relationships rather than ZIP/filename order.
                    root = _xml(z.read('ppt/presentation.xml'))
                    rels = _xml(z.read('ppt/_rels/presentation.xml.rels'))
                    targets = {e.attrib['Id']: e.attrib.get('Target', '') for e in rels
                               if e.attrib.get('TargetMode') != 'External'}
                    names = []
                    for e in root.iter():
                        if e.tag.rsplit('}', 1)[-1] != 'sldId':
                            continue
                        rid = e.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                        target = targets.get(rid, '')
                        if not re.fullmatch(r'(?:/ppt/)?slides/slide\d+\.xml', target):
                            raise ValueError('Unsupported presentation slide relationship')
                        names.append(target.lstrip('/') if target.startswith('/') else 'ppt/' + target)
                paragraphs = []
                for name in names:
                    if z.getinfo(name).file_size > 4 * 1024 * 1024:
                        raise ValueError('Document text part is too large')
                    root = _xml(z.read(name))
                    for p in root.iter():
                        if p.tag.rsplit('}', 1)[-1] == 'p':
                            paragraph = ''.join(t.text or '' for t in p.iter()
                                                if t.tag.rsplit('}', 1)[-1] == 't')
                            if paragraph.strip():
                                paragraphs.append(paragraph.strip())
                text = '\n\n'.join(paragraphs)
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            raise ValueError('Cannot read this document; try PDF or plain text') from None
    else:
        raise ValueError('Use DOCX, PDF, PPTX, TXT or Markdown')
    text = text.strip()
    if not text or '\x00' in text:
        raise ValueError('No readable text found; use selectable text or a text document')
    if len(text) > MAX_TEXT:
        raise ValueError('Extracted text exceeds 100,000 characters; upload a shorter excerpt')
    return text


def _xml(data):
    if re.search(br'<!\s*(?:DOCTYPE|ENTITY)', data, re.I):
        raise ValueError('Document contains unsupported XML declarations')
    return ET.fromstring(data)


def import_material(store, data: bytes, filename: str, kind: str):
    if kind not in KINDS:
        raise ValueError('Choose writing sample, cover letter or supporting context')
    name = filename.replace('\\', '/').rsplit('/', 1)[-1]
    if not name or len(name) > 200 or any(ord(c) < 32 for c in name):
        raise ValueError('Choose a valid filename')
    suffix = Path(name).suffix.lower()
    text = extract_text(data, suffix)
    h = hashlib.sha256(data).hexdigest()
    dest = private_dir(store.root / 'materials') / (h + suffix)
    with store.transaction():
        # The lookup and insert share a transaction, so concurrent imports cannot
        # create duplicate sources or overwrite a reviewed excerpt.
        previous = store.db.execute('SELECT id FROM materials WHERE hash=? AND kind=?', (h, kind)).fetchone()
        missing = not dest.exists()
        repaired = restore_imported_blob(dest, data) or bool(previous and missing)
        if repaired: store.event('material_repaired', h, {'kind': kind, 'hash': h})
        if previous:
            return {**dict(store.db.execute('SELECT * FROM materials WHERE id=?', (previous[0],)).fetchone()), 'existing': True, 'repaired': repaired}
        mid = uuid.uuid4().hex
        store.db.execute('INSERT INTO materials VALUES(?,?,?,?,?,?,?,0,?,1,?,?)',
                         (mid, h, dest.name, name, suffix, kind, text, 'reference', now(), now()))
        store.event('material_imported', mid, {'kind': kind, 'hash': h})
    return {**dict(store.db.execute('SELECT * FROM materials WHERE id=?', (mid,)).fetchone()), 'existing': False, 'repaired': repaired}


def review_material(store, mid: str, text: str, role: str, confirmed: bool):
    if type(confirmed) is not bool or role not in ROLES:
        raise ValueError('Choose how this source can be used and confirm explicitly')
    if not isinstance(text, str) or not 20 <= len(text.strip()) <= 12000:
        raise ValueError('Review an excerpt of 20–12,000 characters')
    text = text.strip()
    with store.transaction():
        row = store.db.execute('SELECT * FROM materials WHERE id=?', (mid,)).fetchone()
        if not row:
            raise ValueError('Source not found')
        if (row['text'], row['role'], bool(row['confirmed'])) == (text, role, confirmed):
            return {'changed': False, 'drafts_removed': 0}
        store.db.execute('UPDATE materials SET text=?,role=?,confirmed=?,revision=revision+1,updated=? WHERE id=?',
                         (text, role, int(confirmed), now(), mid))
        tid = 'material:' + mid
        if confirmed and role == 'personal':
            store.put_template('experience', text, tid)
        else:
            store.db.execute('DELETE FROM templates WHERE id=?', (tid,))
        # Any approved source changes the writing context; all unattempted drafts
        # must be rebuilt. Unapproved excerpt edits have never informed writing.
        removed = store.discard_prepared() if row['confirmed'] or confirmed else 0
        store.event('material_reviewed', mid, {'confirmed': confirmed, 'role': role, 'drafts_removed': removed})
    return {'changed': True, 'drafts_removed': removed}


def writing_context(store):
    """Style/reference documents never become personal factual source IDs."""
    result = {'style_samples': [], 'reference_context': []}
    budgets={'style_samples':12000,'reference_context':8000}
    for r in store.db.execute('SELECT * FROM materials WHERE confirmed=1 ORDER BY updated DESC'):
        item = {'source_id': r['id'], 'revision': r['revision'], 'text': r['text']}
        if r['role'] == 'style' or r['kind'] == 'cover_letter':
            if len(r['text'])<=budgets['style_samples']:
                result['style_samples'].append(item);budgets['style_samples']-=len(r['text'])
        if r['role'] == 'reference':
            if len(r['text'])<=budgets['reference_context']:
                result['reference_context'].append(item);budgets['reference_context']-=len(r['text'])
    return result


def writing_context_hash(store, context):
    revisions = [tuple(r) for r in store.db.execute(
        'SELECT id,revision,role,kind FROM materials WHERE confirmed=1 ORDER BY id')]
    return digest({'posting': {k: context.get(k, '') for k in ('company', 'title', 'description')},
                   'materials': revisions})
