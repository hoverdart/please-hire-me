from pathlib import Path

import pytest

from hireme.policy import eligible
from hireme.util import Blocked
from hireme.worker import cycle


PHD_TITLE = 'PhD Research Intern - Cross-Disciplinary Vision Science - Summer 2027'


@pytest.mark.parametrize('degree', ['BS', 'B.S.', "Bachelor's degree", 'Bachelor of Science', 'MS', 'Master of Science'])
def test_doctoral_internship_title_excludes_confirmed_non_doctoral_degree(store, job, degree):
    store.put_facts({'degree': degree, 'school': 'University of California, Berkeley'})
    with pytest.raises(Blocked, match='degree_mismatch'):
        eligible({**job, 'title': PHD_TITLE}, store.settings(), store.facts())


@pytest.mark.parametrize('degree', ['PhD', 'Ph.D.', 'Doctor of Philosophy', 'Doctoral degree'])
def test_doctoral_title_uses_confirmed_current_degree(store, job, degree):
    store.put_facts({'degree': degree})
    assert eligible({**job, 'title': PHD_TITLE}, store.settings(), store.facts())[0] > 0


@pytest.mark.parametrize('degree', [None, 'Engineering student', 'Professional degree'])
def test_missing_or_unrecognized_current_degree_requires_review_without_institution_inference(store, job, degree):
    store.put_facts({'school': 'University of California, Berkeley', 'highest_completed_degree': 'PhD'})
    if degree is not None: store.put_facts({'degree': degree})
    with pytest.raises(Blocked, match='degree_requirement_review'):
        eligible({**job, 'title': PHD_TITLE}, store.settings(), store.facts())


@pytest.mark.parametrize('requirement', [
    'You must be currently enrolled in a PhD program in Computer Science or a related field.',
    'Applicants are required to be pursuing a Ph.D. in Electrical Engineering.',
    'A PhD is required for this research internship.',
    'This internship is limited to doctoral students.',
])
def test_explicit_mandatory_doctoral_requirement_is_enforced_without_title_keyword(store, job, requirement):
    store.put_facts({'degree': 'BS'})
    with pytest.raises(Blocked, match='degree_mismatch'):
        eligible({**job, 'description': requirement}, store.settings(), store.facts())


@pytest.mark.parametrize('title,description', [
    ('Software Engineer Intern Summer 2027', 'Build Python software. PhD preferred.'),
    ('Research Intern Summer 2027 - PhD preferred', 'Build Python software.'),
    ('Research Intern Summer 2027 - BS, MS, or PhD', 'Build Python software.'),
    ('Software Engineer Intern Summer 2027', 'No PhD is required. Build Python software.'),
    ('Software Engineer Intern Summer 2027', 'A PhD is not required. Build Python software.'),
    ('Software Engineer Intern Summer 2027', 'Work with scientists pursuing a PhD. Build Python software.'),
    ('Software Engineer Intern Summer 2027', 'You must collaborate with PhD researchers. Build Python software.'),
    ('Software Engineer Intern Summer 2027', 'We only use Python and collaborate with PhD researchers.'),
    ('Software Engineer Intern Summer 2027', 'Candidates must be team players. Partner with PhD researchers.'),
    ('Software Engineer Intern Summer 2027', "Applicants must be pursuing a bachelor's, master's, or PhD degree."),
])
def test_preferences_degree_alternatives_and_colleague_descriptions_do_not_create_doctoral_gate(store, job, title, description):
    store.put_facts({'degree': 'BS'})
    assert eligible({**job, 'title': title, 'description': description}, store.settings(), store.facts())[0] > 0


def test_ordinary_software_internship_does_not_require_a_degree_fact(store, job):
    assert 'degree' not in store.facts()
    assert eligible(job, store.settings(), store.facts())[0] > 0


