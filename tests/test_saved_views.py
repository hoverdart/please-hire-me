import pytest

from hireme.backup import create_backup,restore_backup
from hireme.saved_views import change_view,list_views
from hireme.store import Store


def save(store, name='Prepared engineering', **filters):
    return change_view(store, {'action':'save','name':name,'search':'Python','status':'prepared','sort':'fit',**filters})['views']


def test_save_replace_delete_only_change_private_filter_records(store, job, package):
    aid=store.prepare(job,package);store.begin_submit(aid);store.finish(aid,'unknown')
    before=store.snapshot();before.pop('saved_views')
    row=save(store)[0]
    assert row['name']=='Prepared engineering' and row['search']=='Python' and row['status']=='prepared'
    changed=change_view(store,{'action':'replace','id':row['id'],'search':' Café 100%_ ','status':'unknown','sort':'company'})['views'][0]
    assert changed['id']==row['id'] and changed['name']==row['name'] and changed['search']=='Café 100%_'
    assert store.snapshot()['saved_views']==[changed]
    after=store.snapshot();after.pop('saved_views')
    assert after==before and not store.db.execute('SELECT * FROM model_requests').fetchone()
    assert change_view(store,{'action':'delete','id':row['id']})=={'views':[]}
    assert store.db.execute('SELECT state FROM applications WHERE id=?',(aid,)).fetchone()[0]=='unknown'


def test_saved_filters_travel_in_history_backup_without_changing_active_settings(store,tmp_path):
    rows=save(store,name='Café roles')
    archive=tmp_path/'history.zip';create_backup(store,archive)
    root=tmp_path/'restored';restore_backup(archive,root)
    restored=Store(root)
    try:
        assert list_views(restored)==rows
        assert not restored.settings()['live_enabled'] and store.settings()['live_enabled']
    finally:restored.close()


def test_unicode_names_do_not_silently_replace_a_saved_view_and_capacity_is_bounded(store):
    original=save(store,name='Café roles')
    with pytest.raises(ValueError,match='already has this name'):save(store,name='CAFÉ ROLES',search='different')
    assert list_views(store)==original
    for index in range(19):save(store,name=f'Synthetic {index}')
    with pytest.raises(ValueError,match='20 saved views'):save(store,name='Another view')
    row=list_views(store)[0];change_view(store,{'action':'delete','id':row['id']})
    save(store,name='Another view')
    assert len(list_views(store))==20


@pytest.mark.parametrize('data',[
    None, [], {'action':'submit'}, {'action':'save','name':''},
    {'action':'save','name':'x'*49}, {'action':'save','name':'bad\nname'},
    {'action':'save','name':'Name','search':'x'*201},
    {'action':'save','name':'Name','status':['prepared']},
    {'action':'save','name':'Name','status':'made-up'},
    {'action':'save','name':'Name','sort':'made-up'},
    {'action':'delete','id':'../private'}, {'action':'delete','id':'a'*32},
    {'action':'replace','id':'a'*32,'search':'anything'},
])
def test_invalid_saved_view_actions_leave_existing_data_unchanged(store,data):
    save(store)
    before=store.snapshot()
    with pytest.raises(ValueError):change_view(store,data)
    assert store.snapshot()==before
