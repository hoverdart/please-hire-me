from __future__ import annotations

import re

from .util import Blocked, company_normalizer

# Bump when eligibility semantics change, independently of form/answer mapping.
ELIGIBILITY_VERSION = 5
CITIZENSHIP_VERSION = 2

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
US_CITIZEN_GATE=re.compile(r"(?:must be|requires?|only|limited to).{0,25}(?:u\.?s\.?|united states) citizen(?:ship|s)?|(?:u\.?s\.?|united states) citizen(?:ship|s)?.{0,25}(?:required|only)",re.I)
DOCTORAL = r'(?:\bph\.?\s*d\b\.?|\bdoctor(?:al|ate)\b(?:\s+degree)?|\bdoctor of philosophy\b)'
LOWER_DEGREE = r"(?:\b(?:bachelor(?:['’]s)?|master(?:['’]s)?|undergraduate|b\.?s|b\.?a|b\.?sc|m\.?s|m\.?sc)\b\.?)"
MASTER_WORD = r"\bmaster(?:['’]s)?\b"
MASTER_ABBREVIATION = r'\bm\.?\s*s(?:c)?\b\.?'
UNDERGRADUATE = r"(?:\b(?:bachelor(?:['’]s)?|undergraduate|associate(?:['’]s)?|b\.?s|b\.?a|b\.?sc)\b\.?)"
DOCTORAL_FACTS = {'phd','doctorate','doctoral','doctoraldegree','doctoratedegree','doctorofphilosophy'}
MASTER_FACTS = {'ms','ma','msc','master','masters','mastersdegree','masterofscience','masterofarts'}
LOWER_DEGREE_FACTS = {'bs','ba','bsc','bachelor','bachelors','bachelorsdegree','bachelorofscience','bachelorofarts',
                      'undergraduate','associate','associates','associatesdegree','highschool','highschooldiploma'}


def _doctoral_requirement(title, description):
    """Recognize an exclusive doctoral level, not preferences or team prose."""
    def exclusive(fragment):
        if (re.search(rf'\b(?:no|without requiring)\s+{DOCTORAL}',fragment,re.I)
                or re.search(rf'{DOCTORAL}\s*(?:degree\s*)?(?:is\s+)?(?:not\s+(?:required|necessary)|preferred|optional|is a plus)',fragment,re.I)):
            return False
        alternatives=rf'(?:{LOWER_DEGREE}.{{0,25}}(?:\bor\b|/).{{0,25}}{DOCTORAL}|{DOCTORAL}.{{0,25}}(?:\bor\b|/).{{0,25}}{LOWER_DEGREE})'
        return not re.search(alternatives,fragment,re.I)
    if re.search(r'\bintern(?:ships?)?\b',title,re.I) and re.search(DOCTORAL,title,re.I) and exclusive(title):
        return True
    study=r'(?:enrolled|pursuing|working\s+(?:toward|towards|on)|studying|hold|have|possess)'
    prerequisite=rf'(?:(?:\bmust\s+|\brequired to\s+)(?:(?:be|currently)\s+){{0,2}}{study}\b|\blimited to\b|\bopen only to\b|\bonly\s+(?:accepting|considering)\b)'
    mandatory=rf'{prerequisite}[^\n;.!?]{{0,100}}{DOCTORAL}|\bonly\s+(?:current\s+)?{DOCTORAL}|{DOCTORAL}\s*(?:degree\s*)?(?:is\s+)?(?:required|mandatory)\b'
    return any(exclusive(description[max(0,match.start()-30):match.end()+100])
               for match in re.finditer(mandatory,description,re.I))


def _check_doctoral_degree(title, description, facts):
    if not _doctoral_requirement(title,description):return
    # A school, graduation date or completed degree does not establish current
    # doctoral study. Only the explicitly confirmed degree in progress can.
    value=re.sub(r'[^a-z]','',facts.get('degree',{}).get('value','').casefold())
    if value in DOCTORAL_FACTS:return
    if value in LOWER_DEGREE_FACTS|MASTER_FACTS:
        raise Blocked('degree_mismatch','This internship requires doctoral study; your confirmed degree in progress does not match')
    raise Blocked('degree_requirement_review','This internship requires doctoral study; confirm your current degree before applying')


