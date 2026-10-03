import json
import pytest
from hireme.answers import resolve,validate_package
from hireme.util import Blocked


def field(label,kind='text',options=None):
    return {'label':label,'type':kind,'required':True,'options':options or [],'maxlength':-1}


def test_greenhouse_education_dates_use_components_and_section_over_old_binding(store,job):
    store.put_facts({'college_start':'2025-08','graduation':'2028-05'})
    host=job['host']
    start={**field('Start date year','number'),'section':'education'}
    store.bind_field(host,start['label'],[],fact_key='graduation')
    assert resolve(store,host,start)['value']=='2025'
    end={**field('End date year','number'),'section':'education'}
    assert resolve(store,host,end)['value']=='2028'
    month={**field('Start date month*','combobox',['July','August','September']),'section':'education'}
    assert resolve(store,host,month)['value']=='August'
    # An employment date cannot be assumed to be a college date.
    with pytest.raises(Blocked):resolve(store,host,field('End date year','number'))


def test_confirmed_values_match_actual_greenhouse_options(store,job):
    store.put_facts({'state':'CA','pronouns':'he/him','worked_outside_resume':'No','summer_2027_available':'Yes'})
    host=job['host']+'|acme'
    state=field('Which U.S. State or Canadian Province do you reside in?*','combobox',['California','Ontario'])
    assert resolve(store,host,state)['value']=='California'
    assert resolve(store,host,field('Pronouns *','combobox',['He/him/his','She/her/hers']))['value']=='He/him/his'
    employment=field('Have you previously been employed at Acme for any length of time?*','combobox',['I have not previously been employed at Acme','I have been employed at Acme as an intern'])
    assert resolve(store,host,employment)['value']=='I have not previously been employed at Acme'
    assert resolve(store,host,field('I confirm my availability for a Summer 2027 (May/June starts) internship*','checkbox',['Yes','No']))['value']=='Yes'


def test_phone_country_uses_confirmed_residence_and_not_phone_prefix_alone(store,job):
    question=field('Country*','combobox',['United States +1','Canada +1'])
    assert resolve(store,job['host'],question)['value']=='United States +1'
    store.put_facts({'location':'Toronto, Ontario','phone':'+19258560000'})
    with pytest.raises(Blocked):resolve(store,job['host'],question)


def test_profile_question_variations_and_presentation_options(store,job):
    store.put_facts({'school':'University of California, Berkeley','degree':'B.S.','onsite':'Yes','country':'United States'})
    assert resolve(store,job['host'],field('Which college or university do you currently attend?*'))['value']=='University of California, Berkeley'
    assert resolve(store,job['host'],field('Degree*','select',["Bachelor’s degree","Master’s degree"]))['value']=='Bachelor’s degree'
    assert resolve(store,job['host'],field('Will you now or in the future require sponsorship for employment visa status (e.g., H-1B status)?*','select',['Yes','No']))['value']=='No'
    assert resolve(store,job['host'],field('Are you open to working in-person in one of our offices 25% of the time?*','select',['Yes','No']))['value']=='Yes'
    with pytest.raises(Blocked):resolve(store,job['host'],field('What is your highest level of education?*'))


def test_semantic_fact_selection_is_cached_revision_checked_and_clears_queue(store,job,package):
    class Model:
        calls=0
        def match_field(self,*args):self.calls+=1;return {'fact_key':'school','template_id':None}
    store.put_facts({'school':'Confirmed University'})
    host=job['host'];f=field('Where are you studying right now?')
    q=store.ask(job['id'],host,f['label'],[]);model=Model()
    a=resolve(store,host,f,model)
    assert a['value']=='Confirmed University' and model.calls==1
    assert store.db.execute('SELECT resolved FROM questions WHERE id=?',(q,)).fetchone()[0]==1
    assert resolve(store,host,f)==a
    package['answers']=[a];package['steps']=[];package['facts_hash']=__import__('hireme.util',fromlist=['digest']).digest(store.facts())
    validate_package(store,job,package)
    store.put_facts({'school':'Another Confirmed University'})
    with pytest.raises(Blocked):validate_package(store,job,package)


def test_model_cannot_invent_fact_or_confuse_current_and_completed_degree(store,job):
    class Model:
        def match_field(self,*args):return {'fact_key':'made_up_fact','template_id':None}
    with pytest.raises(Blocked):resolve(store,job['host'],field('Unknown factual question'),Model())
    store.put_facts({'degree':'B.S.'})
    class WrongDegree:
        def match_field(self,*args):return {'fact_key':'degree','template_id':None}
    with pytest.raises(Blocked):resolve(store,job['host'],field('What is the highest education you have completed?'),WrongDegree())