@pytest.mark.parametrize('requirement',[
    'Must currently be at least a second-year Ph.D. student in Computer Science.',
    'Must be currently a second year doctoral student in Computer Science.',
    '<h2>What we need to see:</h2><ul><li>Must currently be at least a second-year Ph.D. student.</li></ul>',
    'M.S. or Ph.D. student (or evidence of equivalent level of expertise) in Computer Science.',
    'Requirements:\n3 year PhD student and above.',
    'Qualifications:\nCurrently a Ph.D. candidate in Computer Science.',
])
def test_direct_student_standing_requirements_exclude_undergraduate_before_browsing(store,job,requirement):
    store.put_facts({'degree':'BS'})
    restricted={**job,'description':requirement}
    store.upsert_job(restricted)
    class ForbiddenBrowser:
        def __init__(self,*args):raise AssertionError('An incompatible degree reached application browsing')
    result=cycle(store,Path('.'),discover=False,browser_factory=ForbiddenBrowser,job_ids=[job['id']])
    assert result['attempts']==0 and result['reasons']=={'degree_mismatch':1}
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('requirement',[
    'Must currently be at least a second-year Ph.D. student in Computer Science.',
    'Requirements:\n3 year PhD student and above.',
])
def test_confirmed_doctoral_degree_does_not_invent_current_year_of_study(store,job,requirement):
    store.put_facts({'degree':'PhD'})
    with pytest.raises(Blocked,match='degree_requirement_review'):
        eligible({**job,'description':requirement},store.settings(),store.facts())


@pytest.mark.parametrize('description',[
    'PhD students mentor interns on our team. Build Python software.',
    'PhD students in our team mentor interns. Build Python software.',
    'Our team includes second-year PhD students. Build Python software.',
    'You must collaborate with second-year PhD students. Build Python software.',
    'PhD student preferred. Build Python software.',
    'PhD student (preferred). Build Python software.',
    'Requirements:\nBS, MS or PhD students in Computer Science.',
])
def test_student_wording_does_not_turn_team_prose_preferences_or_bachelor_alternatives_into_a_gate(store,job,description):
    store.put_facts({'degree':'BS'})
    assert eligible({**job,'description':description},store.settings(),store.facts())[0]>0


def test_graduate_alternative_does_not_invent_a_confirmed_masters_students_year(store,job):
    store.put_facts({'degree':'MS'})
    with pytest.raises(Blocked,match='degree_requirement_review'):
        eligible({**job,'description':'Requirements:\nSecond-year MS or PhD student in Computer Science.'},store.settings(),store.facts())


@pytest.mark.parametrize('title,description', [
    ('Systems Software Intern, Aerial PHY- Summer 2027',
     'Pursuing PhD or MS in Electrical and Computer Engineering, Electrical Engineering, Computer Science or related.'),
    ('Systems Software Intern, Aerial PHY- Summer 2027',
     '<h2>What we need to see:</h2><ul><li>Pursuing PhD or MS in Electrical and Computer Engineering.</li></ul>'),
    ('Software Engineer Intern Summer 2027', 'You must be pursuing an MS or PhD in Computer Science.'),
    ('Software Engineer Intern Summer 2027', "Applicants are required to be enrolled in a master's program."),
    ('Software Engineer Intern Summer 2027', 'An M.S. degree is required for this internship.'),
    ('Software Engineer Intern Summer 2027', "This internship is limited to master's students."),
    ('Software Engineer Intern Summer 2027', 'Only current MS students can apply.'),
    ('Software Engineer Intern Summer 2027', "What we need to see:\nPursuing a Master's degree in Computer Science."),
    ('MS Software Engineering Intern Summer 2027', 'Build Python software.'),
    ("Master's Software Engineering Intern Summer 2027", 'Build Python software.'),
    ('Research Intern Summer 2027 - MS/PhD', 'Build Python software.'),
    ('Research Intern Summer 2027 - PhD or MS', 'Build Python software.'),
    ('Software Engineer Intern Summer 2027 - M.S.', 'Build Python software.'),
    ('NVIDIA 2027 Internships: Software Engineering - PhD', 'Build Python software.'),
    ('MS Software Engineering Internships Summer 2027', 'Build Python software.'),
])
def test_required_graduate_study_excludes_confirmed_undergraduate_degree(store, job, title, description):
    store.put_facts({'degree':'B.S.','school':'University of California, Berkeley'})
    with pytest.raises(Blocked,match='degree_mismatch'):
        eligible({**job,'title':title,'description':description},store.settings(),store.facts())


@pytest.mark.parametrize('degree',['MS','M.S.','Master of Science',"Master's degree",'PhD','Ph.D.'])
def test_graduate_alternative_accepts_confirmed_masters_or_doctoral_study(store,job,degree):
    store.put_facts({'degree':degree})
    assert eligible({**job,'description':'Pursuing PhD or MS in Computer Science or related.'},store.settings(),store.facts())[0]>0