def _graduate_requirement(title, description):
    """Recognize required graduate study, including a PhD-or-MS alternative."""
    # MS can also name a software vendor. Require degree wording in prose,
    # and a degree position in titles, rather than interpreting "MS SQL".
    master_context=rf'{MASTER_ABBREVIATION}(?=\s*(?:degree\b|program\b|students?\b|candidates?\b|in\b|or\b|is\b|required\b|mandatory\b|[/,.!?]|$))'
    graduate=rf'(?:{DOCTORAL}|{MASTER_WORD}|{master_context})'
    def exclusive(fragment):
        if (re.search(rf'\b(?:no|without requiring)\s+{graduate}',fragment,re.I)
                or re.search(rf'{graduate}\s*(?:(?:degree|program)\s*)?(?:in\s+[^\n;.!?]{{0,80}}?)?(?:is\s+)?(?:not\s+(?:required|necessary)|preferred|optional|(?:is\s+)?a plus)',fragment,re.I)):
            return False
        alternatives=rf'(?:{UNDERGRADUATE}[^\n;!?]{{0,80}}(?:\bor\b|/)[^\n;!?]{{0,40}}{graduate}|{graduate}[^\n;!?]{{0,80}}(?:\bor\b|/)[^\n;!?]{{0,40}}{UNDERGRADUATE})'
        return not re.search(alternatives,fragment,re.I)
    title_degree=(re.search(rf'{DOCTORAL}|{MASTER_WORD}',title,re.I)
        or re.search(rf'{MASTER_ABBREVIATION}\s+(?:(?:research|software|engineering|engineer|data|science|hardware|systems|design)\s+){{0,4}}intern(?:ships?)?\b',title,re.I)
        or re.search(rf'\bintern(?:ships?)?\b.{{0,60}}[-–(,]\s*{MASTER_ABBREVIATION}\s*[) ]*$',title,re.I))
    if re.search(r'\bintern(?:ships?)?\b',title,re.I) and title_degree and exclusive(title):
        return True
    # A direct requirement bullet may omit "must" (NVIDIA: "Pursuing PhD
    # or MS ..."). Only a clause that addresses the applicant gets that rule;
    # collaborating with, or being mentored by, graduate researchers does not.
    description=re.sub(r'<[^>]+>','\n',description)
    study=r'(?:enrolled(?:\s+in)?|pursuing|working\s+(?:toward|towards|on)|studying|hold|have|possess)'
    prerequisite=rf'(?:(?:\bmust\s+|\brequired to\s+)(?:(?:be|currently)\s+){{0,2}}{study}\b|\blimited to\b|\bopen only to\b|\bonly\s+(?:accepting|considering)\b)'
    direct=rf'(?:^|[\n;:])\s*(?:[-*•]\s*)?(?:currently\s+)?(?:pursuing|enrolled\s+in|working\s+(?:toward|towards|on)|studying)\b'
    mandatory=rf'(?:{prerequisite}|{direct})[^\n;.!?]{{0,100}}?{graduate}|\bonly\s+(?:current\s+)?{graduate}|{graduate}\s*(?:(?:degree|program)\s*)?(?:is\s+)?(?:required|mandatory)\b'
    return any(exclusive(description[max(0,match.start()-30):match.end()+120])
               for match in re.finditer(mandatory,description,re.I))


def _check_graduate_degree(title, description, facts):
    if not _graduate_requirement(title,description):return
    value=re.sub(r'[^a-z]','',facts.get('degree',{}).get('value','').casefold())
    if value in DOCTORAL_FACTS|MASTER_FACTS:return
    if value in LOWER_DEGREE_FACTS:
        raise Blocked('degree_mismatch','This internship requires master’s or doctoral study; your confirmed degree in progress does not match')
    raise Blocked('degree_requirement_review','This internship requires master’s or doctoral study; confirm your current degree before applying')



def fit_score(job,facts):
    text=(job.get("title","")+" "+job.get("description","")).lower()
    title=job.get("title","").lower()
    skills=[s.strip().lower() for s in facts.get("skills",{}).get("value","").split(",") if s.strip()]
    matches=[s for s in skills if re.search(r"(?<!\w)"+re.escape(s)+r"(?!\w)",text)]
    score=35+min(40,len(matches)*8)
    if re.search(r"intern|new.?grad|early career|university|graduate",title): score+=15
    return min(100,score),matches


def employment_kind(job):
    """Classify the advertised job, never the applicant's past experience.

    Explicit title signals take precedence over board metadata and narrowly
    phrased descriptions. An incidental mention of interns is not a role type.
    """
    title=job.get('title','')
    intern=r'\bintern(?:ships?)?\b|\bco[- ]?op\b'
    part=r'\bpart[- ]?time\b'
    graduate=r'\bnew[- ]?grad(?:uate)?\b|\bearly[- ]career\b'
    if re.search(graduate,title,re.I):return 'new-grad'
    if re.search(intern,title,re.I):return 'internship'
    if re.search(part,title,re.I):return 'part-time'
    metadata=str(job.get('employment_type') or '')
    if re.fullmatch(r'intern(?:ships?)?|co[- ]?op',metadata,re.I):return 'internship'
    if re.fullmatch(r'part[- ]?time',metadata,re.I):return 'part-time'
    if re.fullmatch(r'full[- ]?time',metadata,re.I):return 'new-grad'
    if job.get('source')=='simplify:internships':return 'internship'
    desc=job.get('description','')
    if re.search(r'\b(?:this|the) (?:role|position|opportunity) (?:is|will be) (?:an? )?(?:'+intern+r')',desc,re.I):return 'internship'
    if re.search(r'\b(?:this|the) (?:role|position|opportunity) (?:is|will be) (?:a )?(?:'+part+r')',desc,re.I):return 'part-time'
    return 'new-grad'


