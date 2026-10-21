"""Source-derived synthetic GET shapes; no network or claimed live ACK."""
import copy

import pytest

from hireme.util import Blocked
from hireme.workday_nvidia_readback import match, normalize


def rows(*records):
    return {'total':len(records),'data':list(records)}


def uncertain(resource,data,expected,**kwargs):
    with pytest.raises(Blocked) as raised:
        match(resource,data,expected,**kwargs)
    assert raised.value.reason=='remote_draft_uncertain'


def test_name_aggregate_has_no_invented_top_level_id_or_client_preferred_check():
    data={'legalName':{'id':'name-1','country':{'id':'us','descriptor':'United States'},
                      'deferToLegalName':False,'firstName':'Ada','lastName':'Example',
                      'fullyComposedName':'Ada Example','preferredName':False},'preferredName':None}
    expected={'legalName':{'firstName':'Ada','lastName':'Example','country':{'id':'us'}}}
    assert match('name',data,expected)==data
    assert 'id' not in normalize('name',data)
    uncertain('name',data,{**expected,'preferredCheck':False})


def test_address_never_infers_unreturned_street_or_postal_from_composed_text():
    data=rows({'id':'address-1','descriptor':'Synthetic address','city':'Berkeley',
               'country':{'id':'us','descriptor':'United States'},'countryRegion':{'id':'ca'},
               'fullyComposedAddress':'123 Synthetic Street, Berkeley, CA 94720'})
    assert match('address',data,{'city':'Berkeley','country':{'id':'us'}})['id']=='address-1'
    uncertain('address',data,{'city':'Berkeley','addressLine1':'123 Synthetic Street'})
    uncertain('address',data,{'postalCode':'94720'})


def test_phone_proves_only_explicit_native_number_type_and_code():
    data=rows({'id':'phone-1','descriptor':'Synthetic phone','phoneNumber':'2025550100',
               'countryPhoneCode':{'id':'us-1','descriptor':'+1'},'phoneType':{'id':'mobile'}})
    assert match('phonenumber',data,{'phoneNumber':'2025550100','phoneType':{'id':'mobile'},
                                    'countryPhoneCode':{'id':'us-1'}})['id']=='phone-1'
    uncertain('phonenumber',data,{'phoneNumber':2025550100})
    uncertain('phonenumber',data,{'extension':'123'})


def work(**changes):
    return {'id':'work-1','startMonth':9,'startYear':'2025','endMonth':None,'endYear':None,
            'jobTitle':'Engineer','jobHistoryCompanyName':'Synthetic Company','location':'Berkeley',
            'responsibilitiesAndAchievements':'Built a synthetic system.','currentlyWorkHere':True,**changes}


def test_work_projection_uses_explicit_month_precision_and_source_field_names():
    expected={'companyName':'Synthetic Company','roleDescription':'Built a synthetic system.',
              'startDate':'2025-09','currentlyWorkHere':True}
    result=match('workexperiences',rows(work()),expected,precision={'startDate':'YYYY-MM','endDate':'YYYY-MM'})
    assert result['startDate']=='2025-09' and 'endDate' not in result
    assert 'startDate' not in normalize('workexperiences',rows(work()))[0]
    uncertain('workexperiences',rows(work()),expected)
    uncertain('workexperiences',rows(work()),{**expected,'startDate':'2025-09-01'},precision='YYYY-MM-DD')


@pytest.mark.parametrize('changes',[{'startMonth':True},{'startMonth':13},{'startYear':'0000'},
                                    {'startYear':2025},{'endYear':'2026','endMonth':None}])
def test_work_split_date_cannot_be_repaired_or_guessed(changes):
    uncertain('workexperiences',rows(work(**changes)),{'jobTitle':'Engineer'},precision='YYYY-MM')


def education(**changes):
    return {'id':'education-1','degree':{'id':'degree-1','descriptor':'BS'},
            'fieldOfStudy':{'id':'field-1','descriptor':'Computer Science'},
            'firstYearAttended':'2024-08-01','lastYearAttended':'2028-05-01','gradeAverage':'3.9',
            'school':{'id':'school-1','descriptor':'Synthetic University'},'schoolName':'Synthetic University',**changes}


def test_education_preserves_native_dates_and_compares_only_reviewed_precision():
    data=rows(education())
    expected={'school':{'id':'school-1'},'firstYearAttended':'2024','lastYearAttended':'2028'}
    assert normalize('educations',data)[0]['firstYearAttended']=='2024-08-01'
    assert match('educations',data,expected,precision='YYYY')['id']=='education-1'
    uncertain('educations',data,expected)
    uncertain('educations',data,{**expected,'lastYearAttended':'2029'},precision='YYYY')
    uncertain('educations',rows(education(lastYearAttended='2028')),{'lastYearAttended':'2028-05'},precision='YYYY-MM')


@pytest.mark.parametrize('value',['2024-02-30','2024-13-01','0000','2024-00','2024/08/01',2024])
def test_year_only_comparison_still_rejects_invalid_actual_date_components(value):
    uncertain('educations',rows(education(firstYearAttended=value)),{'firstYearAttended':'2024'},precision='YYYY')


def test_education_validates_expected_date_and_exact_reference_even_at_year_precision():
    uncertain('educations',rows(education()),{'firstYearAttended':'2024-02-30'},precision='YYYY')
    uncertain('educations',rows(education()),{'degree':{'id':'other'}})