def test_writing_uses_approved_sentences_omits_other_employer_and_is_immutable(store,job,package):
    tid=store.put_template('motivation','Hadrian feels close to my manufacturing work. I built Python services around production workflows. Hadrian is interesting for the same reason.')
    class Model:
        def choose_answer(self,*args):return None
        def match_field(self,*args):return {'fact_key':None,'template_id':None}
        def choose_sentences(self,label,choices,*args):
            assert not any('Hadrian' in c['text'] for c in choices)
            return [c['id'] for c in choices]
    # A second sample forces semantic selection instead of unconditional single-template reuse.
    store.put_template('motivation','Seldon especially caught my attention. I tested models against recorded research traces.')
    f=field('Why are you interested in joining Acme?','textarea')
    a=resolve(store,job['host'],f,Model(),context=job)
    assert a['value']=='I built Python services around production workflows. I tested models against recorded research traces.'
    assert resolve(store,job['host'],f)==a
    package['answers']=[a];package['steps']=[]
    validate_package(store,job,package)
    a['value']+=' I served a million users.'
    with pytest.raises(Blocked):validate_package(store,job,package)


def test_untrusted_work_samples_and_options_still_block(store,job):
    class Model:
        def match_field(self,*args):raise AssertionError('Must not call Claude for forbidden work')
    with pytest.raises(Blocked,match='human_work_sample'):resolve(store,job['host'],field('Solve this coding challenge'),Model())
    with pytest.raises(Blocked,match='option_mismatch'):resolve(store,job['host'],field('Will you require sponsorship?','select',['Yes','Unknown']))


def test_graduation_season_school_alias_and_discovery_source(store,job):
    store.put_facts({'school':'University California Berkeley','graduation':'2028-05'})
    assert resolve(store,job['host'],field('Which university do you currently attend?','select',['UC Berkeley','Other']))['value']=='UC Berkeley'
    class Model:
        def match_field(self,*args):return {'fact_key':'graduation','template_id':None}
    assert resolve(store,job['host'],field('When do you expect to graduate?','select',['Fall 2027','Spring 2028']),Model())['value']=='Spring 2028'
    source={**job,'source':'gh:acme'}
    answer=resolve(store,job['host'],field('How did you hear about this role?','select',['LinkedIn','University Career Center / Job Board','Other']),context=source)
    assert answer['value']=='Other' and answer['provenance']['job_source']=='gh:acme'


def test_single_company_specific_sample_is_adapted_not_copied(store,job):
    store.put_template('motivation','Hadrian feels close to my work. I built manufacturing software using Python.')
    class Model:
        def choose_sentences(self,label,choices,*args):return [c['id'] for c in choices]
    a=resolve(store,job['host'],field('Why do you want this role?','textarea'),Model(),context=job)
    assert a['value']=='I built manufacturing software using Python.'


def test_closed_school_and_composite_job_board_option_use_known_sources(store,job):
    store.put_facts({'school':'University of California, Berkeley'})
    a=resolve(store,job['host'],field('Which college or university do you currently attend?','select',['Harvard University','Other']))
    assert a['value']=='Other' and a['provenance']['fact_key']=='school'
    a=resolve(store,job['host'],field('How did you hear about this role?','select',['Employee Referral','University Career Center / Job Board']),context={**job,'source':'gh:acme'})
    assert a['value']=='University Career Center / Job Board'
    with pytest.raises(Blocked):resolve(store,job['host'],field('How did you hear about this role?','select',['Employee Referral','LinkedIn']),context={**job,'source':'gh:acme'})


def test_scoped_history_and_summer_preferences_not_offered_as_universal_facts(store,job):
    store.put_facts({'worked_outside_resume':'No','contacts_outside_resume':'No','summer_2027_relocate':'Yes'})
    store.update_settings({'prior_employers':['Acme']})
    class Model:
        def match_field(self,f,facts,*args):
            assert 'worked_outside_resume' not in facts and 'contacts_outside_resume' not in facts
            assert 'summer_2027_relocate' not in facts
            return {'fact_key':None,'template_id':None}
    with pytest.raises(Blocked):resolve(store,job['host']+'|acme',field('Have you ever been employed by this company?'),Model(),context={**job,'title':'Software Engineer Fall 2027'})


