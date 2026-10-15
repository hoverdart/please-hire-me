import hashlib
from types import SimpleNamespace

import pytest

from hireme.browser import Browser
from hireme.lever_resume import LeverResume
from hireme.util import Blocked

ACCOUNT='11111111-2222-3333-4444-555555555555'
PDF=b'%PDF-1.4\nApproved binary resume\x00\xff\n%%EOF'
HASH=hashlib.sha256(PDF).hexdigest()


def multipart(*,blob=PDF,account=ACCOUNT,filename=HASH+'.pdf',extra=b''):
    return (b'--boundary\r\nContent-Disposition: form-data; name="resume"; filename="'+filename.encode()+
            b'"\r\nContent-Type: application/pdf\r\n\r\n'+blob+
            b'\r\n--boundary\r\nContent-Disposition: form-data; name="accountId"\r\n\r\n'+account.encode()+
            b'\r\n'+extra+b'--boundary--\r\n')


def setup_upload(store,monkeypatch,host='jobs.lever.co'):
    monkeypatch.setattr('hireme.browser.public_host',lambda host:True)
    browser=Browser(store);browser.current_host=host;browser.upload_payloads={HASH:PDF}
    values={'accountId':ACCOUNT,'resumeStorageId':''}
    def locator(selector):
        name='accountId' if 'accountId' in selector else 'resumeStorageId'
        return SimpleNamespace(count=lambda:1,input_value=lambda:values[name])
    browser.page=SimpleNamespace(locator=locator)
    browser.lever_resume=LeverResume(browser,{'hash':HASH})
    request=SimpleNamespace(url='https://'+host+'/parseResume',method='POST',post_data='',
        post_data_buffer=multipart(),headers={'content-type':'multipart/form-data; boundary=boundary'})
    route=SimpleNamespace(request=request,action=None)
    route.abort=lambda:setattr(route,'action','abort')
    def continue_(**overrides):
        route.action='continue';route.overrides=overrides
    route.continue_=continue_
    return browser,route,values


@pytest.mark.parametrize('host',['jobs.lever.co','jobs.eu.lever.co'])
@pytest.mark.parametrize('omitted_bytes',[False,True])
def test_lever_native_resume_upload_requires_matching_storage_acknowledgement(store,monkeypatch,host,omitted_bytes):
    browser,route,values=setup_upload(store,monkeypatch,host)
    if omitted_bytes:route.request.post_data_buffer=multipart(blob=b'')
    browser._route(route)
    assert route.action=='continue' and not browser.submission_requests
    from email import policy
    from email.parser import BytesParser
    payload=route.overrides['post_data'];content_type=route.request.headers['content-type']
    body=BytesParser(policy=policy.default).parsebytes(b'Content-Type: '+content_type.encode()+b'\r\n\r\n'+payload)
    parts=list(body.iter_parts())
    assert len(parts)==2 and parts[0].get_payload(decode=True)==PDF
    assert parts[1].get_payload(decode=True)==ACCOUNT.encode()
    # Some Playwright wrappers report the continued body on the response.
    if omitted_bytes:route.request.post_data_buffer=payload
    with pytest.raises(Blocked,match='upload_verification_failed'):browser.lever_resume.verify()
    response=SimpleNamespace(request=route.request,status=200,json=lambda:{'resumeStorageId':'storage-fixture'})
    browser._upload_response(response)
    assert HASH in browser.uploaded_files
    with pytest.raises(Blocked,match='upload_verification_failed'):browser.lever_resume.verify()
    values['resumeStorageId']='storage-fixture';browser.lever_resume.verify()
    values['accountId']='99999999-2222-3333-4444-555555555555'
    with pytest.raises(Blocked,match='upload_verification_failed'):browser.lever_resume.verify()
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()


@pytest.mark.parametrize('change',[
    {'post_data_buffer':multipart(blob=PDF+b'unapproved')},
    {'post_data_buffer':multipart(account='99999999-2222-3333-4444-555555555555')},
    {'post_data_buffer':multipart(filename='unapproved.pdf')},
    {'post_data_buffer':multipart(extra=b'--boundary\r\nContent-Disposition: form-data; name="email"\r\n\r\nprivate@example.invalid\r\n')},
    {'post_data_buffer':multipart(extra=b'--boundary\r\nContent-Disposition: form-data; name="accountId"\r\n\r\nother-account\r\n')},
    {'post_data_buffer':multipart().replace(b'Content-Type: application/pdf',b'Content-Type: application/pdf\r\nContent-Transfer-Encoding: binary')},
    {'url':'https://jobs.lever.co/parseResume?extra=unapproved'},
    {'url':'https://jobs.eu.lever.co/parseResume'},
    {'method':'PUT'},
])
def test_lever_parser_never_grants_other_payloads_or_accounts(store,monkeypatch,change):
    browser,route,values=setup_upload(store,monkeypatch)
    for key,value in change.items():setattr(route.request,key,value)
    browser._route(route)
    assert route.action=='abort' and not browser.uploaded_files


@pytest.mark.parametrize('status,result',[(200,{}),(200,{'resumeStorageId':None}),(200,{'resumeStorageId':'bad\nvalue'}),(500,{'resumeStorageId':'storage-fixture'})])
def test_lever_parser_http_success_alone_is_not_an_upload_ack(store,monkeypatch,status,result):
    browser,route,values=setup_upload(store,monkeypatch)
    browser._route(route)
    browser._upload_response(SimpleNamespace(request=route.request,status=status,json=lambda:result))
    assert not browser.uploaded_files
    with pytest.raises(Blocked,match='upload_verification_failed'):browser.lever_resume.verify()
