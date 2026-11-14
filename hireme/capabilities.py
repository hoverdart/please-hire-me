"""Evidence-backed capability matrix. Describes support; grants no execution authority.

Each cell names what is implemented and what evidence exists. "Live" records
employer-side evidence observed for specific postings; it never generalizes
to a whole platform. A recognized host or a passing fixture is not acceptance.
"""
from __future__ import annotations

from pathlib import Path

COLUMNS = ('discovery', 'preparation', 'draft_saves', 'submission', 'authentication', 'challenge_handoff',
           'receipt_verification', 'fixture_coverage', 'live_validation')
COLUMN_TITLES = {'discovery': 'Discovery', 'preparation': 'Preparation', 'draft_saves': 'Draft saves', 'submission': 'Submission',
                 'authentication': 'Authentication', 'challenge_handoff': 'Challenge handoff',
                 'receipt_verification': 'Receipt verification', 'fixture_coverage': 'Local fixture coverage',
                 'live_validation': 'Live validation'}
# implemented: built and exercised by fixtures. partial: built with stated gaps. gated: present but off until commissioned.
# manual: handed to the applicant. none: not built / no evidence.
LEVELS = ('implemented', 'partial', 'gated', 'manual', 'none')

MATRIX = [
    {'platform': 'Ashby', 'transport': 'Native form transport; adaptive UI only before any write', 'cells': {
        'discovery': ('implemented', 'Public board API for configured boards; tenant renames followed only for the same posting UUID.'),
        'preparation': ('implemented', 'Local fill with autosaves suppressed; school and location lookups are read-only.'),
        'draft_saves': ('partial', 'Source-bound field, education, SMS-preference and attachment saves with matching acknowledgements. An interrupted guest draft cannot be read back or resumed; the requisition stays held.'),
        'submission': ('implemented', 'One final request bound to the posting, form, render and acknowledged answers.'),
        'authentication': ('none', 'Guest applications; no employer sign-in.'),
        'challenge_handoff': ('partial', 'Optional visible wait of up to 600 seconds inside the same attempt. Off by default; needs a desktop session.'),
        'receipt_verification': ('implemented', 'Correlated native acknowledgement plus a fresh completed-application receipt.'),
        'fixture_coverage': ('implemented', ['tests/test_ashby_drafts.py', 'tests/test_ashby_attachments.py', 'tests/test_ashby_education.py',
                                             'tests/test_ashby_preflight.py', 'tests/test_ashby_alias_guard.py', 'tests/test_ashby_crash_guard.py']),
        'live_validation': ('partial', 'Two applications returned native acknowledgements and fresh receipts (Oct 31–Nov 1, 2026). One attempt reached a CAPTCHA and remains uncertain. Not every employer form or conditional flow.')}},
    {'platform': 'Lever', 'transport': 'Native form transport; adaptive UI only before any write', 'cells': {
        'discovery': ('implemented', 'Public postings API with hosted HTML description fields.'),
        'preparation': ('implemented', 'Local fill; remote parsing, uploads and submission suppressed.'),
        'draft_saves': ('partial', 'Resume parser upload for the exact approved PDF. Parser output never becomes a fact; parser-filled optional fields are cleared only when they match the acknowledged parser reply.'),
        'submission': ('implemented', 'One final request after the storage acknowledgement matches the form.'),
        'authentication': ('none', 'Guest applications; no employer sign-in.'),
        'challenge_handoff': ('manual', 'The official verification client runs; a visible CAPTCHA stops the worker and goes to the dashboard.'),
        'receipt_verification': ('implemented', 'Transport success, verification challenge and accepted application are separate outcomes.'),
        'fixture_coverage': ('implemented', ['tests/test_lever_submission.py', 'tests/test_lever_resume.py', 'tests/test_lever_postings.py']),
        'live_validation': ('none', 'No accepted live application. One attempt passed fill and preflight then reached a CAPTCHA; another returned an employer verification error. Both remain uncertain.')}},
    {'platform': 'Greenhouse', 'transport': 'Native form transport; adaptive UI only before any write', 'cells': {
        'discovery': ('implemented', 'Public board API for configured boards.'),
        'preparation': ('implemented', 'Standard and embedded forms; custom public careers links never gain write access.'),
        'draft_saves': ('implemented', 'Approved PDF upload with a matching acknowledgement.'),
        'submission': ('implemented', 'Final endpoint and confirmation route bound to the board and job.'),
        'authentication': ('partial', 'Email security codes read from the owner-bound Gmail connection, bound to the application challenge. Other continuations are held.'),
        'challenge_handoff': ('manual', 'Visible challenges stop the worker and go to the dashboard.'),
        'receipt_verification': ('implemented', 'Correlated native acknowledgement plus a fresh receipt; email verification is a separate pending state.'),
        'fixture_coverage': ('implemented', ['tests/test_greenhouse_submission.py', 'tests/test_gmail.py', 'tests/test_native_submission_observation.py']),
        'live_validation': ('partial', 'Two applications returned native acknowledgements and fresh receipts after the localized-prompt and checkbox repairs. Not every board or question shape.')}},
    {'platform': 'Workday (NVIDIA)', 'transport': 'Adaptive UI executor; a separate native controller stays gated', 'cells': {
        'discovery': ('implemented', 'Configured tenant with exact requisition matching.'),
        'preparation': ('partial', 'Rendered posting verified in the browser. Multi-step drafts cannot advance without employer saves.'),
        'draft_saves': ('implemented', 'Journaled UI saves with captured replies; needs the remote-draft permission and a verified owner session.'),
        'submission': ('implemented', 'Final action after current-value verification and a durable intent.'),
        'authentication': ('manual', 'Applicant signs in through the dedicated employer browser; session expiry requests sign-in again.'),
        'challenge_handoff': ('manual', 'CAPTCHA and MFA stop the worker and go to the dashboard.'),
        'receipt_verification': ('implemented', 'New visible receipt plus an owner-authenticated readback of the same application ID and requisition.'),
        'fixture_coverage': ('implemented', ['tests/test_workday_browser.py', 'tests/test_workday_browser_receipt.py', 'tests/test_workday_sessions.py',
                                             'tests/test_workday_ui_resume.py', 'tests/test_workday_nvidia.py']),
        'live_validation': ('partial', 'One authenticated application reached Application Submitted with a fresh Application Received readback (Nov 3–4, 2026). The native controller is not commissioned.')}},
    {'platform': 'Workday (other tenants)', 'transport': 'Adaptive UI executor for exact posting URLs', 'cells': {
        'discovery': ('partial', 'Five configured tenants; other tenants enter through an exact posting URL.'),
        'preparation': ('partial', 'Rendered posting verified in the browser; shard and site derived from the posting URL.'),
        'draft_saves': ('partial', 'Same journaled UI saves as NVIDIA. Unrecognized definitions, attestations and widgets are held.'),
        'submission': ('partial', 'Same final-action contract. Receipt readback wording is validated only against the NVIDIA record.'),
        'authentication': ('manual', 'A separate applicant account and sign-in per tenant.'),
        'challenge_handoff': ('manual', 'CAPTCHA and MFA stop the worker and go to the dashboard.'),
        'receipt_verification': ('partial', 'Other status wording stays conservative and can end uncertain.'),
        'fixture_coverage': ('implemented', ['tests/test_workday_browser_posting.py', 'tests/test_workday_postings.py', 'tests/test_workday_holds.py']),
        'live_validation': ('none', 'No application to another tenant has been observed. NVIDIA evidence does not transfer.')}},
    {'platform': 'iCIMS', 'transport': 'Adaptive UI executor through the posting\'s own content frame', 'cells': {
        'discovery': ('partial', 'AMD careers links resolve to the same numeric requisition on its fixed iCIMS host. No general iCIMS search.'),
        'preparation': ('partial', 'The executor uses the approved same-origin, same-requisition content frame while retaining its wrapper. Multi-step drafts cannot advance locally.'),
        'draft_saves': ('partial', 'Journaled UI saves for application steps. A step that saves through an account-profile endpoint is refused.'),
        'submission': ('partial', 'Same final-action contract as other adaptive flows.'),
        'authentication': ('manual', 'Account sign-in and creation are handed to the applicant in the dedicated employer browser.'),
        'challenge_handoff': ('manual', 'Visible challenges stop the worker and go to the dashboard.'),
        'receipt_verification': ('partial', 'Fresh receipt text read from the approved frame plus the captured final reply. No employer-side readback.'),
        'fixture_coverage': ('partial', ['tests/test_icims_flow.py', 'tests/test_icims_amd_entry.py']),
        'live_validation': ('none', 'Public AMD careers and iCIMS entry pages observed read-only on Nov 14, 2026. No real application submitted or accepted; authenticated tenant flows remain unverified.')}},
    {'platform': 'Custom employer forms', 'transport': 'Static single-page adapter, then the adaptive UI executor', 'cells': {
        'discovery': ('partial', 'Recognized portals and imported posting links only.'),
        'preparation': ('implemented', 'Native controls and inspectable custom comboboxes on single-page forms.'),
        'draft_saves': ('partial', 'Observed multi-step saves on the posting\'s own origin. Cross-origin frames, hidden or unknown controls and unapproved consent are held.'),
        'submission': ('partial', 'Complete validated package, durable intent, correlated reply and a fresh receipt are all required.'),
        'authentication': ('manual', 'Optional native signup or sign-in for simple same-origin forms; JavaScript, SSO and verification stay manual.'),
        'challenge_handoff': ('manual', 'Visible challenges stop the worker and go to the dashboard.'),
        'receipt_verification': ('partial', 'Generic receipt wording only; anything ambiguous ends uncertain.'),
        'fixture_coverage': ('implemented', ['tests/test_browser_agent.py', 'tests/test_browser.py', 'tests/test_repeated_form_groups.py']),
        'live_validation': ('none', 'No custom employer form has recorded live acceptance evidence.')}},
    {'platform': 'Handshake / Work at a Startup', 'transport': 'Routing to the employer ATS; native platform flows gated', 'cells': {
        'discovery': ('gated', 'Saved listings only. Bulk search is off and separately gated.'),
        'preparation': ('partial', 'A saved listing is routed to its employer requisition when it links to one.'),
        'draft_saves': ('gated', 'Native platform writes need reviewed, machine-bound flow evidence that is not distributed.'),
        'submission': ('gated', 'No native platform submission without that evidence; routed jobs use the employer adapter.'),
        'authentication': ('manual', 'Separate dedicated profiles; the applicant signs in.'),
        'challenge_handoff': ('manual', 'Handed to the applicant.'),
        'receipt_verification': ('gated', 'Platform receipts are recognized only inside a commissioned flow.'),
        'fixture_coverage': ('partial', ['tests/test_platform_connections.py', 'tests/test_platform_browser.py', 'tests/test_connected_workspace_ui.py']),
        'live_validation': ('none', 'No native platform application has been observed.')}},
]


