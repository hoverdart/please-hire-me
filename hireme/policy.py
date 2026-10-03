from __future__ import annotations

import re

from .util import Blocked, company_key

LOCATIONS = {
 "San Francisco Bay Area": r"san francisco|\bsf\b|bay area|palo alto|mountain view|menlo park|redwood city|san mateo|sunnyvale|santa clara|san jose|oakland|berkeley|cupertino|foster city|emeryville|burlingame",
 "United States":r"united states|\busa?\b|,\s*(?:CA|WA|NY|IL|TX|MA|VA|AZ|CO|NC|GA|PA|OR|FL|MI|OH|MD|NJ|CT|UT|MN|WI)\b|san francisco|new york|seattle|boston|chicago|austin|palo alto|mountain view|berkeley|san jose|redwood city|sunnyvale|santa clara|bellevue|redmond|kirkland|menlo park|san mateo|oakland",
 "New York":r"new york|\bnyc\b|brooklyn|manhattan",
 "Seattle":r"seattle|bellevue|redmond|kirkland",
 "Remote (US)":r"remote.{0,25}(?:united states|\bus\b|\busa\b)|(?:united states|\bus\b|\busa\b).{0,25}remote",
}
SENIOR=re.compile(r"\bsenior\b|\bsr\.?\b|\bprincipal\b|\blead\b|\bmanager\b|\bdirector\b|\bhead of\b|\barchitect\b|\bstaff\b",re.I)
YEARS=re.compile(r"(?:at least|minimum(?: of)?|requires?|must have|\b)(\d+)(?:\s*[-–]\s*\d+)?\+?\s+years?(?: of)?\s+(?:professional|relevant|industry|work|software|engineering|experience)",re.I)
GRAD=re.compile(r"(?:graduat\w*.{0,35}(?:between|from)\s+)(\d{4})(?:.{0,20}(?:and|to|[-–])\s*)(\d{4})",re.I)
NO_SPONSOR=re.compile(r"(?:do(?:es)? not|cannot|can.t|will not|unable to|not (?:available|offered)).{0,35}sponsor|sponsorship.{0,30}(?:not (?:available|offered)|unavailable)|without.{0,15}sponsorship",re.I)
CITIZEN=re.compile(r"(?:must be|requires?|only|limited to).{0,25}(?:us citizen|u\.s\. citizen|us person|u\.s\. person)|(?:us citizen|u\.s\. citizen|us person).{0,25}(?:required|only)|\bitar\b|(?:active|must have|requires?).{0,20}security clearance",re.I)


def fit_score(job,facts):
    text=(job.get("title","")+" "+job.get("description","")).lower()
    title=job.get("title","").lower()
    skills=[s.strip().lower() for s in facts.get("skills",{}).get("value","").split(",") if s.strip()]
    matches=[s for s in skills if re.search(r"(?<!\w)"+re.escape(s)+r"(?!\w)",text)]
    score=35+min(40,len(matches)*8)
    if re.search(r"intern|new.?grad|early career|university|graduate",title): score+=15
    return min(100,score),matches


def eligible(job,s,facts):
    title=job.get("title",""); desc=job.get("description",""); location=job.get("location","")
    text=title+"\n"+desc
    clean=re.sub(r"member of technical staff|technical staff", "",title,flags=re.I)
    if SENIOR.search(clean): raise Blocked("seniority_mismatch")
    if not any(re.search(re.escape(r),title,re.I) for r in s["roles"]): raise Blocked("role_mismatch")
    internship=bool(re.search(r"\bintern\b|internship",text,re.I))
    if internship and "internship" not in s["seniority"]: raise Blocked("internship_out_of_scope")
    if not internship and "new-grad" not in s["seniority"]: raise Blocked("fulltime_out_of_scope")
    summer=bool(re.search(r"summer\s*2027",text,re.I))
    seasonal=facts.get("summer_2027_relocate",{}).get("value")=="Yes"
    locations=(s["summer_2027_locations"] if summer else s["school_locations"]) if seasonal else s["locations"]
    patterns=[LOCATIONS.get(l,re.escape(l)) for l in locations]
    # SF is a US location even when a posting uses only the city abbreviation.
    if 'United States' in locations:
        patterns.append(r'\bsf\b')
    if not location or not any(re.search(p,location,re.I) for p in patterns): raise Blocked("location_mismatch")
    required=job.get("min_years")
    matches=[int(m.group(1)) for m in YEARS.finditer(desc)]
    if matches: required=max([required or 0,*matches])
    allowed=min(s["max_years_required"],float(facts.get("professional_years",{}).get("value","0")))
    if required is not None and required>allowed: raise Blocked("experience_mismatch")
    if re.search(r"(?:two|three|four|five|six|seven|eight|nine|ten)\s+(?:or more\s+)?years?",desc,re.I):
        raise Blocked("ambiguous_experience","Written-out experience gate needs verification")
    if NO_SPONSOR.search(desc) and facts.get("needs_sponsorship",{}).get("value")!="No":
        raise Blocked("sponsorship_mismatch")
    if CITIZEN.search(desc):
        if facts.get("us_person",{}).get("value")!="Yes": raise Blocked("citizenship_mismatch")
        if re.search(r"citizen|clearance",desc,re.I): raise Blocked("citizenship_or_clearance_review")
    grad=facts.get("graduation",{}).get("value","")
    windows=list(GRAD.finditer(desc))
    for m in windows:
        if not grad or not int(m.group(1))<=int(grad[:4])<=int(m.group(2)): raise Blocked("graduation_mismatch")
    if re.search(r"(?:must|required|eligible).{0,40}graduat|graduat.{0,30}(?:must|between|before|after)",desc,re.I) and not windows:
        raise Blocked("graduation_window_review","An unparsed graduation requirement needs review")
    start=re.search(r"(winter|spring|summer|fall|autumn)\s*(\d{4})",title,re.I)
    if start:
        span={"winter":(1,3),"spring":(3,5),"summer":(5,8),"fall":(8,11),"autumn":(8,11)}[start.group(1).lower()]
        lo=f"{start[2]}-{span[0]:02d}"; hi=f"{start[2]}-{span[1]:02d}"
        earliest=facts.get("earliest_start",{}).get("value"); latest=facts.get("latest_start",{}).get("value")
        if earliest and earliest>hi or latest and latest<lo: raise Blocked("start_window_mismatch")
    pay=job.get("compensation") or {}
    floor=s["min_hourly_usd"] if internship else s["min_annual_usd"]
    if floor:
        if pay.get("currency")!="USD" or pay.get("period")!=("hour" if internship else "year") or pay.get("min") is None:
            raise Blocked("compensation_unknown")
        if pay["min"]<floor: raise Blocked("compensation_mismatch")
    # Every pipeline uses the same exact/alias company policy.
    aliases={company_key(k):company_key(v) for k,v in s["company_aliases"].items()}
    norm=lambda x:aliases.get(company_key(x),company_key(x))
    if norm(job["company"]) in {norm(x) for x in s["skip_companies"]+s["interview_companies"]}:
        raise Blocked("company_blocked")
    score,evidence=fit_score(job,facts)
    if score<s["min_fit_score"]: raise Blocked("low_fit")
    return score,evidence
