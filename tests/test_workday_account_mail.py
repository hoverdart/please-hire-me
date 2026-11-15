import base64
import json
from types import SimpleNamespace

import pytest

from hireme.workday_account_mail import activation_link,activation_url,find_activation
from hireme.workday_site import site_for_url

URL='https://acme.wd1.myworkdayjobs.com/External/job/US/Synthetic_R12345'
JOB={'url':URL,'host':'acme.wd1.myworkdayjobs.com'}
TOKEN='a'*96
ACTIVATION='https://acme.wd1.myworkdayjobs.com/External/activate/'+TOKEN+'/'


def message(link=ACTIVATION,*,recipient='test@candidate.invalid',sender='otp.workday.com',dkim='otp.workday.com',received=9900):
    return {'internalDate':str(received*1000),'payload':{'mimeType':'text/html','headers':[
        {'name':'From','value':'noreply@'+sender},{'name':'To','value':recipient},
        {'name':'Authentication-Results','value':'mx.google.com; dkim=pass header.i=@'+dkim+';'}],
        'body':{'data':base64.urlsafe_b64encode(('<p>Activate your account</p><a href="'+link+'">Activate</a>').encode()).decode()}}}


def test_activation_is_bound_to_new_request_owner_tenant_and_site():
    assert activation_link(message(),'test@candidate.invalid',JOB,9800,clock=10000)==ACTIVATION


@pytest.mark.parametrize('changes',[
    {'recipient':'other@candidate.invalid'},{'sender':'evil.invalid'},{'dkim':'evil.invalid'},{'received':9000}])
def test_unrelated_or_old_email_cannot_activate_an_account(changes):
    assert activation_link(message(**changes),'test@candidate.invalid',JOB,9800,clock=10000) is None


@pytest.mark.parametrize('link',[
    ACTIVATION.replace('acme.wd1','other.wd1'),ACTIVATION.replace('/External/','/Other/'),
    ACTIVATION.replace('/activate/','/passwordreset/'),ACTIVATION.replace('myworkdayjobs.com','myworkdayjobs.com.evil.invalid'),
    ACTIVATION+'?redirect=https%3A%2F%2Fevil.invalid%2FExternal%2Flogin',
    ACTIVATION+'?redirect=%2FOther%2Flogin',ACTIVATION+'?unknown=value',
    ACTIVATION+'?redirect=%2FExternal%2Flogin&redirect=%2FExternal%2Flogin',
    ACTIVATION+'#fragment',ACTIVATION.replace('https://','http://')])
def test_activation_link_cannot_escape_its_observed_native_operation(link):
    assert not activation_url(link,site_for_url(URL))
    assert activation_link(message(link),'test@candidate.invalid',JOB,9800,clock=10000) is None


def test_same_site_redirect_and_locale_are_valid_without_widening_host():
    link=ACTIVATION.replace('/External/','/en-US/External/')+'?redirect=%2Fen-US%2FExternal%2Flogin'
    assert activation_url(link,site_for_url(URL))


def test_two_different_activation_tokens_are_ambiguous():
    mail=message()
    mail['payload']['body']['data']=base64.urlsafe_b64encode((ACTIVATION+'\n'+ACTIVATION.replace(TOKEN,'b'*96)).encode()).decode()
    assert activation_link(mail,'test@candidate.invalid',JOB,9800,clock=10000) is None


def test_mail_reservation_precedes_activation_and_never_stores_the_link(store,monkeypatch):
    import time
    now=time.time();store.update_settings({'gmail_verification':True})
    mail=message(received=int(now))
    calls=[]
    class Messages:
        def list(self,**kwargs):calls.append(kwargs);return SimpleNamespace(execute=lambda:{'messages':[{'id':'new-message'}]})
        def get(self,**kwargs):return SimpleNamespace(execute=lambda:mail)
    api=Messages();client=SimpleNamespace(email='test@candidate.invalid',service=SimpleNamespace(users=lambda:SimpleNamespace(messages=lambda:api)))
    assert find_activation(store,JOB,now-1,'account-intent',client=client)==ACTIVATION
    assert find_activation(store,JOB,now-1,'account-intent',client=client) is None
    assert store.db.execute('SELECT challenge_id FROM mail_consumptions').fetchone()[0]=='account-intent'
    assert TOKEN not in json.dumps(store.snapshot()) and TOKEN.encode() not in store.path.read_bytes()
    assert calls[0]['includeSpamTrash'] is True and 'otp.workday.com' in calls[0]['q']