def test_unrelated_native_history_with_missing_optional_date_is_not_a_false_match():
    data=rows(education(id='unrelated',lastYearAttended=None),education())
    assert match('educations',data,{'lastYearAttended':'2028-05'},precision='YYYY-MM')['id']=='education-1'


def test_duplicate_matching_records_and_incomplete_pages_are_uncertain():
    one={'id':'web-1','url':'https://synthetic.example/'}
    uncertain('webaddresses',rows(one,{**one,'id':'web-2'}),{'url':one['url']})
    uncertain('webaddresses',rows(one,one),{'url':one['url']})
    uncertain('webaddresses',{'total':2,'data':[one]},{'url':one['url']})
    uncertain('webaddresses',{'total':True,'data':[one]},{'url':one['url']})


def test_previous_worker_and_resume_require_exact_native_fields():
    data=rows({'id':'previous-1','candidateIsPreviousWorker':False,'manager':None,'email':None,
               'employeeID':None,'location':None})
    assert match('previousworker',data,{'candidateIsPreviousWorker':False})['id']=='previous-1'
    uncertain('previousworker',data,{'candidateIsPreviousWorker':0})
    resume={'attachments':[{'id':'attachment-1','file':'file-1','fileName':'approved.pdf','fileLength':101,
                            'contentType':{'id':'Content_Type_ID=application/pdf','descriptor':'PDF'}}]}
    assert match('resumeattachments',resume,{'file':'file-1','fileName':'approved.pdf','fileLength':101})['id']=='attachment-1'
    uncertain('resumeattachments',resume,{'file':'another-file','fileName':'approved.pdf'})
    uncertain('resumeattachments',resume,{'fileLength':'101'})


def package(**changes):
    return {'source':{'id':'source-1','descriptor':'Company site','referenceID':'site','category':None,
                      'applicantSourceType':None},'sourcePopulatedFromUrl':False,
            'termsAndConditions':'Synthetic agreement','termsAndConditionsConsentMoment':'2026-10-21T10:01:00Z',
            'termsAndConditionsLockedMoment':'2026-10-21T10:00:00Z','termsAndConditionsUpdated':False,
            'hispanicOrLatino':False,'gender':{'id':'gender-1'},'ethnicity':[{'id':'ethnicity-1'}],
            'veteranStatuses':[{'id':'veteran-1'}],'jobPostingAnchor':{'id':'posting-1','descriptor':None},**changes}


def test_source_uses_native_package_values_without_defaulting_missing_approved_data():
    expected={'source':{'id':'source-1'},'sourcePopulatedFromUrl':False,'referredBy':''}
    assert match('source',package(),expected)['source']['id']=='source-1'
    uncertain('source',package(),{**expected,'referredBy':'A real person'})
    data=package();data.pop('sourcePopulatedFromUrl')
    uncertain('source',data,expected)
    uncertain('source',package(),{'referredBy':''})


def test_us_disclosure_projects_observed_package_lists_without_copying_request_values():
    expected={'gender':{'id':'gender-1'},'ethnicity':{'id':'ethnicity-1'},
              'ethnicityMulti':[{'id':'ethnicity-1'}],'veteranStatus':{'id':'veteran-1'},'hispanicOrLatino':False}
    assert match('uspersonalinfo',package(),expected)['hispanicOrLatino'] is False
    uncertain('uspersonalinfo',package(),{**expected,'hispanicOrLatino':True})
    data=package();data.pop('hispanicOrLatino')
    uncertain('uspersonalinfo',data,expected)
    uncertain('uspersonalinfo',package(ethnicity=[]),expected)


def test_terms_require_explicit_unmodified_consent_strictly_after_the_locked_moment():
    assert match('termsandconditions',package(),{'acceptTermsAndAgreements':True})['acceptTermsAndAgreements'] is True
    uncertain('termsandconditions',package(),{'acceptTermsAndAgreements':1})
    for changes in ({'termsAndConditionsUpdated':True},{'termsAndConditionsUpdated':0},
                    {'termsAndConditionsConsentMoment':'2026-10-21T10:00:00Z'},
                    {'termsAndConditionsConsentMoment':'2026-10-21T09:59:59Z'},
                    {'termsAndConditionsConsentMoment':'2026-10-21T10:01:00'},
                    {'termsAndConditionsConsentMoment':'2026-10-21T10:01:00+00:60'},
                    {'termsAndConditionsLockedMoment':''}):
        uncertain('termsandconditions',package(**changes),{'acceptTermsAndAgreements':True})


@pytest.mark.parametrize('resource,data,expected',[
    ('package',{'error':'synthetic'},{'hispanicOrLatino':False}),
    ('addresses',{'total':1,'data':[None]},{'city':'Berkeley'}),
    ('unknown',{}, {'any':'value'}),
    ('name',{'legalName':{'firstName':'Ada'}},{'legalName':{'firstName':'Ada'}}),
])
def test_unknown_or_error_shapes_hold_with_fixed_reason(resource,data,expected):
    uncertain(resource,data,expected)


def test_helpers_do_not_mutate_native_readback_or_approved_payload():
    data,expected=package(),{'source':{'id':'source-1'},'referredBy':''}
    before=copy.deepcopy((data,expected))
    match('source',data,expected)
    assert (data,expected)==before