def test_prior_internship_uses_hashed_resume_evidence_and_rejects_future_only(store,tmp_path,job):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
    import hashlib
    def resume(year):
        writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(f'BT /F1 12 Tf 50 700 Td (EXPERIENCE) Tj 0 -14 Td (Acme - 01/{year} - 03/{year}) Tj 0 -14 Td (AI Engineer Intern) Tj ET'.encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
        path=tmp_path/f'resume-{year}.pdf'
        with path.open('wb') as f:writer.write(f)
        data=path.read_bytes();h=hashlib.sha256(data).hexdigest();(store.root/'documents'/(h+'.pdf')).write_bytes(data)
        store.db.execute("UPDATE documents SET hash=?,filename=? WHERE kind='resume'",(h,h+'.pdf'))
        return h
    h=resume('2020');f=field('Do you have prior internship or co-op experience?','select',['Yes','No'])
    a=resolve(store,job['host'],f)
    assert a['value']=='Yes' and a['provenance']['resume_hash']==h and a['provenance']['resume_quote']=='AI Engineer Intern'
    resume('2099')
    with pytest.raises(Blocked):resolve(store,job['host'],f)


def test_semantic_model_cannot_turn_unrelated_boolean_into_new_claim(store,job):
    class Model:
        def match_field(self,*args):return {'fact_key':'needs_sponsorship','template_id':None}
    with pytest.raises(Blocked):resolve(store,job['host'],field('Have you published five research papers?','select',['Yes','No']),Model())


def test_long_writing_sample_is_assembled_within_question_sentence_limit(store,job,package):
    store.put_template('project','I built a Python service. I added tracing. I measured latency. I improved retries. I documented the rollout.')
    class Model:
        def choose_sentences(self,label,choices,*args):return [c['id'] for c in choices[:3]]
    f=field('Describe a project you built in 3-4 sentences.','textarea')
    a=resolve(store,job['host'],f,Model(),context=job)
    assert a['value']=='I built a Python service. I added tracing. I measured latency.'
    assert len(a['provenance']['sample_parts'])==3
    package['answers']=[a];package['steps']=[]
    validate_package(store,job,package)


def test_sample_selection_cannot_exceed_word_limit(store,job):
    store.put_template('project','I built a Python service and measured every deployment carefully.')
    class Model:
        def choose_sentences(self,label,choices,*args):return [c['id'] for c in choices]
    with pytest.raises(Blocked,match='answer_too_long'):resolve(store,job['host'],field('Describe a project you built; maximum 5 words.','textarea'),Model(),context=job)


def test_numbered_examples_inherit_shared_sentence_limit(store,job,package):
    tid=store.put_template('project','I built a service. I added tracing. I measured latency. I improved retries. I documented the rollout.')
    instruction=field('Each bullet should be concise, no longer than 3-4 sentences each. First example:','textarea')
    instruction['required']=False
    class Model:
        def match_field(self,*args):return {'fact_key':None,'template_id':tid}
        def choose_sentences(self,label,choices,context,*args):
            assert context['max_sentences']==4
            return [c['id'] for c in choices[:3]]
    f=field('Second example:','textarea')
    a=resolve(store,job['host'],f,Model(),context={**job,'form_questions':[instruction['label'],f['label']]})
    assert len(a['provenance']['sample_parts'])==3
    package['answers']=[a];package['steps']=[{'fields':[instruction,f]}]
    validate_package(store,job,package)


def test_cached_writing_is_reassembled_for_a_shorter_field(store,job):
    store.put_template('project','I built a service. I added tracing. I measured latency. I improved retries. I documented the rollout.')
    class Model:
        def choose_sentences(self,label,choices,context,maxlength):return [c['id'] for c in choices[:1 if maxlength<30 else 3]]
    f=field('Describe a project you built','textarea');f['maxlength']=70
    first=resolve(store,job['host'],f,Model(),context=job)
    f['maxlength']=27
    shorter=resolve(store,job['host'],f,Model(),context=job)
    assert len(shorter['value'])<len(first['value']) and shorter['value']=='I built a service.'
    assert resolve(store,job['host'],f,context=job)==shorter


def test_high_school_is_distinct_from_university(store,job):
    store.put_facts({'high_school':'Dougherty Valley High School','school':'UC Berkeley'})
    assert resolve(store,job['host'],field('What high school did you attend?'))['value']=='Dougherty Valley High School'
    assert resolve(store,job['host'],field('Which university do you attend?'))['value']=='UC Berkeley'
    with pytest.raises(Blocked):resolve(store,job['host'],field('High school GPA','number'))


def test_applied_role_comes_from_posting(store,job):
    f=field('What internship position are you applying for?')
    assert resolve(store,job['host'],f,context=job)['value']==job['title']
    assert resolve(store,job['host'],field(f['label'],'select',['Software Engineering Intern','Accounting Intern']),context=job)['value']=='Software Engineering Intern'
    with pytest.raises(Blocked):resolve(store,job['host'],field(f['label'],'select',['Accounting Intern']),context=job)


def test_tailored_writing_is_cached_and_invalidated_by_source_and_posting(store,job,package):
    store.update_settings({'tailored_writing':True})
    tid=store.put_template('experience','I built Python services around production workflows.')
    class Model:
        def draft_answer(self,label,choices,context,maxlength):
            return {'answer':'I want to build Python systems that people rely on every day.','sentence_ids':[choices[0]['id']]}
    f=field('Why do you want to work here?','textarea')
    a=resolve(store,job['host'],f,Model(),context=job)
    package['answers']=[a];package['steps']=[]
    validate_package(store,job,package)
    with pytest.raises(Blocked):validate_package(store,{**job,'description':'Different posting'},package)
    a['value']+=' Invented claim.'
    with pytest.raises(Blocked):validate_package(store,job,package)
    store.put_template('experience','I built TypeScript services around production workflows.',tid)
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)


