"""Plain-language display metadata. This module never changes worker policy or job state."""
from __future__ import annotations

# Ordinary eligibility decisions stay in the ledger, rather than becoming tasks for the applicant.
NOT_MATCH_REASONS = frozenset({
    'seniority_mismatch', 'role_mismatch', 'internship_out_of_scope', 'fulltime_out_of_scope',
    'location_mismatch', 'experience_mismatch', 'sponsorship_mismatch', 'citizenship_mismatch',
    'graduation_mismatch', 'start_window_mismatch', 'compensation_mismatch', 'company_blocked', 'low_fit',
    'expired_posting',
})
WAIT_REASONS = frozenset({'company_cooldown', 'company_limit', 'company_same_day', 'daily_limit'})
QUIET_REASONS = NOT_MATCH_REASONS | WAIT_REASONS

# (Reason label, next useful step). Unknown reasons keep their exact evidence in the ledger.
REASON_GUIDANCE = {
    'seniority_mismatch': ('Outside your experience level', 'Your desk will keep looking for roles at your chosen level.'),
    'role_mismatch': ('Outside your chosen roles', 'Your desk will keep looking for your role keywords.'),
    'internship_out_of_scope': ('Internships are outside your search', 'Change seniority in Preferences if you want to include internships.'),
    'fulltime_out_of_scope': ('Full-time roles are outside your search', 'Change seniority in Preferences if you want to include new-grad roles.'),
    'location_mismatch': ('Outside your chosen locations', 'Review your location preferences if your search has changed.'),
    'experience_mismatch': ('Requires more experience', 'Your desk will keep looking within your confirmed experience and search limits.'),
    'sponsorship_mismatch': ('Sponsorship requirement does not match', 'This opportunity is held based on the posting and your confirmed authorization facts.'),
    'citizenship_mismatch': ('Citizenship or export-control requirement does not match', 'This opportunity is held based on your confirmed facts.'),
    'graduation_mismatch': ('Graduation dates do not match', 'The posting’s graduation window does not include your confirmed graduation date.'),
    'start_window_mismatch': ('Start dates do not match', 'This posting falls outside your confirmed availability.'),
    'compensation_mismatch': ('Below your compensation minimum', 'Your desk will keep looking within your compensation preferences.'),
    'company_blocked': ('Excluded by your company preferences', 'No action needed. Your desk respects your exclusions and interview list.'),
    'low_fit': ('Below your minimum fit score', 'Your desk will keep looking for closer matches.'),
    'expired_posting': ('Posting is no longer available', 'No action needed. Your desk will keep looking for open roles.'),
    'company_cooldown': ('Company cooldown is active', 'Wait for your configured cooldown to finish. No immediate action is needed.'),
    'company_limit': ('Company application limit reached', 'Your desk respects your lifetime application ceiling for this company.'),
    'company_same_day': ('Already applied to this company today', 'The company’s daily application limit is active.'),
    'daily_limit': ('Daily application limit reached', 'The worker can continue on the next local calendar day.'),
    'captcha_blocked': ('A CAPTCHA needs you', 'Open the official posting and complete the application manually. Your desk will not bypass a CAPTCHA.'),
    'account_blocked': ('Sign-in needs you', 'Sign in through the dedicated employer browser on the worker’s computer.'),
    'account_or_verification_blocked': ('Sign-in or verification needs you', 'Check the employer’s candidate portal and any verification message.'),
    'account_automation_disabled': ('An employer account is required', 'Complete the application manually, or review the optional account setting in Preferences.'),
    'account_result_uncertain': ('An employer account needs verification', 'Check the account, then record your evidence under Employer accounts.'),
    'account_creation_held': ('Account creation is held', 'Verify the account and sign-in manually before recording confirmation.'),
    'account_credentials_unavailable': ('Saved account credentials are unavailable', 'Sign in manually using the dedicated employer browser.'),
    'human_work_sample': ('This answer needs your own work', 'Open the official posting and complete the requested work yourself.'),
    'missing_fact': ('A personal answer is missing', 'Answer the question above or add the confirmed information in Your facts.'),
    'required_answer_missing': ('A required answer is missing', 'Review the unanswered questions or complete this application manually.'),
    'option_mismatch': ('The employer’s choices need review', 'Choose an exact answer in the question queue if one is available.'),
    'ambiguous_experience': ('Experience requirements need review', 'Read the official posting before deciding whether to apply manually.'),
    'citizenship_or_clearance_review': ('Citizenship or clearance needs review', 'Read the official requirement and use your own confirmed status.'),
    'graduation_window_review': ('Graduation requirements need review', 'Check the dates in the official posting before applying manually.'),
    'compensation_unknown': ('Compensation needs review', 'The posted compensation could not be verified against your minimum.'),
    'unsupported_form': ('This application needs manual completion', 'Open the official posting. The worker cannot safely operate this form yet.'),
    'unsupported_widget': ('An application field needs manual completion', 'Open the official posting and complete its unsupported field yourself.'),
    'multi_step_requires_adapter': ('This application flow needs manual completion', 'Open the official posting to complete the remaining steps yourself.'),
    'browser_error': ('The application browser encountered a problem', 'Review the posting and recorded details. Uncertain submissions stay held.'),
    'document_tampered': ('Your stored PDF needs attention', 'Reimport the matching original PDF in Your facts to restore its private copy, then review the application before starting another batch.'),
    'provider_unavailable': ('The model connection needs attention', 'Open Model connection and check installation, login, or the saved API key.'),
    'cover_letter_not_enabled': ('A cover letter is required', 'Add reviewed writing sources and enable tailored writing and cover letters in Preferences, or apply manually.'),
    'company_uncertain': ('An earlier company submission is uncertain', 'Verify the earlier outcome before submitting another application to this company.'),
    'company_verification_pending': ('An earlier company application needs verification', 'Complete the earlier email verification before another application to this company.'),
}
STATUS_LABELS = {'manually_applied': 'Applied manually', 'skipped': 'Don’t apply', 'confirmed': 'Submitted', 'discovered': 'Ready to evaluate', 'blocked': 'Needs action',
                 'unknown': 'Uncertain', 'awaiting_verification': 'Check email', 'prepared': 'Prepared',
                 'submitting': 'Submitting', 'rejected': 'Not a match'}