@pytest.mark.parametrize('degree',[None,'Engineering student','Professional degree'])
def test_graduate_requirement_does_not_infer_current_study_from_school_or_completed_degree(store,job,degree):
    store.put_facts({'school':'University of California, Berkeley','highest_completed_degree':'MS'})
    if degree is not None:store.put_facts({'degree':degree})
    with pytest.raises(Blocked,match='degree_requirement_review'):
        eligible({**job,'description':'Pursuing PhD or MS in Computer Science.'},store.settings(),store.facts())


@pytest.mark.parametrize('title,description',[
    ('Research Intern Summer 2027 - BS/MS/PhD','Build Python software.'),
    ('Research Intern Summer 2027 - MS or Bachelor degree','Build Python software.'),
    ('Software Engineer Intern Summer 2027',"Pursuing a bachelor's, master's, or PhD degree in Computer Science."),
    ('Software Engineer Intern Summer 2027',"Applicants must be pursuing a bachelor's or master's degree."),
    ('Software Engineer Intern Summer 2027','You must be pursuing a BS or PhD in Computer Science.'),
    ('Software Engineer Intern Summer 2027',"Master's degree preferred. Build Python software."),
    ('Research Intern Summer 2027 - MS preferred','Build Python software.'),
    ('Software Engineer Intern Summer 2027','Pursuing MS preferred. Build Python software.'),
    ('Software Engineer Intern Summer 2027',"A master's degree in Computer Science is preferred."),
    ('Software Engineer Intern Summer 2027','No MS is required. Build Python software.'),
    ('Software Engineer Intern Summer 2027',"A master's degree is not required. Build Python software."),
    ('Software Engineer Intern Summer 2027','Work with scientists pursuing MS degrees. Build Python software.'),
    ('Software Engineer Intern Summer 2027','You must collaborate with MS and PhD researchers.'),
    ('Software Engineer Intern Summer 2027','Candidates must have good communication skills. Partner with MS researchers.'),
    ('Software Engineer Intern Summer 2027','The team includes graduate researchers pursuing PhDs and masters degrees.'),
    ('Software Engineer Intern Summer 2027 - Cambridge, MA','Build Python software.'),
    ('Software Engineer Intern Summer 2027 - MS Office','Build Python software.'),
    ('Software Engineer Intern Summer 2027','You must have MS SQL experience. Build Python software.'),
])
def test_bachelor_alternatives_preferences_and_team_prose_do_not_create_graduate_gate(store,job,title,description):
    store.put_facts({'degree':'BS'})
    assert eligible({**job,'title':title,'description':description},store.settings(),store.facts())[0]>0


@pytest.mark.parametrize('changes',[
    {'title':PHD_TITLE},
    {'description':'Pursuing PhD or MS in Electrical and Computer Engineering, Electrical Engineering, Computer Science or related.'},
    {'description':'Must currently be at least a second-year Ph.D. student in Computer Science.'},
])
def test_degree_gate_runs_before_browser_or_model_and_before_final_intent(store, job, package, changes):
    store.put_facts({'degree': 'BS'})
    graduate = {**job, **changes}
    store.upsert_job(graduate)
    class ForbiddenBrowser:
        def __init__(self, *args): raise AssertionError('A job requiring a different degree reached application browsing')
    result = cycle(store, Path('.'), discover=False, browser_factory=ForbiddenBrowser, job_ids=[job['id']])
    assert result['attempts'] == result['confirmed'] == 0
    assert result['reasons'] == {'degree_mismatch': 1}
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()
    assert not store.db.execute('SELECT 1 FROM model_requests').fetchone()
    # A posting changed after preparation must also be rejected at the final
    # durable-intent boundary, independent of earlier worker screening.
    store.upsert_job(job)
    from hireme.util import digest
    aid = store.prepare(job, {**package, 'facts_hash': digest(store.facts())})
    store.upsert_job(graduate)
    with pytest.raises(Blocked, match='degree_mismatch'): store.begin_submit(aid)
    assert store.application_record(aid)['state'] == 'prepared'
    assert not store.db.execute("SELECT 1 FROM events WHERE kind='submit_intent'").fetchone()
