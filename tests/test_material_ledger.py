import pytest
from hireme.material_ledger import search_materials
from hireme.materials import import_material, review_material


def test_source_search_reaches_old_sources_and_literal_unicode_excerpts(store):
    source = import_material(store, 'Synthetic STRAßE excerpt with literal 100%_value.'.encode(), 'old-notes.txt', 'context')
    store.db.execute("UPDATE materials SET created='2000-01-01' WHERE id=?", (source['id'],))
    for i in range(30): import_material(store, f'Newer synthetic excerpt number {i} for review.'.encode(), f'new-{i}.txt', 'context')
    assert source['id'] not in {row['id'] for row in search_materials(store)['materials']}
    before=store.snapshot(); changes=store.db.total_changes
    for term in ('STRASSE', '100%_value', 'old-notes'):
        result=search_materials(store, search=term, offset=100)
        assert result['total']==1 and result['offset']==0 and result['materials'][0]['id']==source['id']
        assert result['library_total']==31
    assert search_materials(store,search='not present')['materials']==[]
    assert store.snapshot()==before and store.db.total_changes==changes


def test_source_approval_filters_do_not_treat_unapproved_roles_as_approved(store):
    for role in ('personal', 'style', 'reference'):
        source=import_material(store, f'Synthetic {role} excerpt long enough for review.'.encode(), role+'.txt', 'context')
        review_material(store,source['id'],source['text'],role,True)
    source=import_material(store,b'Unapproved synthetic excerpt long enough for review.','unapproved.txt','context')
    review_material(store,source['id'],source['text'],'style',False)
    assert search_materials(store,status='approved')['total']==3
    assert search_materials(store,status='review')['total']==1
    for role in ('personal','style','reference'):
        result=search_materials(store,status=role)
        assert result['total']==1 and result['materials'][0]['confirmed']==1
    snapshot=store.snapshot(material_search='Unapproved',material_status='review')
    assert snapshot['material_count']==1 and snapshot['material_total']==4
    assert snapshot['material_search']=='Unapproved' and snapshot['material_status']=='review'


@pytest.mark.parametrize('kwargs',[{'search':None},{'search':'x'*201},{'status':'unknown'},{'offset':True},{'offset':-1},{'offset':1000001},{'limit':0},{'limit':101}])
def test_source_search_rejects_invalid_bounds(store,kwargs):
    with pytest.raises(ValueError): search_materials(store,**kwargs)


@pytest.mark.parametrize('issue',['missing','empty','permissions','link','directory','oversize'])
def test_source_original_readability_reports_unavailable_without_writes(store,tmp_path,issue):
    source=import_material(store,b'Synthetic original source content for review.','original.txt','context')
    path=store.root/'materials'/source['filename']
    assert search_materials(store)['materials'][0]['original_available']
    if issue=='missing': path.unlink()
    elif issue=='empty': path.write_bytes(b'')
    elif issue=='permissions': path.chmod(0o644)
    elif issue=='link':
        outside=tmp_path/'outside.txt'; outside.write_bytes(b'Outside synthetic content')
        path.unlink(); path.symlink_to(outside)
    elif issue=='directory': path.unlink(); path.mkdir()
    else:
        with path.open('wb') as target: target.truncate(21*1024*1024)
    changes=store.db.total_changes
    assert not search_materials(store)['materials'][0]['original_available']
    assert store.db.total_changes==changes
    assert store.db.execute('SELECT text FROM materials WHERE id=?',(source['id'],)).fetchone()[0]==source['text']


def test_source_original_readability_is_not_an_integrity_certificate(store):
    source=import_material(store,b'Synthetic original source content for review.','original.txt','context')
    path=store.root/'materials'/source['filename']; path.write_bytes(b'Changed but readable synthetic bytes')
    assert search_materials(store)['materials'][0]['original_available']
    from hireme.backup import create_backup
    with pytest.raises(ValueError,match='missing or changed'): create_backup(store,store.root.parent/'tampered.zip')


def test_read_only_private_original_is_available_without_changing_permissions(store):
    source=import_material(store,b'Synthetic original source content for review.','original.txt','context')
    path=store.root/'materials'/source['filename']; path.chmod(0o400)
    assert search_materials(store)['materials'][0]['original_available']
    assert path.stat().st_mode & 0o777==0o400
