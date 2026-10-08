from __future__ import annotations

import copy
from zoneinfo import ZoneInfo

DEFAULTS = {
    "schema_version": 1,
    "provider": "claude-cli",
    "provider_model": "sonnet",
    "model_effort": "medium",
    "model_escalation": False,
    "remote_drafts": False,
    "deployment": "local",
    "max_attempts_per_cycle": 10,
    "max_model_requests_per_cycle": 40,
    "max_model_requests_per_day": 100,
    "max_output_tokens": 2000,
    "tailored_writing": False,
    "contextual_preferences": False,
    "gmail_reports": False,
    "gmail_verification": False,
    "employer_accounts": False,
    "gmail_code_wait_seconds": 60,
    "cover_letters": False,
    "cover_letter_words": 250,
    "timezone": "America/Los_Angeles",
    "schedule_hours": 6,
    "target_per_cycle": 7,
    "max_per_cycle": 10,
    "target_per_day": 28,
    "max_per_day": 40,
    "max_per_company": 2,
    "company_cooldown_days": 90,
    "live_enabled": False,
    "onboarding_complete": False,
    "browser_channel": "chromium",
    "headless": False,
    "discovery_workers": 6,
    "summer_2027_locations": ["United States", "Remote (US)"],
    "school_locations": ["San Francisco Bay Area", "Remote (US)"],
    "prior_employers": [],
    "locations": ["San Francisco Bay Area", "New York", "Seattle", "Remote (US)"],
    "roles": ["software", "machine learning", "research", "developer", "technical staff"],
    "seniority": ["internship", "new-grad"],
    "min_annual_usd": 0,
    "min_hourly_usd": 0,
    "skip_companies": [],
    "interview_companies": [],
    "company_aliases": {},
    "signed_in_portals": [],
    "min_fit_score": 45,
    "max_years_required": 1,
    "model_timeout_seconds": 90,
    "cycle_timeout_seconds": 10800,
}


def validate_settings(changes: dict, current: dict | None = None) -> dict:
    if not isinstance(changes, dict) or set(changes) - set(DEFAULTS):
        raise ValueError("Unknown settings keys")
    s = copy.deepcopy(DEFAULTS)
    s.update(copy.deepcopy(current or {}))
    s.update(changes)
    if s['model_effort'] not in ('low','medium','high'):raise ValueError('Invalid model effort')
    if s['provider'] not in ('claude-cli','codex-cli','anthropic-api','openai-api'):raise ValueError('Unsupported provider')
    if s['deployment'] not in ('local','pi'):raise ValueError('Choose local or pi')
    import re
    if not isinstance(s['provider_model'],str) or (s['provider_model'] and not re.fullmatch(r'[A-Za-z0-9._:/-]{1,100}',s['provider_model'])):raise ValueError('Invalid model identifier')
    for key,ceiling in [('max_attempts_per_cycle',50),('max_model_requests_per_cycle',200),('max_model_requests_per_day',500),('max_output_tokens',8000)]:
        if type(s[key]) is not int or not 1<=s[key]<=ceiling:raise ValueError('Invalid '+key)
    if s['max_model_requests_per_cycle']>s['max_model_requests_per_day']:raise ValueError('Cycle model limit exceeds daily limit')
    for key in ("schedule_hours", "target_per_cycle", "max_per_cycle", "target_per_day", "max_per_day",
                "max_per_company", "company_cooldown_days", "discovery_workers", "model_timeout_seconds",
                "cycle_timeout_seconds", "cover_letter_words", "gmail_code_wait_seconds"):
        if type(s[key]) is not int or not 1 <= s[key] <= 86400:
            raise ValueError(f"Invalid {key}")
    if s["discovery_workers"] > 16 or s["max_per_cycle"] > 50 or s["max_per_day"] > 100:
        raise ValueError("Worker or submission limit too high")
    if s["target_per_cycle"] > s["max_per_cycle"] or s["target_per_day"] > s["max_per_day"]:
        raise ValueError("Targets exceed ceilings")
    for key in ("live_enabled", "onboarding_complete", "headless", "tailored_writing", "contextual_preferences", "cover_letters", "gmail_reports", "gmail_verification", "employer_accounts", "model_escalation", "remote_drafts"):
        if type(s[key]) is not bool:
            raise ValueError(f"Invalid {key}")
    if s["gmail_code_wait_seconds"]>120:raise ValueError("Gmail verification wait must be at most 120 seconds")
    if not 100<=s["cover_letter_words"]<=350:raise ValueError("Cover letters must use 100–350 words")
    if s["cover_letters"] and not s["tailored_writing"]:raise ValueError("Cover letters require tailored writing")
    for key in ("min_annual_usd", "min_hourly_usd", "max_years_required", "min_fit_score"):
        import math
        if type(s[key]) not in (int, float) or not math.isfinite(s[key]) or s[key] < 0:
            raise ValueError(f"Invalid {key}")
    if s["min_fit_score"] > 100:
        raise ValueError("Fit score must be 0–100")
    for key in ("locations", "summer_2027_locations", "school_locations", "prior_employers", "roles", "seniority", "skip_companies", "interview_companies", "signed_in_portals"):
        if not isinstance(s[key], list) or any(not isinstance(x, str) or not x.strip() or len(x) > 200 for x in s[key]):
            raise ValueError(f"Invalid {key}")
    if not s["locations"] or not s["roles"] or not s["seniority"]:
        raise ValueError("Targets cannot be empty")
    if not isinstance(s["company_aliases"], dict) or any(not isinstance(k, str) or not isinstance(v, str) for k,v in s["company_aliases"].items()):
        raise ValueError("Invalid aliases")
    if s["browser_channel"] not in ("chrome", "chromium", "system-chromium"):
        raise ValueError("Unsupported browser channel")
    ZoneInfo(s["timezone"])
    return s


