from contextlib import contextmanager
import json

import pytest

from hireme.draft_journal import DraftJournal
from hireme.util import Blocked, digest
from tests.test_ashby_drafts import draft,request,form,RENDER
from types import SimpleNamespace


def test_intent_and_ack_are_visible_on_an_independent_connection(store,job,package):
    import sqlite3
    journal=DraftJournal(store,job);key=('endpoint','POST','request')
    record=journal.intent({key},'field_save',{'render':'render','path':'path'},package)
    db=sqlite3.connect(store.root/'ledger.sqlite3')
    assert db.execute('SELECT state FROM draft_operations WHERE id=?',(record,)).fetchone()[0]=='intent'
    journal.acknowledge(key)
    assert db.execute('SELECT state FROM draft_operations WHERE id=?',(record,)).fetchone()[0]=='acknowledged'
    db.close()
    raw=repr(tuple(store.db.execute('SELECT * FROM draft_operations').fetchone()))
    assert package['answers'][0]['value'] not in raw and digest(package) not in raw
    with pytest.raises(Blocked):journal.acknowledge(key)
    with pytest.raises(Blocked):journal.intent({key},'field_save',{},package)


def test_uncommitted_parent_aborts_operation(store,job,package):
    journal=DraftJournal(store,job)
    with store.transaction(),pytest.raises(Blocked):journal.intent({'key'},'field_save',{},package)
    assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()


def test_failed_commit_cannot_expose_operation_success(store,job,package,monkeypatch):
    journal=DraftJournal(store,job);original=store.transaction
    @contextmanager
    def fail():
        with original():
            yield
            raise RuntimeError('synthetic failed commit')
    with monkeypatch.context() as m:
        m.setattr(store,'transaction',fail)
        with pytest.raises(RuntimeError):journal.intent({'key'},'field_save',{},package)
    assert not journal.keys and not store.db.execute('SELECT 1 FROM draft_operations').fetchone()
    journal.intent({'key'},'field_save',{},package)
    with monkeypatch.context() as m:
        m.setattr(store,'transaction',fail)
        with pytest.raises(RuntimeError):journal.acknowledge('key')
    assert store.db.execute('SELECT state FROM draft_operations').fetchone()[0]=='intent'


def test_overlapping_aliases_and_foreign_callbacks_cannot_ack(store,job,package):
    journal=DraftJournal(store,job)
    journal.intent({'original','rewritten'},'file_upload',{},package)
    with pytest.raises(Blocked):journal.intent({'rewritten','third'},'file_upload',{},package)
    with pytest.raises(Blocked):DraftJournal(store,job).acknowledge('original')
    journal.acknowledge('rewritten')
    with pytest.raises(Blocked):journal.acknowledge('original')


def test_compilation_never_enables_autosaves_and_revalidates_metadata(store,job,package):
    d,_=draft(store,job,package);p=d.package
    d.package=None;d.grants={}
    before=store.db.total_changes
    d.compile(None,p['answers'],p)
    assert store.db.total_changes==before
    assert d.package is None and not d.grants
    r,data=request()
    assert not d.allow(r,data,'key')
    d.enable(None,p['answers'],p)
    d.forms[RENDER]['entries']['_systemfield_email']['title']='Changed question'
    with pytest.raises(Blocked,match='form_changed'):d.allow(r,data,'key')
    assert not store.db.execute('SELECT 1 FROM draft_operations').fetchone()


def test_failed_native_ack_or_ack_commit_never_marks_field_saved(store,job,package,monkeypatch):
    d,_=draft(store,job,package);r,data=request();assert d.allow(r,data,'key')
    response=SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':form({'_systemfield_email':data['variables']['value']})}})
    monkeypatch.setattr(d.journal,'acknowledge',lambda key:(_ for _ in ()).throw(RuntimeError('commit failed')))
    with pytest.raises(RuntimeError):d.observe_save(response,'key')
    assert d.failed and not d.complete and not d.saved
    assert store.db.execute('SELECT state FROM draft_operations').fetchone()[0]=='intent'


@pytest.mark.parametrize('change',['none','question','requiredness','consent'])
def test_acknowledged_action_rotation_is_revalidated_before_another_save(store,job,package,change):
    from tests.test_ashby_drafts import submission_fixture
    d,_,r,data=submission_fixture(store,job,package)
    r,data=request();assert d.allow(r,data,'first')
    saved=form({'_systemfield_email':data['variables']['value']})
    saved['formControls']=[{'identifier':'66666666-6666-4666-8666-666666666666','title':'Submit','__typename':'FormControl'}]
    if change=='question':
        saved['sections'][0]['fieldEntries'].append({'id':RENDER+'_conditional','field':{'path':'conditional','title':'New required legal declaration','type':'String'}})
    if change=='requiredness':saved['sections'][0]['fieldEntries'][1]['isRequired']=True
    d.observe_save(SimpleNamespace(status=200,json=lambda:{'data':{'setFormValue':saved}}),'first')
    if change=='consent':d.texting_contexts['acme']['shouldAskForTextingConsent']=True
    if change!='none':
        with pytest.raises(Blocked,match='form_changed'):d.allow(r,data,'second')
        assert store.db.execute('SELECT COUNT(*) FROM draft_operations').fetchone()[0]==1
    else:assert d.allow(r,data,'second')