def test_context_choices_use_confirmed_skills_season_and_locality(store,job):
    store.update_settings({'contextual_preferences':True})
    store.put_facts({'skills':'React, TypeScript, Python, SQL, FastAPI, Docker, AWS, GCP','earliest_start':'2027-05','summer_2027_relocate':'Yes','onsite':'Yes'})
    options=['Fall 2026','Winter 2026','Spring 2027','Summer 2027']
    assert resolve(store,job['host'],field('Which internship position are you available for?','select',options),context=job)['value']=='Summer 2027'
    areas=['Product Engineering','Backend/Infrastructure','Security Engineering','Open to any area']
    first=resolve(store,job['host'],field('Which type of engineering work are you most excited to do? Select your first choice.','select',areas),context=job)
    second=resolve(store,job['host'],field('Which type of engineering work are you most excited to do? Select your second choice.','select',areas),context=job)
    assert first['value']=='Backend/Infrastructure' and second['value']=='Product Engineering'
    assert resolve(store,job['host'],field('Please select the location where you can work','select',['Reno, NV','San Francisco, CA']),context=job)['value']=='San Francisco, CA'
    with pytest.raises(Blocked):resolve(store,job['host'],field('Are you legally authorized to work in France?','select',['Yes','No']),context=job)


def test_context_choices_are_opt_in_and_do_not_guess_season(store,job):
    store.put_facts({'earliest_start':'2027-05','summer_2027_relocate':'Yes'})
    f=field('Which internship position are you available for?','select',['Summer 2027','Fall 2026'])
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)
    store.update_settings({'contextual_preferences':True})
    store.put_facts({'earliest_start':'2028-05'})
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)


def test_city_autocomplete_alias_preserves_location(store):
    from hireme.answers import resolve
    from hireme.util import Blocked
    import pytest
    store.put_facts({'location':'Berkeley, CA'})
    field={'label':'Location (City)*','type':'combobox','required':True,'options':['Berkeley, California, United States'],'maxlength':-1}
    assert resolve(store,'job-boards.greenhouse.io',field)['value']=='Berkeley, California, United States'
    with pytest.raises(Blocked):resolve(store,'job-boards.greenhouse.io',{**field,'options':['Berkeley, England, United Kingdom']})


@pytest.mark.parametrize('label',[
    'Why are you excited about Lightfield and this role?',
    'What makes you excited about Koah?',
    'Describe your prior experience, if any, with Nvidia Cosmos or a comparable world foundation model.',
])
def test_batch_writing_wording_uses_grounded_drafting(store,job,label):
    store.update_settings({'tailored_writing':True})
    store.put_template('experience','I built Python services for manufacturing workflows.')
    class Model:
        def draft_answer(self,question,choices,context,maxlength):
            assert context['single_line']
            return {'answer':'I built Python services.\nI want to apply that experience to this role.',
                    'sentence_ids':[choices[0]['id']]}
    answer=resolve(store,job['host'],field(label),Model(),context=job)
    assert '\n' not in answer['value']
    assert answer['provenance']['tailored']
    assert resolve(store,job['host'],field(label),context=job)==answer