def matrix(store=None):
    """The matrix, with this installation's own recorded outcomes when a ledger is supplied."""
    rows = []
    recorded = {}
    if store is not None:
        from .field_context import ats
        labels = {'Ashby': 'Ashby', 'Lever': 'Lever', 'Greenhouse': 'Greenhouse', 'iCIMS': 'iCIMS', 'Other': 'Custom employer forms'}
        for row in store.db.execute('''SELECT j.host,a.state,COUNT(*) AS total FROM application_records a JOIN jobs j ON j.id=a.job_id
                WHERE a.attempted IS NOT NULL GROUP BY j.host,a.state'''):
            kind = ats(row['host'])
            name = ('Workday (NVIDIA)' if row['host'].startswith('nvidia.') else 'Workday (other tenants)') if kind == 'Workday' else labels.get(kind)
            if not name:
                continue
            bucket = recorded.setdefault(name, {'confirmed': 0, 'uncertain': 0, 'not_submitted': 0})
            if row['state'] == 'confirmed':
                bucket['confirmed'] += row['total']
            elif row['state'] in ('unknown', 'submitting', 'awaiting_verification'):
                bucket['uncertain'] += row['total']
            elif row['state'] == 'not_submitted':
                bucket['not_submitted'] += row['total']
    for entry in MATRIX:
        cells = {}
        for column in COLUMNS:
            level, detail = entry['cells'][column]
            cells[column] = {'level': level, 'detail': detail if isinstance(detail, str) else 'Synthetic local fixtures only.',
                             **({'fixtures': detail} if isinstance(detail, list) else {})}
        rows.append({'platform': entry['platform'], 'transport': entry['transport'], 'cells': cells,
                     'this_installation': recorded.get(entry['platform'], {'confirmed': 0, 'uncertain': 0, 'not_submitted': 0}) if store is not None else None})
    return {'columns': [{'key': key, 'title': COLUMN_TITLES[key]} for key in COLUMNS], 'platforms': rows,
            'note': 'Fixture coverage is local synthetic evidence. Live validation lists observed employer evidence for specific postings and does not certify a platform.'}


def markdown():
    """The documentation table, generated from the same data the dashboard serves."""
    lines = ['| Platform | ' + ' | '.join(COLUMN_TITLES[key] for key in COLUMNS) + ' |', '| --- |' + ' --- |' * len(COLUMNS)]
    for entry in MATRIX:
        cells = []
        for column in COLUMNS:
            level, detail = entry['cells'][column]
            text = ', '.join(f'`{Path(path).name}`' for path in detail) if isinstance(detail, list) else detail
            cells.append(f'**{level.capitalize()}.** {text}'.replace('|', '\\|'))
        lines.append(f'| **{entry["platform"]}**<br>{entry["transport"]} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)