# No citizenship, authorization, GPA, experience or preference defaults.
FACTS = {
    "full_name": "Full legal name", "first_name": "First name", "last_name": "Last name",
    "preferred_name": "Preferred name", "name_pronunciation": "How to pronounce your name, in your own spelling",
    "email": "Personal email", "phone": "Phone",
    "location": "Current city / state", "street": "Street address", "city": "City",
    "state": "State", "postal_code": "Postal code", "country": "Country of residence",
    "linkedin": "LinkedIn URL", "github": "GitHub URL", "website": "Website",
    "high_school": "High school attended", "school": "University", "degree": "Degree in progress", "major": "Major",
    "graduation": "Expected graduation (YYYY-MM)", "college_start": "College start (YYYY-MM)",
    "highest_completed_degree": "Highest completed degree", "gpa": "GPA",
    "work_authorized_us": "Currently authorized to work in the US (Yes / No)",
    "needs_sponsorship": "Need sponsorship now or in future (Yes / No)",
    "citizenship": "Country of citizenship", "us_person": "US person for export controls (Yes / No)",
    "unrestricted_authorization": "Unrestricted work authorization (Yes / No)",
    "earliest_start": "Earliest start (YYYY-MM)", "latest_start": "Latest start (YYYY-MM)",
    "professional_years": "Years of qualifying professional experience",
    "skills": "Verified skills (comma separated)", "race": "Race / ethnicity answer",
    "gender": "Gender answer", "pronouns": "Pronouns answer", "veteran": "Veteran status answer", "disability": "Disability answer",
    "salary": "Desired compensation answer", "notice_period": "Notice period",
    "relocate": "Willing to relocate (Yes / No)", "onsite": "Willing to work onsite (Yes / No)",
    "recording": "Consent to interview recording (Yes / No)", "background_check": "Consent to background check (Yes / No)",
    "sms": "Consent to SMS (Yes / No)", "native_name": "Legal name in native script",
    "worked_outside_resume": "Worked for employers not on resume (Yes / No)",
    "contacts_outside_resume": "Know people at employers not on resume (Yes / No)",
    "summer_2027_relocate": "Willing to relocate for summer 2027 (Yes / No)",
    "summer_2027_available": "Available for a Summer 2027 internship starting May/June (Yes / No)",
    "outside_business_activity": "Currently provide services to another business or organization (Yes / No)",
    "business_activity_details": "Approved current business/contract activity and potential overlap disclosure",
    "recruitment_data_consent": "Consent to storing/processing data for employment application consideration (Yes / No)",
    "temporary_work_authorization": "Currently hold temporary US work authorization, such as F-1/OPT/CPT/H-1B (Yes / No)",
    "programming_proficiency": "Self-assessed programming proficiency (Beginner / Intermediate / Advanced / Expert)",
    "hispanic_latino": "Hispanic / Latino response (separate from race)",
    "conflict_disclosures": "Have a conflict-of-interest disclosure for employers you apply to: relationships with their staff or vendors, outside business activity, investments over 5% or in their competitors/partners, or retained IP (Yes / No)",
    "government_official": "Within 5 years, a government official or holder of a prominent public function (PEP), referred by one, or closely related to one (Yes / No)",
    "demographic_data_consent": "Consent to employers storing/processing your voluntary demographic survey responses (Yes / No)",
}
REQUIRED = {"full_name", "first_name", "last_name", "email", "phone", "location", "graduation",
            "work_authorized_us", "needs_sponsorship", "us_person", "professional_years", "skills"}
BOOLEANS = {"work_authorized_us", "needs_sponsorship", "us_person", "unrestricted_authorization",
            "relocate", "onsite", "recording", "background_check", "sms", "worked_outside_resume", "contacts_outside_resume", "summer_2027_relocate", "summer_2027_available", "outside_business_activity", "recruitment_data_consent", "temporary_work_authorization", "conflict_disclosures", "government_official", "demographic_data_consent"}


def validate_fact(key: str, value: str) -> str:
    import re
    if key not in FACTS or not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError("A known fact and nonempty value are required")
    value = value.strip()
    if re.search(r"\[(?:your|value|answer)|example\.com|Firstname|Your University|123 Example", value, re.I):
        raise ValueError("Replace placeholder values")
    if key in BOOLEANS and value not in ("Yes", "No"):
        raise ValueError("Answer exactly Yes or No")
    if key in {"graduation", "college_start", "earliest_start", "latest_start"} and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError("Use YYYY-MM")
    if key == "email" and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Invalid email")
    if key == "professional_years" and (not re.fullmatch(r"\d+(?:\.\d+)?", value) or float(value) > 80):
        raise ValueError("Invalid experience")
    if key == "gpa" and not re.fullmatch(r"\d(?:\.\d+)?(?:\s*/\s*\d(?:\.\d+)?)?", value):
        raise ValueError("Use the exact GPA, optionally including its scale")
    return value
