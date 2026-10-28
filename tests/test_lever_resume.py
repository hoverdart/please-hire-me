import hashlib
from types import SimpleNamespace

import pytest

from hireme.browser import Browser
from hireme.lever_resume import LeverResume,parsed_urls
from hireme.util import Blocked

ACCOUNT='11111111-2222-3333-4444-555555555555'
PDF=b'%PDF-1.4\nApproved binary resume\x00\xff\n%%EOF'
HASH=hashlib.sha256(PDF).hexdigest()


def test_parser_url_categories_follow_reviewed_order_and_last_link_wins():
    assert parsed_urls([{'domain':'github.com','url':'https://github.com/first'},
        {'domain':'GITHUB.COM','url':'https://github.com/last'},
        {'domain':'linkedin.com','url':'https://linkedin.com/in/test'},
        {'domain':None,'url':'https://portfolio.invalid/'}])=={
        'urls[GitHub]':'https://github.com/last','urls[LinkedIn]':'https://linkedin.com/in/test',
        'urls[Other]':'https://portfolio.invalid/'}


@pytest.mark.parametrize('links',[None,{},['invalid'],[{'domain':1,'url':'https://test.invalid/'}],
    [{'domain':'test','url':None}],[{'domain':'test','url':'https://test.invalid/\n'}],
    [{'url':'https://test.invalid/'}]*101])
def test_unrecognized_parser_links_never_authorize_clearing_a_field(links):
    assert parsed_urls(links)=={}


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


@pytest.mark.parametrize('change',['none','required','initial','approved','changed','duplicate','unsupported_type','url_type','changed_storage','revoked'])
def test_parser_cleanup_only_clears_exact_acknowledged_initially_blank_optional_unapproved_values(store,monkeypatch,change):
    browser,route,values=setup_upload(store,monkeypatch);browser._route(route)
    parser_url='https://parser-only.invalid/'
    browser._upload_response(SimpleNamespace(request=route.request,status=200,json=lambda:{
        'resumeStorageId':'storage-fixture','links':[{'domain':'example.invalid','url':parser_url}]}))
    values['resumeStorageId']='storage-fixture'
    f={'ref':{'name':'urls[Other]'},'label':'Custom website','type':'text','required':False,'value':''}
    fields=[f];answers=[];current=[parser_url]
    browser._control=lambda field:SimpleNamespace(input_value=lambda:current[0],fill=lambda value:current.__setitem__(0,value))
    if change=='required':f['required']=True
    elif change=='initial':f['value']='Existing applicant entry'
    elif change=='approved':answers=[{'field':f}]
    elif change=='changed':current[0]='https://changed.invalid/'
    elif change=='duplicate':fields.append(dict(f))
    elif change=='unsupported_type':f['type']='textarea'
    elif change=='url_type':f['type']='url'
    elif change=='changed_storage':values['resumeStorageId']='other-storage'
    elif change=='revoked':
        def reject():raise Blocked('paused')
        browser.write_boundary=SimpleNamespace(check=reject)
    before=current[0]
    if change in {'changed_storage','revoked'}:
        with pytest.raises(Blocked,match='upload_verification_failed' if change=='changed_storage' else 'paused'):
            browser.lever_resume.clear_unapproved_prefills(fields,answers,'synthetic-job')
    else:browser.lever_resume.clear_unapproved_prefills(fields,answers,'synthetic-job')
    assert current[0]==('' if change=='none' else before)
    assert store.db.execute("SELECT COUNT(*) FROM events WHERE kind='parser_prefill_cleared'").fetchone()[0]==int(change=='none')


@pytest.mark.parametrize('protected',['approved','required'])
def test_protected_parser_field_stays_while_another_optional_unapproved_field_is_cleared(store,monkeypatch,protected):
    browser,route,values=setup_upload(store,monkeypatch);browser._route(route)
    urls={'urls[Other]':'https://portfolio.invalid/','urls[Twitter]':'https://twitter.com/test'}
    browser._upload_response(SimpleNamespace(request=route.request,status=200,json=lambda:{'resumeStorageId':'storage-fixture',
        'links':[{'url':urls['urls[Other]']},{'domain':'twitter.com','url':urls['urls[Twitter]']}]}))
    values['resumeStorageId']='storage-fixture';current=dict(urls)
    fields=[{'ref':{'name':name},'type':'text','required':protected=='required' and name=='urls[Other]',
             'value':'','label':name} for name in urls]
    answers=[{'field':fields[0]}] if protected=='approved' else []
    def control(f):
        name=f['ref']['name'];return SimpleNamespace(input_value=lambda:current[name],fill=lambda value:current.__setitem__(name,value))
    browser._control=control
    browser.lever_resume.clear_unapproved_prefills(fields,answers,'synthetic-job')
    assert current=={'urls[Other]':urls['urls[Other]'],'urls[Twitter]':''}