def reason_code(reason):
    return (reason or '').partition(':')[0].strip()


def job_display(job):
    status = job['status']; code = reason_code(job.get('reason', ''))
    display_status = status
    if status == 'blocked':
        if code in NOT_MATCH_REASONS: display_status = 'not_match'
        elif code in WAIT_REASONS: display_status = 'waiting'
    label = {'not_match': 'Not a match', 'waiting': 'Limit active'}.get(display_status, STATUS_LABELS.get(status, status.replace('_', ' ')))
    reason_label, step = REASON_GUIDANCE.get(code, (code.replace('_', ' ').capitalize(), 'Review the recorded details and the official posting.')) if code else ('', '')
    return {'display_status': display_status, 'status_label': label, 'reason_label': reason_label,
            'next_step': step, 'requires_attention': status in ('unknown', 'awaiting_verification') or status == 'blocked' and code not in QUIET_REASONS}


def attention_sql(alias='j'):
    """The SQL equivalent of requires_attention, with reasons supplied as parameters."""
    code = f"TRIM(SUBSTR({alias}.reason,1,CASE WHEN INSTR({alias}.reason,':')>0 THEN INSTR({alias}.reason,':')-1 ELSE LENGTH({alias}.reason) END))"
    reasons = tuple(sorted(QUIET_REASONS))
    placeholders = ','.join('?' for _ in reasons)
    return (f"({alias}.status IN ('unknown','awaiting_verification') OR ({alias}.status='blocked' AND {code} NOT IN ({placeholders})))", reasons)
