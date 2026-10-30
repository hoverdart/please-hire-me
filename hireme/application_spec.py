"""Immutable preparation contract shared by the browser ATS adapters.

This package contains private approved data in memory. It is not a receipt or
transport grant. Each adapter still validates native paths and acknowledgements.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .util import Blocked, application_identity, digest, safe_document

SUPPORTED = frozenset({'text','textarea','email','tel','url','date','number',
    'combobox','select','radio','checkbox','yesno','checkbox-group','file','ashby_education'})


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def question_identity(field):
    """Native identity first; a unique accessible identity otherwise, never index."""
    if field.get('ashby_entry_id'):
        return 'ashby:'+field['ashby_entry_id']+(':'+field['ashby_texting_consent'] if field.get('ashby_texting_consent') else '')
    ref=field.get('ref',{})
    if ref.get('id'):return 'id:'+ref['id']
    if ref.get('name'):return 'name:'+ref['name']
    label=field.get('label')
    if not isinstance(label,str):raise Blocked('unsupported_form','The visible question has no readable identity')
    return 'label:'+label


@dataclass(frozen=True)
class Question:
    identity: str
    context: str
    required: bool
    kind: str
    choices: tuple
    native_values: tuple
    rules: str
    dependencies: str


@dataclass(frozen=True)
class ApplicationSpec:
    posting: tuple
    questions: tuple[Question,...]
    documents: str


@dataclass(frozen=True)
class ApprovedApplication:
    specification: ApplicationSpec
    package_json: str
    source_revision: str

    def package(self):
        return json.loads(self.package_json)


def plan(store, job, package):
    from .answers import validate_package
    from .approval_boundary import source_fingerprint
    validate_package(store,job,package)
    questions=[];seen=set()
    for step_number,step in enumerate(package.get('steps',[])):
        for field in step.get('fields',[]):
            kind=field.get('type');identity=question_identity(field)
            key=(step_number,identity)
            if kind not in SUPPORTED:
                raise Blocked('unsupported_form',(field.get('label') or 'Control')+' — unsupported visible control')
            if key in seen or identity=='label:':
                raise Blocked('unsupported_form','The application has no unique question locator')
            seen.add(key)
            options=field.get('options',[]);native=field.get('option_values',[])
            if (not isinstance(options,list) or any(not isinstance(v,str) for v in options)
                    or not isinstance(native,list) or any(not isinstance(v,str) for v in native)
                    or len(set(options))!=len(options) or native and len(native)!=len(options)):
                raise Blocked('form_changed','The employer choices are ambiguous')
            questions.append(Question(identity,encoded({k:field.get(k) for k in ('label','help_text','help_links','ashby_path')}),
                bool(field.get('required')),kind,tuple(options),tuple(native),
                encoded({k:field.get(k) for k in ('min','max','step','maxlength','pattern','multiple')}),
                encoded(field.get('conditional_dependencies',[]))))
    for document in package.get('documents',[]):
        try:
            path=safe_document(store.root/'documents'/document['filename'],store.root/'documents')
            checksum=hashlib.sha256(path.read_bytes()).hexdigest()
            if checksum!=document['hash']:raise ValueError('hash')
        except (KeyError,TypeError,ValueError,OSError):
            raise Blocked('document_tampered','Validate every approved document before uploading') from None
    spec=ApplicationSpec(application_identity(job['url']),tuple(sorted(questions,key=lambda q:(q.identity,q.context))),
        encoded([{k:d.get(k) for k in ('kind','hash','field')} for d in package.get('documents',[])]))
    return ApprovedApplication(spec,encoded(package),source_fingerprint(store))
