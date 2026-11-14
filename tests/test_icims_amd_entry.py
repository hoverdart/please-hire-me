"""The observed AMD careers bridge must retain identity and every write fence."""
import pytest

from hireme import icims
from hireme.application_evidence import ats_for
from hireme.ats_adapters import adapter_for
from hireme.browser_agent import AdaptiveBrowser
from hireme.browser_agent_protocol import guard_pending, validate_browser_package
from hireme.util import Blocked, application_identity, digest, now

CMS = 'https://careers.amd.com/careers-home/jobs/93935'
HOSTED = 'https://careers-amd.icims.com/jobs/93935/job'


def amd(job, *, url=CMS, host='careers-amd.icims.com', company='AMD'):
    return {**job, 'id': digest(url), 'url': url, 'host': host, 'company': company}


@pytest.mark.parametrize('host', ['careers-amd.icims.com', 'careers.amd.com'])
def test_known_bridge_navigates_without_rewriting_saved_source(job, host):
    source = amd(job, host=host)
    original = dict(source)
    assert adapter_for(source).navigation(source) == (HOSTED + '?in_iframe=1', None)
    assert source == original
    assert ats_for(source['url']) == 'icims'
    assert application_identity(source['url']) == application_identity(HOSTED)
    assert application_identity(CMS + '/?utm_source=public') == application_identity(HOSTED)


@pytest.mark.parametrize('url', [
    'http://careers.amd.com/careers-home/jobs/93935',
    'https://careers.amd.com.attacker.invalid/careers-home/jobs/93935',
    'https://other.careers.amd.com/careers-home/jobs/93935',
    'https://careers.amd.com:444/careers-home/jobs/93935',
    'https://person@careers.amd.com/careers-home/jobs/93935',
    'https://careers.amd.com/careers-home/jobs/93935/another',
    'https://careers.amd.com/careers-home/jobs/%39%33%39%33%35',
    'https://careers.amd.com/careers-home/jobs/1234567890123',
    'https://careers.amd.com/search?job=93935',
])
def test_unknown_bridge_shapes_gain_no_tenant_or_requisition_alias(url):
    assert icims.posting_url(url) == url
    assert icims.identity(url) is None
    assert application_identity(url) != application_identity(HOSTED)
    assert ats_for(url) == 'other'


def test_bridge_cannot_claim_another_tenants_host(job):
    source = amd(job, host='careers-other.icims.com')
    with pytest.raises(Blocked, match='icims_posting_unrecognized'):
        adapter_for(source).navigation(source)


def test_missing_declared_host_cannot_admit_a_known_non_bridge_url(store, job):
    source = {**job, 'host': None,
        'url': 'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Software-Intern_JR2023495'}
    with pytest.raises(Blocked, match='unapproved_destination'):
        AdaptiveBrowser(store).apply(source, live=False)


def test_legacy_empty_userinfo_comparison_never_grants_navigation(store, job):
    url = 'https://@careers.amd.com/careers-home/jobs/93935'
    # Legacy normalization remains conservative for duplicate comparisons,
    # while raw employer navigation still refuses any userinfo.
    assert application_identity(url) == application_identity(HOSTED)
    assert icims.posting_url(url) == url and icims.identity(url) is None
    source = amd(job, url=url)
    with pytest.raises(Blocked, match='icims_posting_unrecognized'):
        adapter_for(source).navigation(source)
    with pytest.raises(Blocked, match='unapproved_destination'):
        AdaptiveBrowser(store).apply(source, live=False)


@pytest.mark.parametrize('host', ['careers-amd.icims.com', 'careers.amd.com'])
def test_adaptive_entry_uses_only_the_known_host_and_keeps_original_job(store, job, host):
    source = amd(job, host=host)
    store.upsert_job(source)
    store.update_settings({'allowed_application_ats': ['icims']})
    recorded = []
    class NavigationObserved(Exception):
        pass
    class Page:
        def goto(self, url, **kwargs):
            recorded.append(url)
            raise NavigationObserved()
    browser = AdaptiveBrowser(store)
    browser.page = Page()
    with pytest.raises(NavigationObserved):
        browser.apply(source, live=False)
    assert recorded == [HOSTED + '?in_iframe=1']
    assert browser.allowed_origin == 'https://careers-amd.icims.com'
    assert browser.job['url'] == CMS and browser.job['id'] == source['id']
    assert store.db.execute('SELECT url FROM jobs WHERE id=?', (source['id'],)).fetchone()[0] == CMS
    assert not store.db.execute('SELECT 1 FROM applications').fetchone()


@pytest.mark.parametrize('state', ['confirmed', 'unknown', 'awaiting_verification', 'not_submitted', 'submitting'])
def test_bridge_and_hosted_link_cannot_repeat_retained_attempts(store, job, package, state):
    first = amd(job)
    store.upsert_job(first)
    store.update_settings({'allowed_application_ats': ['icims']})
    aid = store.prepare(first, {**package, 'job_id': first['id'], 'url': first['url']})
    store.db.execute("UPDATE applications SET state=?,attempted='2020-01-01T00:00:00+00:00' WHERE id=?", (state, aid))
    original = dict(store.application_record(aid))
    second = amd(job, url=HOSTED, company='Different feed name')
    store.upsert_job(second)
    with pytest.raises(Blocked, match='duplicate_or_uncertain'):
        store._check_budget(second, store.settings())
    assert dict(store.application_record(aid)) == original


@pytest.mark.parametrize('state', ['intent', 'unknown'])
def test_bridge_cannot_abandon_an_unacknowledged_ui_save_under_an_alias(store, job, state):
    first = amd(job)
    second = amd(job, url=HOSTED, company='Different feed name')
    store.upsert_job(first); store.upsert_job(second)
    store.db.execute('INSERT INTO draft_operations VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        ('held-save', first['id'], 'earlier-session', 'ui:next', '{}', 'request', 'sources', state, now(), now(), ''))
    original = dict(store.db.execute('SELECT * FROM draft_operations').fetchone())
    with pytest.raises(Blocked, match='browser_draft_uncertain'):
        guard_pending(store, second)
    assert dict(store.db.execute('SELECT * FROM draft_operations').fetchone()) == original


def test_manual_application_of_careers_link_protects_hosted_alias(store, job):
    first = amd(job); second = amd(job, url=HOSTED, company='Different feed name')
    store.upsert_job(first); store.upsert_job(second)
    store.update_settings({'allowed_application_ats': ['icims']})
    store.db.execute("INSERT INTO job_decisions VALUES(?,'manually_applied','discovered','2020-01-01T00:00:00+00:00')", (first['id'],))
    with pytest.raises(Blocked, match='duplicate_or_uncertain'):
        store._check_budget(second, store.settings())


def test_package_keeps_source_url_but_requires_the_known_execution_origin(store, job):
    source = amd(job)
    package = {'url': CMS, 'answers': [], 'documents': [], 'steps': [], 'browser_agent': {
        'version': 1, 'profile_binding': 'a' * 64, 'last_url': HOSTED, 'complete': False,
        'completed_steps': [], 'uploaded_documents': []}}
    validate_browser_package(store, source, package)
    for target in (CMS, HOSTED.replace('careers-amd.', 'careers-other.')):
        changed = {**package, 'browser_agent': {**package['browser_agent'], 'last_url': target}}
        with pytest.raises(Blocked, match='browser_package_invalid'):
            validate_browser_package(store, source, changed)
    assert package['url'] == CMS
