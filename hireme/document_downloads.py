"""Read-only downloads of selected and historically recorded applicant PDFs."""
from __future__ import annotations

import hashlib
import json
import re

from .onboarding import _read_pdf_bytes

NAMES = {'resume': 'resume.pdf', 'transcript': 'transcript.pdf', 'cover_letter': 'cover-letter.pdf'}


def recorded_pdf(store, application_id, index, expected_hash):
    if type(index) is not int or index < 0:
        raise ValueError('Choose a recorded PDF')
    if not isinstance(expected_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_hash):
        raise ValueError('Choose a recorded PDF with a valid content reference')
    record=store.application_record(application_id)
    if not record: raise ValueError('Application record not found')
    try:
        package=json.loads(record['package'])
        documents=package.get('documents',[])
        if not isinstance(documents,list) or index>=len(documents): raise ValueError
        document=documents[index]
        if not isinstance(document,dict): raise ValueError
    except (ValueError,TypeError,AttributeError):
        raise ValueError('Recorded document details could not be read') from None
    kind=document.get('kind'); filename=document.get('filename')
    if not isinstance(kind,str) or kind not in NAMES:
        raise ValueError('This recorded document is not a supported PDF')
    if document.get('hash')!=expected_hash or filename!=expected_hash+'.pdf':
        raise ValueError('The recorded document changed. Reload its evidence before downloading.')
    try: data=_verified_pdf(store,filename,expected_hash)
    except ValueError:
        raise ValueError('The recorded PDF cannot be read or has changed. Restore the original file from a private history backup.') from None
    return data,NAMES[kind]


def _verified_pdf(store,filename,expected_hash):
    parent=store.root/'documents'
    if filename!=expected_hash+'.pdf' or parent.is_symlink(): raise ValueError('PDF storage is unavailable')
    data=_read_pdf_bytes(parent/filename)
    if not data.startswith(b'%PDF-') or hashlib.sha256(data).hexdigest()!=expected_hash:
        raise ValueError('PDF bytes do not match their content reference')
    return data


def selected_pdf(store,kind,expected_hash):
    if kind not in ('resume','transcript') or not isinstance(expected_hash,str) or not re.fullmatch(r'[a-f0-9]{64}',expected_hash):
        raise ValueError('Choose the selected resume or transcript PDF')
    document=store.db.execute('SELECT * FROM documents WHERE kind=?',(kind,)).fetchone()
    if not document: raise ValueError('No selected PDF is available for this document purpose')
    if document['hash']!=expected_hash:
        raise ValueError('Your selected PDF changed. Refresh Your facts before downloading.')
    try: data=_verified_pdf(store,document['filename'],expected_hash)
    except ValueError:
        raise ValueError('The selected PDF is missing or changed. Re-upload the matching original to repair it.') from None
    current=store.db.execute('SELECT * FROM documents WHERE kind=?',(kind,)).fetchone()
    if not current or dict(current)!=dict(document):
        raise ValueError('Your selected PDF changed. Refresh Your facts before downloading.')
    return data,NAMES[kind]
