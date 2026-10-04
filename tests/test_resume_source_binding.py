import pytest
from reportlab.pdfgen import canvas

from hireme.onboarding import import_resume,model_candidates


def pdf(path,lines):
    document=canvas.Canvas(str(path))
    for index,line in enumerate(lines):document.drawString(72,720-index*20,line)
    document.save();return path


def test_model_candidates_use_selected_pdf_and_ignore_linked_text_cache(store,tmp_path):
    source=pdf(tmp_path/'resume.pdf',['Test Person','Synthetic Research University','Authorization Yes'])
    imported=import_resume(store,source)
    cache=store.root/'config'/'resume.txt';cache.unlink()
    secret=tmp_path/'synthetic-secret.txt';secret.write_text('SYNTHETIC SECRET THAT MUST NOT BE SENT');cache.symlink_to(secret)
    before=store.facts();seen=[]
    class Provider:
        def extract_resume(self,text):
            seen.append(text)
            return {'facts':[{'key':'full_name','value':'Test Person','quote':'Test Person'},
                             {'key':['school'],'value':'Synthetic Research University','quote':'Synthetic Research University'},
                             'Synthetic malformed individual fact',
                             {'key':'school','value':'Synthetic Research University','quote':'Synthetic Research University'},
                             {'key':'work_authorized_us','value':'Yes','quote':'Authorization Yes'}]}
    values=model_candidates(store,Provider())
    assert len(seen)==1 and 'Synthetic Research University' in seen[0] and 'SYNTHETIC SECRET' not in seen[0]
    assert values['school']=='Synthetic Research University' and 'work_authorized_us' not in values
    assert store.facts()['full_name']==before['full_name'] and store.facts()['work_authorized_us']==before['work_authorized_us']
    school=store.facts(False)['school']
    assert not school['confirmed'] and imported['hash'] in school['source']


def test_corrupt_selected_resume_is_rejected_before_any_model_request(store,tmp_path):
    result=import_resume(store,pdf(tmp_path/'resume.pdf',['Test Person','Synthetic Research University']))
    (store.root/'documents'/(result['hash']+'.pdf')).write_bytes(b'%PDF-Synthetic corruption')
    class Provider:
        def extract_resume(self,text):pytest.fail('Corrupt resume must not be sent to a model')
    before=store.facts(False)
    with pytest.raises(ValueError,match='Stored resume changed'):model_candidates(store,Provider())
    assert store.facts(False)==before and not store.db.execute('SELECT * FROM model_requests').fetchone()


@pytest.mark.parametrize('change',['selection','content'])
def test_resume_change_during_extraction_discards_suggestions_and_preserves_request_count(store,tmp_path,change):
    original=import_resume(store,pdf(tmp_path/'resume.pdf',['Test Person','Synthetic Research University']))
    replacement=pdf(tmp_path/'replacement.pdf',['Test Person','Different synthetic resume.'])
    before=store.facts(False)
    class Provider:
        def extract_resume(self,text):
            store.reserve_model_request()
            if change=='selection':import_resume(store,replacement)
            else:(store.root/'documents'/(original['hash']+'.pdf')).write_bytes(b'%PDF-Synthetic changed content')
            return {'facts':[{'key':'school','value':'Synthetic Research University','quote':'Synthetic Research University'}]}
    with pytest.raises(ValueError,match='resume changed'):model_candidates(store,Provider())
    assert store.facts(False)==before and store.db.execute('SELECT COUNT(*) FROM model_requests').fetchone()[0]==1


def test_invalid_fact_list_does_not_change_saved_facts(store,tmp_path):
    import_resume(store,pdf(tmp_path/'resume.pdf',['Test Person','Synthetic resume.']))
    before=store.facts(False)
    class Provider:
        def extract_resume(self,text):return {'facts':'invalid synthetic response'}
    with pytest.raises(ValueError,match='invalid facts list'):model_candidates(store,Provider())
    assert store.facts(False)==before
