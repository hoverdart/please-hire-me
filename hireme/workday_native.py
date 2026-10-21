"""NVIDIA request/response primitives reviewed in the official public client.

This module does not install authenticated execution authority. The source proves
request builders and client success tests; live owner/profile-bound operation
acknowledgements and a fresh receipt are required to commission the installed
bounded NVIDIA driver for ordinary scheduling.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from urllib.parse import parse_qsl, urlsplit

from .util import Blocked, digest
from .workday_profiles import NVIDIA, NVIDIA_SITE, PUBLIC_CLIENT_EVIDENCE


SESSION_HEADER = 'X-Cxs-Session-Id'
PAGES = {
    '5eff652ad12a10000ef9477b27420000': 'My Information',
    '5eff652ad12a10000f1297b7988e0000': 'My Experience',
    '9737f85c149f10001235035c54060000': 'Availability',
    '5eff652ad12a10000f1c2d323fb00000': 'Application Questions',
    '5eff652ad12a10000f2b959dfeb90000': 'Application Questions 2',
    '5eff652ad12a10000f34b78ac38f0000': 'Application Questions 3',
    '5eff652ad12a10000f402dc5a03c0000': 'Voluntary Disclosures',
    '5eff652ad12a10000f45aaae21610000': 'Self Identification',
    '5eff652ad12a10000f4fb1cc55060000': 'Review',
}
QUESTION_TYPES = {
    '85fb160ed47910001589e3a427670070': 'date',
    'c4d9bd57996c100025ab7d705b150045': 'multiple',
    '53d73448d8de1000063827b5cb68003f': 'number',
    'c4d9bd57996c10002583f04b9fc30042': 'single',
    'c4d9bd57996c100025ab7d7f5aed0046': 'text',
}
RESOURCES = {'name', 'address', 'emailaddress', 'phonenumber', 'source', 'previousworker',
             'workexperiences', 'educations', 'webaddresses', 'resumeattachments', 'selfidentify'}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._~-]{1,300}', value):
        raise Blocked('workday_unapproved_operation', 'The native identifier is unrecognized')
    return value


def encoded(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode()


def native_url(service, endpoint, *, site=False, old=False):
    if service not in {'candidatehome', 'jobapplication', 'common'}:
        raise Blocked('workday_unapproved_operation')
    if endpoint.startswith('/') or '#' in endpoint or '\\' in endpoint or '..' in endpoint.split('/'):
        raise Blocked('workday_unapproved_operation')
    base = '/wday/cxs' if old else '/wday/calypso/cxs/'+service
    return f'https://{NVIDIA}{base}/nvidia/'+(NVIDIA_SITE+'/' if site else '')+endpoint


def checked_object(data):
    if not isinstance(data, dict) or data.get('errors') or data.get('error') or data.get('commonRequestId'):
        raise Blocked('remote_draft_uncertain', 'The employer response contains an error or an unknown shape')
    return data


def pages(data):
    values = checked_object(data).get('data')
    if not isinstance(values, list) or not values or len(values) > 12:
        raise Blocked('multi_step_requires_adapter', 'The official flow pages are unrecognized')
    ids = [page.get('id') for page in values if isinstance(page, dict)]
    if (len(ids) != len(values) or any(not isinstance(value,str) for value in ids)
            or len(ids) != len(set(ids)) or any(value not in PAGES for value in ids)
            or PAGES[ids[-1]] != 'Review'):
        raise Blocked('multi_step_requires_adapter', 'Sign-in, assessment or unknown application pages need manual completion')
    return [{'id': page['id'], 'section': PAGES[page['id']], 'definition': page} for page in values]


def draft_created(data):
    value = checked_object(data)
    if set(value)-{'id', 'sessionId'}:
        raise Blocked('remote_draft_uncertain', 'The draft creation acknowledgement changed')
    remote = identifier(value.get('id'))
    session = value.get('sessionId')
    if not isinstance(session, str) or not session or len(session) > 4096:
        raise Blocked('remote_draft_uncertain', 'The draft session acknowledgement is missing')
    return remote, session


def contains_saved(actual, expected):
    """Require every approved wire value; additional native metadata proves nothing."""
    if isinstance(expected, dict):
        return (isinstance(actual, dict) and all(key in actual and contains_saved(actual[key], value)
                                               for key, value in expected.items()))
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(contains_saved(a, b) for a, b in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def saved_record(data, body, record_id=None):
    value = checked_object(data)
    remote = identifier(value.get('id'))
    if record_id is not None and remote != record_id or not contains_saved(value, body):
        raise Blocked('remote_draft_uncertain', 'The saved native values do not match the approved payload')
    return remote


def questionnaire_payload(questionnaire, answers):
    questions = checked_object(questionnaire).get('questions')
    if not isinstance(questions, list) or not isinstance(answers, dict):
        raise Blocked('workday_unapproved_operation')
    if any(not isinstance(question,dict) or not isinstance(question.get('id'),str) for question in questions):
        raise Blocked('workday_unapproved_operation')
    by_id = {question['id']: question for question in questions}
    if len(by_id) != len(questions) or set(answers)-set(by_id):
        raise Blocked('workday_unapproved_operation', 'Questionnaire identities are ambiguous')
    result = []
    for question_id, value in answers.items():
        identifier(question_id)
        question = by_id[question_id]
        question_type=question.get('type')
        if not isinstance(question_type,dict) or not isinstance(question_type.get('id'),str):raise Blocked('unsupported_widget')
        kind = QUESTION_TYPES.get(question_type['id'])
        item = {'questionItem': {'id': question_id}}
        if kind == 'text' and isinstance(value, str) and value:
            item['answerText'] = value
        elif kind == 'date' and isinstance(value, str):
            from datetime import date
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise Blocked('invalid_date_answer') from None
            item['answerDate'] = value
        elif kind == 'number' and type(value) is int and value > 0:
            # The native client drops zero/negative numeric answers. Hold them
            # instead of claiming the employer saved a value it omitted.
            item['answerNumeric'] = value
        elif kind in {'single', 'multiple'}:
            values = [value] if kind == 'single' else value
            options = question.get('possibleAnswers')
            if (not isinstance(values, list) or not values or any(not isinstance(choice,str) for choice in values)
                    or len(values) != len(set(values))
                    or not isinstance(options, list)):
                raise Blocked('mapping_review')
            ids = [option.get('id') for option in options if isinstance(option, dict)]
            if (len(ids) != len(options) or any(not isinstance(choice,str) for choice in ids)
                    or len(ids) != len(set(ids)) or any(choice not in ids for choice in values)):
                raise Blocked('mapping_review')
            item['questionMultipleChoiceAnswers'] = [{'id': identifier(choice)} for choice in values]
        else:
            raise Blocked('unsupported_widget', 'The native questionnaire answer encoding needs review')
        result.append(item)
    return {'questionnaireAnswers': result}


def questionnaire_saved(data, payload):
    value = checked_object(data)
    identifier(value.get('id'))
    answers = value.get('questionnaireAnswers')
    if not isinstance(answers, list):
        raise Blocked('remote_draft_uncertain')
    by_id = {}
    for answer in answers:
        if not isinstance(answer, dict):
            raise Blocked('remote_draft_uncertain')
        question_item=answer.get('questionItem')
        key=question_item.get('id') if isinstance(question_item,dict) else None
        if not isinstance(key,str) or not key or key in by_id:
            raise Blocked('remote_draft_uncertain')
        by_id[key] = answer
    if set(by_id) != {answer['questionItem']['id'] for answer in payload['questionnaireAnswers']}:
        raise Blocked('remote_draft_uncertain')
    for expected in payload['questionnaireAnswers']:
        if not contains_saved(by_id[expected['questionItem']['id']], expected):
            raise Blocked('remote_draft_uncertain')
    return value['id']


def upload_saved(data, *, filename, length, content_type):
    value = checked_object(data)
    remote = identifier(value.get('id'))
    expected = {'fileName': filename, 'fileLength': length, 'contentType': {'id': 'Content_Type_ID='+content_type}}
    if type(length) is not int or length <= 0 or not contains_saved(value, expected):
        raise Blocked('upload_verification_failed')
    return {'file': remote, **expected}


def finalized(data, remote_id, status):
    value = checked_object(data)
    if status != 200 or set(value)!={'id'} or value.get('id') != remote_id:
        raise Blocked('submission_unknown', 'The native final acknowledgement is unverified')
    return remote_id


@dataclass(frozen=True)
class NativeOperation:
    url: str
    method: str
    body: bytes
    headers: dict
    purpose: str


def record_operation(remote_id, session_id, resource, body, *, record_id=None, definition=False):
    if resource not in RESOURCES or not isinstance(body, dict):
        raise Blocked('workday_unapproved_operation')
    endpoint = f'jobapplication/{identifier(remote_id)}/{resource}'
    if record_id is not None:
        endpoint += '/'+identifier(record_id)
    headers = {'accept': 'application/json', 'content-type': 'application/json', SESSION_HEADER: session_id}
    if definition:
        if record_id is not None:
            raise Blocked('workday_unapproved_operation')
        headers['form-definition'] = 'true'
    return NativeOperation(native_url('jobapplication', endpoint), 'PUT' if record_id else 'POST',
                           encoded(body), headers, 'definition_read' if definition else 'draft_write')


def finalize_operation(remote_id, session_id):
    # Ordinary manual applications with no SMS/WhatsApp/HiredScore opt-in data.
    # Agency and communication-preference extensions require their own review.
    return NativeOperation(native_url('jobapplication', f'jobapplication/{identifier(remote_id)}/finalize', old=True),
        'POST', b'{}', {'accept': 'application/json', 'content-type': 'application/json', SESSION_HEADER: session_id}, 'final_submit')


SOURCE_SIGNATURE = digest({'version': 1, 'public_client': PUBLIC_CLIENT_EVIDENCE,
                           'pages': PAGES, 'question_types': QUESTION_TYPES, 'resources': sorted(RESOURCES)})


class NativeReadScope:
    """An authenticated definition read is scoped to one owned native draft."""
    def __init__(self,store,session,remote_id,session_id,*,questionnaires=None,boundary=None,resume_proof=None):
        self.store,self.session=store,session
        self.remote_id,self.session_id=identifier(remote_id),session_id
        self.questionnaires=dict(questionnaires or {})
        resumed=(isinstance(resume_proof,dict) and set(resume_proof)=={'remote_id','owner_hash','profile_binding','read_hash'}
            and resume_proof['remote_id']==remote_id and resume_proof['owner_hash']==session.owner_hash
            and resume_proof['profile_binding']==session.profile_binding
            and re.fullmatch(r'[a-f0-9]{64}',str(resume_proof['read_hash'])))
        if (not session.current(store) or session.host!=NVIDIA or not isinstance(session_id,str) or not session_id and not resumed
                or len(session_id)>4096 or re.search(r'[\x00-\x1f\x7f]',session_id)
                or set(self.questionnaires)-{'primary','secondary','supplementary'}):
            raise Blocked('workday_session_unverified')
        for value in self.questionnaires.values():identifier(value)
        self.boundary=boundary

    def allow(self,url,method,payload,headers):
        if not self.session.current(self.store):return False
        if self.boundary is not None:self.boundary.check()
        lower={key.lower():value for key,value in headers.items()}
        if (lower.get('form-definition')!='true' or lower.get(SESSION_HEADER.lower())!=self.session_id
                or method not in {'POST','PUT'}):return False
        try:body=json.loads(payload)
        except (ValueError,TypeError,UnicodeError):return False
        if not isinstance(body,dict):return False
        for resource in RESOURCES:
            expected=record_operation(self.remote_id,self.session_id,resource,{},definition=True)
            if url==expected.url and method==expected.method:
                return body=={} or resource=='workexperiences' and type(body.get('currentlyWorkHere')) is bool and set(body)=={'currentlyWorkHere'}
        for resource in ('termsandconditions','uspersonalinfo'):
            endpoint=native_url('jobapplication',f'jobapplication/{self.remote_id}/{resource}',old=True)
            if url==endpoint and method=='PUT':return body=={}
        for kind,questionnaire in self.questionnaires.items():
            endpoint=native_url('common',f'questionnaire/{questionnaire}/definition?type={kind}')
            if url==endpoint and method=='POST':return body=={}
        return False


def native_read(host,url,method,payload,headers,*,scope=None):
    """No blanket POST exemption: a live owner/draft scope is mandatory."""
    if host!=NVIDIA:return False
    from .workday_profiles import native_profile_read
    if native_profile_read(host,url,method,payload):return True
    return bool(scope and scope.allow(url,method,payload,headers))