def test_batch_option_only_labels_use_confirmed_locality_and_discovery(store,job):
    store.update_settings({'contextual_preferences':True})
    store.put_facts({'onsite':'Yes','location':'Berkeley, CA'})
    offices=['San Francisco HQ - 181 Fremont Street','New York City - 1 World Trade']
    assert resolve(store,job['host'],field('; '.join(offices),'radio',offices),context=job)['value']==offices[0]
    options=['LinkedIn','Indeed','News article or press coverage','AI chat bot','Search engine','Social media','Other']
    result=resolve(store,job['host'],field('; '.join(options),'radio',options),context={**job,'source':'ash:koahlabs'})
    assert result['value']=='Other'
    with pytest.raises(Blocked):
        resolve(store,job['host'],field('; '.join(options),'radio',options),context={**job,'source':'user'})


def test_no_ai_application_question_still_requires_human(store,job):
    with pytest.raises(Blocked,match='human_work_sample'):
        resolve(store,job['host'],field("Tell me about the most exciting project you've built (please type answer without AI)"))


def test_relocation_need_uses_current_location_not_willingness(store,job):
    store.update_settings({'contextual_preferences':True})
    store.put_facts({'location':'Berkeley, CA','relocate':'Yes'})
    question=field('Do you require relocation to the SF Bay Area?','radio',['Yes','No'])
    assert resolve(store,job['host'],question,context=job)['value']=='No'
    store.put_facts({'location':'Boston, MA'})
    with pytest.raises(Blocked):resolve(store,job['host'],question,context=job)


def test_hq_work_question_uses_confirmed_onsite_preference(store,job):
    store.put_facts({'onsite':'Yes'})
    question=field('Can you work from our San Francisco HQ (4 days a week)?','radio',['Yes','No'])
    assert resolve(store,job['host'],question,context=job)['value']=='Yes'


def test_current_location_wording_uses_verified_geographic_option(store,job):
    store.put_facts({'location':'Berkeley, CA'})
    question=field('Where are you currently located?','combobox',['Berkeley, California, United States','Boston, Massachusetts, United States'])
    assert resolve(store,job['host'],question,context=job)['value']=='Berkeley, California, United States'


def test_combined_bay_area_and_onsite_question_needs_both_facts(store,job):
    store.update_settings({'contextual_preferences':True})
    store.put_facts({'location':'Berkeley, CA','onsite':'Yes'})
    options=["Yes, I'm currently located in the Bay Area and am open to work 5 days a week in-office", "No, but I'm planning to relocate to the Bay Area", "No, but I'm open to relocating to the Bay Area", "No, and I am not open to relocation"]
    question=field('Are you currently located in the San Francisco Bay Area and able to work from our office 5 days per week?','radio',options)
    assert resolve(store,job['host'],question,context=job)['value']==options[0]
    store.put_facts({'location':'Boston, MA'})
    with pytest.raises(Blocked):resolve(store,job['host'],question,context=job)


def test_tailored_answers_can_use_resume_without_separate_templates_and_revalidate_hash(store,job):
    from reportlab.pdfgen import canvas
    import io,hashlib
    stream=io.BytesIO();c=canvas.Canvas(stream)
    c.drawString(40,700,'Built Python services and reduced processing time by 20 percent.')
    c.save();data=stream.getvalue();h=hashlib.sha256(data).hexdigest()
    (store.root/'documents'/(h+'.pdf')).write_bytes(data)
    store.db.execute("UPDATE documents SET hash=?,filename=? WHERE kind='resume'",(h,h+'.pdf'))
    store.update_settings({'tailored_writing':True})
    class Model:
        def draft_answer(self,label,choices,context,maxlength):
            source=next(x for x in choices if 'resume_hash' in x)
            return {'answer':source['text'],'sentence_ids':[source['id']]}
    f=field('First example:','textarea')
    answer=resolve(store,job['host'],f,Model(),context=job)
    assert answer['provenance']['sample_parts'][0]['resume_hash']==h
    assert resolve(store,job['host'],f,context=job)==answer
    (store.root/'documents'/(h+'.pdf')).write_bytes(data+b'changed')
    with pytest.raises(Blocked):resolve(store,job['host'],f,context=job)


def test_multiline_template_formats_for_text_and_validates_before_submit(store,job,package):
    store.put_template('motivation','I build Python services.\nI enjoy improving their reliability.')
    f=field('Why do you want this role?')
    answer=resolve(store,job['host'],f,context=job)
    assert '\n' not in answer['value']
    package['answers']=[answer];package['steps']=[]
    validate_package(store,job,package)


def test_graduation_year_is_derived_from_confirmed_month(store,job):
    assert resolve(store,job['host'],field('What year will you graduate?','select',['2027','2028','2029']))['value']=='2028'
