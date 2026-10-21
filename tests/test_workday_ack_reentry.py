"""Body decoding can yield from a native response callback to its consumer."""
from types import SimpleNamespace

from greenlet import getcurrent, greenlet
import pytest

from hireme.util import Blocked, digest
from hireme.workday_flow import NativeFlow
from hireme.workday_nvidia import NvidiaNativeDriver
from tests.test_workday_native import multipart, request
from tests.test_workday_state import journal, wd_job


def suspended_response_callback(observer,response,data):
    """Suspend the first native body read just as Playwright's callback yields."""
    caller=getcurrent()
    reads=[]
    def body():
        reads.append(True)
        if len(reads)==1:caller.switch('native-body-pending')
        return data
    response.json=body
    callback=greenlet(lambda:observer(response))
    assert callback.switch()=='native-body-pending' and not callback.dead
    return callback


@pytest.mark.parametrize('valid',[True,False])
def test_native_control_reentrant_body_read_settles_once_before_consumer_checks(store,wd_job,valid):
    saved=journal(store,wd_job)
    store.update_settings({'remote_drafts':True})
    browser=SimpleNamespace(workday_grant=None)
    native=NvidiaNativeDriver.__new__(NvidiaNativeDriver)
    native.store,native.job,native.browser=store,wd_job,browser
    native.remote='remote-fixture';native.session_id='native-session';native.read_only=False
    native.approved_package={'job_id':wd_job['id'],'url':wd_job['url'],'answers':[],
        'documents':[],'steps':[],'facts_hash':digest(store.facts())}
    native.controller=SimpleNamespace(session=saved.session,profile=SimpleNamespace(signature=saved.signature))
    pending=[];controls=[]
    class Transport:
        def request(self,operation):
            control=browser.workday_grant
            assert control.consume_request(request(operation))
            response=SimpleNamespace(request=request(operation),status=200)
            pending.append(suspended_response_callback(control.observe_response,response,
                {} if valid else {'unexpected':True}))
            assert not control.observed and saved.current()['state']=='unknown'
            controls.append(control)
            return response
    native.transport=Transport()
    if valid:native._control('validate',{'section':'My Information'})
    else:
        with pytest.raises(Blocked,match='not acknowledged'):
            native._control('validate',{'section':'My Information'})
    control,=controls
    assert control.observed and saved.current()['state']==('acknowledged' if valid else 'unknown')
    # The delayed callback completes after the consumer's second body read. It
    # must leave the already-durable result alone, including terminal rejection.
    assert pending[0].switch() is False and pending[0].dead
    assert saved.current()['state']==('acknowledged' if valid else 'unknown')
    rows=list(store.db.execute('SELECT state FROM remote_draft_steps'))
    assert [row[0] for row in rows]==['acknowledged' if valid else 'unknown']
    assert store.db.execute("SELECT count(*) FROM events WHERE kind='workday_draft_acknowledged'").fetchone()[0]==int(valid)
    assert browser.workday_grant is None


@pytest.mark.parametrize('valid',[True,False])
def test_native_upload_reentrant_scan_body_read_authorizes_only_one_matched_next_upload(store,wd_job,valid):
    saved=journal(store,wd_job)
    store.update_settings({'remote_drafts':True})
    browser=SimpleNamespace(workday_grant=None)
    native=NvidiaNativeDriver.__new__(NvidiaNativeDriver)
    native.browser=browser;native.resuming=False
    document=dict(store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone())
    callbacks=[];transmitted=[];queues=[]
    class Transport:
        def upload(self,document,*,scan=False):
            queue=browser.workday_grant;queues.append(queue)
            upload=next(grant for grant in queue.grants if grant.scan==scan)
            body,content_type=multipart(document,b'')
            req=SimpleNamespace(url=upload.url,method='POST',post_data_buffer=body,
                headers={'content-type':content_type})
            override=queue.consume_request(req)
            if not override:raise Blocked('upload_verification_failed','The prior scan was rejected')
            assert override['post_data'].count(upload.data)==1
            transmitted.append(upload.url)
            data=({'diagnosis':'clean','containsMacros':False,'fileSize':len(upload.data)+(not valid),
                   'fileType':'application/pdf'} if scan else
                  {'id':'native-attachment','fileName':document['filename'],'fileLength':len(upload.data),
                   'contentType':{'id':'Content_Type_ID=application/pdf'}})
            response=SimpleNamespace(request=req,status=200)
            callbacks.append(suspended_response_callback(queue.observe_response,response,data))
            assert not upload.observed and not upload.acknowledged
            return response
    native.transport=Transport()
    flow=NativeFlow.__new__(NativeFlow)
    flow.driver,flow.browser,flow.store,flow.journal=native,browser,store,saved
    if valid:flow._upload({'fields':[]},[document],None)
    else:
        with pytest.raises(Blocked,match='prior scan was rejected'):
            flow._upload({'fields':[]},[document],None)
    assert len(transmitted)==(2 if valid else 1)
    for callback in callbacks:assert callback.switch() is False and callback.dead
    assert all(grant.observed for grant in queues[0].grants if grant.grant)
    assert queues[0].complete is valid
    assert [row[0] for row in store.db.execute('SELECT state FROM remote_draft_steps ORDER BY created,id')]==(
        ['acknowledged','acknowledged'] if valid else ['unknown'])
    for kind in ('workday_pdf_scanned','workday_pdf_uploaded'):
        assert store.db.execute('SELECT count(*) FROM events WHERE kind=?',(kind,)).fetchone()[0]==int(valid)
    assert browser.workday_grant is None
