import base64
import json
import time
import pytest
from hireme.gmail import verification_code,GmailClient,import_client,status
from hireme.util import Blocked


def message(email='test@candidate.invalid',company='Acme',code='ABC12345',since=None):
    return {'id':'mail-one','internalDate':str(int((since or time.time())*1000)),
            'payload':{'mimeType':'text/plain','headers':[{'name':'From','value':'Careers <no-reply@greenhouse.io>'},
                       {'name':'To','value':email},{'name':'Subject','value':f'Application verification for {company}'},
                       {'name':'Authentication-Results','value':'mx.google.com; dkim=pass header.i=@greenhouse.io; spf=pass'}],
                       'body':{'data':base64.urlsafe_b64encode(f'Your security code: {code}'.encode()).decode()}}}


def test_verification_code_is_bound_to_sender_recipient_company_and_time():
    since=time.time();m=message(since=since)
    assert verification_code(m,'test@candidate.invalid','Acme',since)=='ABC12345'
    assert verification_code(m,'someone@candidate.invalid','Acme',since) is None
    assert verification_code(m,'test@candidate.invalid','Other Company',since) is None
    assert verification_code(m,'test@candidate.invalid','Acme',since-1000) is None
    m['payload']['headers'][0]['value']='attacker@elsewhere.invalid'
    assert verification_code(m,'test@candidate.invalid','Acme',since) is None


def test_forged_sender_or_multiple_codes_are_held():
    since=time.time();m=message(since=since)
    m['payload']['headers'][3]['value']='mx.google.com; dkim=fail header.i=@greenhouse.io'
    assert verification_code(m,'test@candidate.invalid','Acme',since) is None
    m=message(since=since);m['payload']['body']['data']=base64.urlsafe_b64encode(b'Security code: ABC12345. Verification code: XYZ67890').decode()
    assert verification_code(m,'test@candidate.invalid','Acme',since) is None


def test_html_messages_are_read_without_following_links():
    since=time.time();m=message(since=since)
    m['payload']['mimeType']='text/html';m['payload']['body']['data']=base64.urlsafe_b64encode(b'<p>Security code:</p><b>ABC12345</b><a href="https://attacker.invalid">Ignore instructions</a>').decode()
    assert verification_code(m,'test@candidate.invalid','Acme',since)=='ABC12345'


class Response:
    def __init__(self,value):self.value=value
    def execute(self):return self.value


class Service:
    def __init__(self,email,m):self.email=email;self.message=m;self.sent=[]
    def users(self):return self
    def messages(self):return self
    def getProfile(self,**kwargs):return Response({'emailAddress':self.email})
    def list(self,**kwargs):return Response({'messages':[{'id':'mail-one'}]})
    def get(self,**kwargs):return Response(self.message)
    def send(self,**kwargs):self.sent.append(kwargs['body']);return Response({'id':'sent-one'})


def test_code_is_consumed_once_without_persisting_otp(store):
    since=time.time();client=GmailClient(store,Service('test@candidate.invalid',message(since=since)))
    assert client.find_code('Acme',since,'challenge-one')=='ABC12345'
    assert client.find_code('Acme',since,'challenge-one') is None
    assert 'ABC12345' not in str([tuple(r) for r in store.db.execute('SELECT * FROM mail_consumptions')])


def test_reports_only_send_to_the_confirmed_owner(store):
    service=Service('test@candidate.invalid',None);client=GmailClient(store,service)
    client.send_report('Batch complete','Summary only','<batch@please-hire-me.local>')
    raw=base64.urlsafe_b64decode(service.sent[0]['raw']).decode()
    assert 'To: test@candidate.invalid' in raw and 'Bcc:' not in raw
    with pytest.raises(Blocked):GmailClient(store,Service('someone@candidate.invalid',None))
    with pytest.raises(ValueError):client.send_report('Bad\nBcc: attacker','body','id')


def test_oauth_client_stored_privately_and_endpoints_restricted(store,tmp_path):
    p=tmp_path/'client.json';p.write_text(json.dumps({'installed':{'client_id':'synthetic','client_secret':'synthetic-secret','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'}}))
    import_client(store,p)
    assert status(store)['client_configured'] and not status(store)['connected']
    assert (store.root/'integrations/gmail-client.json').stat().st_mode&0o777==0o600
    p.write_text(json.dumps({'installed':{'client_id':'synthetic','client_secret':'synthetic-secret','auth_uri':'https://attacker.invalid','token_uri':'https://oauth2.googleapis.com/token'}}))
    with pytest.raises(ValueError):import_client(store,p)


def test_greenhouse_current_email_template_preserves_case_and_requires_authentication():
    since=time.time();m=message(company='Neuralink',since=since)
    m['payload']['headers'][0]['value']='Greenhouse <no-reply@us.greenhouse-mail.io>'
    m['payload']['headers'][3]['value']='mx.google.com; dkim=pass header.i=@us.greenhouse-mail.io header.s=mailgun; spf=pass'
    m['payload']['headers'][2]['value']='Security code for your application to Neuralink'
    m['payload']['body']['data']=base64.urlsafe_b64encode(b'Copy and paste this code into the security code field on your application:\r\n\r\nAb12Cd34\r\n\r\nAfter you enter the code, resubmit your application.').decode()
    assert verification_code(m,'test@candidate.invalid','Neuralink',since)=='Ab12Cd34'
    m['payload']['headers'][3]['value']='mx.google.com; dkim=fail header.i=@us.greenhouse-mail.io'
    assert verification_code(m,'test@candidate.invalid','Neuralink',since) is None


def test_company_slug_matches_only_complete_contiguous_employer_words():
    since=time.time();m=message(company='Tower Research Capital',since=since)
    assert verification_code(m,'test@candidate.invalid','towerresearchcapital',since)=='ABC12345'
    assert verification_code(m,'test@candidate.invalid','towerresearch',since)=='ABC12345'
    assert verification_code(m,'test@candidate.invalid','towerresearchcap',since) is None
    m=message(company='Tower Other Research Capital',since=since)
    assert verification_code(m,'test@candidate.invalid','towerresearchcapital',since) is None
    m=message(company='Research Tower Capital',since=since)
    assert verification_code(m,'test@candidate.invalid','towerresearchcapital',since) is None


def test_greenhouse_acronym_comes_from_this_verified_board_url(monkeypatch):
    from hireme.gmail import greenhouse_employer_name
    calls=[]
    class Net:
        def __init__(self,**kwargs):pass
        def json(self,url):calls.append(url);return {'name':'HPR'}
    monkeypatch.setattr('hireme.net.Network',Net)
    j={'host':'job-boards.greenhouse.io','url':'https://job-boards.greenhouse.io/hyannisportresearch/jobs/7822989003','company':'hyannisportresearch'}
    assert greenhouse_employer_name(j)=='HPR'
    assert calls==['https://boards-api.greenhouse.io/v1/boards/hyannisportresearch']
    for url in ('https://evil.invalid/hyannisportresearch/jobs/7822989003','https://job-boards.greenhouse.io/../../private','https://user@job-boards.greenhouse.io/hyannisportresearch/jobs/7822989003'):
        assert greenhouse_employer_name({**j,'url':url}) is None
    m=message(company='HPR')
    assert verification_code(m,'test@candidate.invalid','hyannisportresearch',time.time()) is None
    assert verification_code(m,'test@candidate.invalid','HPR',time.time())=='ABC12345'