def listing_scope(job,s):
    title=job.get("title",""); desc=job.get("description","")
    text=title+"\n"+desc
    clean=re.sub(r"member of technical staff|technical staff", "",title,flags=re.I)
    if SENIOR.search(clean): raise Blocked("seniority_mismatch")
    role_patterns={'software':r'software|\bswe\b|\b(?:front[- ]?end|back[- ]?end|full[- ]?stack|platform) (?:software )?(?:engineer|developer)',
                   'machine learning':r'machine learning|\bml (?:engineer|research|intern)\b'}
    if not any(re.search(role_patterns.get(r.casefold(),re.escape(r)),title,re.I) for r in s["roles"]): raise Blocked("role_mismatch")
    kind=employment_kind(job);internship=kind=='internship'
    summer=bool(re.search(r"\bsummer\s*2027\b|\b2027\s*summer\b",text,re.I))
    accepted=kind in s['seniority'] or internship and summer and 'summer-internship' in s['seniority']
    if not accepted:
        raise Blocked({'internship':'internship_out_of_scope','part-time':'parttime_out_of_scope','new-grad':'fulltime_out_of_scope'}[kind])
    return kind,summer


def eligible(job,s,facts):
    kind,summer=listing_scope(job,s);internship=kind=='internship'
    title=job.get("title",""); desc=job.get("description",""); location=job.get("location","")
    text=title+"\n"+desc
    _check_doctoral_degree(title,desc,facts)
    _check_graduate_degree(title,desc,facts)
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
    if CITIZEN.search(desc) or US_CITIZEN_GATE.search(desc):
        if facts.get("us_person",{}).get("value")!="Yes": raise Blocked("citizenship_mismatch")
        citizenship=re.sub(r'[^a-z]','',facts.get('citizenship',{}).get('value','').casefold())
        confirmed_us=citizenship in {'us','usa','unitedstates','unitedstatesofamerica','uscitizen','unitedstatescitizen','citizenoftheunitedstates','americancitizen'}
        if re.search(r'\bclearance\b',desc,re.I) or US_CITIZEN_GATE.search(desc) and not confirmed_us:
            raise Blocked("citizenship_or_clearance_review")
    grad=facts.get("graduation",{}).get("value","")
    from .graduation import required
    required(desc,grad)
    start=re.search(r"(winter|spring|summer|fall|autumn)\s*(\d{4})",title,re.I)
    reverse_start=re.search(r"(\d{4})\s*(winter|spring|summer|fall|autumn)",title,re.I) if not start else None
    if reverse_start:
        start=re.search(r"(winter|spring|summer|fall|autumn)\s*(\d{4})",reverse_start[2]+' '+reverse_start[1],re.I)
    if start:
        span={"winter":(1,3),"spring":(3,5),"summer":(5,8),"fall":(8,11),"autumn":(8,11)}[start.group(1).lower()]
        lo=f"{start[2]}-{span[0]:02d}"; hi=f"{start[2]}-{span[1]:02d}"
        earliest=facts.get("earliest_start",{}).get("value"); latest=facts.get("latest_start",{}).get("value")
        if kind=='new-grad' and facts.get('fulltime_start'):
            earliest=facts['fulltime_start']['value'][:7];latest=None
        if earliest and earliest>hi or latest and latest<lo: raise Blocked("start_window_mismatch")
    pay=job.get("compensation") or {}
    hourly=kind in {'internship','part-time'}
    floor=s["min_hourly_usd"] if hourly else s["min_annual_usd"]
    if floor:
        if pay.get("currency")!="USD" or pay.get("period")!=("hour" if hourly else "year") or pay.get("min") is None:
            raise Blocked("compensation_unknown")
        if pay["min"]<floor: raise Blocked("compensation_mismatch")
    # Every pipeline uses the same exact/alias company policy.
    norm=company_normalizer(s['company_aliases'])
    if norm(job["company"]) in {norm(x) for x in s["skip_companies"]+s["interview_companies"]}:
        raise Blocked("company_blocked")
    score,evidence=fit_score(job,facts)
    if score<s["min_fit_score"]: raise Blocked("low_fit")
    return score,evidence
