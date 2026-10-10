from __future__ import annotations

import json
import re

from .util import Blocked, digest
from .field_context import binding as contextual_binding, save_binding, present, field_context,validate_numeric
from .answer_context import GENERIC_WORK_COUNTRY, employment_country_question, work_country_location, us_city_location

# These mappings authorize exact values only. Unknown wording is queued, never guessed.
RULES = [
    (r"^(full |legal |full legal )?name\*?$", "full_name"),
    (r"^(legal )?first name\*?$", "first_name"), (r"^(legal )?last name\*?$", "last_name"),
    (r"^preferred (first )?name\*?$", "preferred_name"),
    (r"^e-?mail( address)?\*?$", "email"), (r"^(phone|phone number|mobile|mobile phone)\*?$", "phone"),
    (r"^(current )?location( \(city\))?\*?$", "location"), (r"^city\*?$", "city"),
    (r"^state\*?$", "state"), (r"^(zip|zip code|postal code)\*?$", "postal_code"),
    (r"^(street address|address line 1)\*?$", "street"), (r"^country( of residence)?\*?$", "country"),
    (r"^linkedin( profile)?( url| link)?\*?$", "linkedin"), (r"^github( profile)?( url| link)?\*?$", "github"),
    (r"^(website|personal website|portfolio)( url| link)?\*?$", "website"),
    (r"^(school|university|college|school name|university name|college\s*/\s*university|university\s*/\s*college)\*?$", "school"),
    (r"^(major|field of study|discipline|discipline\s*/\s*field of study)\*?$", "major"), (r"^(cumulative )?gpa\*?$", "gpa"),
    (r"^(expected |anticipated )?graduation( date| semester| term| season| month| year| month\s*/\s*year| month and year)?\*?$", "graduation"),
    (r"^are you (legally )?authorized to work in (the )?(united states|us|u\.s\.)\??\*?$", "work_authorized_us"),
    (r"^will you( now or in the future)? require( visa)? sponsorship( now or in the future)?\??\*?$", "needs_sponsorship"),
    (r"^do you( now or in the future)? require( visa)? sponsorship\??\*?$", "needs_sponsorship"),
    (r"^do you have unrestricted work authorization\??\*?$", "unrestricted_authorization"),
    (r"^country of citizenship\*?$", "citizenship"),
    (r"^are you a us person\??\*?$", "us_person"),
    (r"^(gender|gender identity)\*?$", "gender"), (r"^(race|race / ethnicity|race/ethnicity)\*?$", "race"),
    (r"^veteran status\*?$", "veteran"), (r"^disability status\*?$", "disability"),
    (r"^(desired salary|salary expectations|desired compensation)\*?$", "salary"),
    (r"^notice period\*?$", "notice_period"),
]
REFUSE = re.compile(r"do not use (?:ai|artificial intelligence)|don.t use ai|without (?:ai|artificial intelligence)|graded (?:test|work|assessment)|solve this|prove that|coding challenge",re.I)


def field_key(label):
    if re.search(r'\b(?:hispanic|latino)\b',label,re.I):return 'hispanic_latino'
    if re.search(r'currently.*(?:hold|have).*temporary.*(?:work|employment).*authoriz',label,re.I):return 'temporary_work_authorization'
    if re.search(r'(?:programming|program analysis).*proficien|proficien.*(?:programming|program analysis)',label,re.I):return 'programming_proficiency'
    if re.search(r'currently own,? operate,? or provide services.*business or organization',label,re.I):return 'outside_business_activity'
    if re.search(r'describe the nature of the activity.*your role.*overlap',label,re.I):return 'business_activity_details'
    if (re.search(r'(?:store|process).*data.*(?:considering|consideration|eligibility).*application.*employment',label,re.I)
            and not re.search(r'marketing|advertis|sell|sale|third.part',label,re.I)):return 'recruitment_data_consent'
    if (re.search(r'consent.{0,80}(?:collect|stor|process).{0,120}(?:demographic|self.identif|voluntary.{0,20}survey)',label,re.I)
            and not re.search(r'marketing|advertis|sell|sale|third.part',label,re.I)):return 'demographic_data_consent'
    if re.search(r'(?:personal|familial) relationships?.{0,300}outside business activit',label,re.I|re.S):return 'conflict_disclosures'
    if re.search(r'government official.{0,400}(?:hold|held|referred|recommended|related)',label,re.I|re.S):return 'government_official'
    # Politically exposed person (PEP) declarations ask the same thing.
    if re.search(r'entrusted with (?:a )?(?:prominent )?(?:public )?(?:position|function)|politically exposed|family member of (?:someone|a person) holding such a position',label,re.I):return 'government_official'
    label=" ".join(label.strip().casefold().split()).rstrip(" *?:")
    if re.fullmatch(r'are you (?:currently )?registered with finra',label):return 'finra_registered'
    if re.fullmatch(r'(?:are you actively maintaining|do you (?:actively )?(?:hold|maintain)) (?:any )?securities licenses',label):return 'securities_licenses'
    if re.fullmatch(r'(?:alternate|alternative|secondary|additional|other|backup) e-?mail(?: address)?',label):return 'alternate_email'
    if re.fullmatch(r'(?:at the time of application,? )?are you (?:18\+ years of age|18 or older|at least 18 years old)',label):return 'over_18'
    if re.fullmatch(r'are you willing and able to work nights and weekends',label):return 'nights_weekends'
    if re.fullmatch(r'do you have experience working with robots',label):return 'robots_experience'
    if re.fullmatch(r'have you worked with humanoids',label):return 'humanoids_experience'
    if re.match(r'when will you be available to work as a full.time,? permanent employee\?',label):return 'fulltime_start'
    if re.fullmatch(r'(?:earliest (?:available )?start(?: date)?|available start date|start date availability)',label):return 'earliest_start'
    if re.fullmatch(r'what is the earliest date you are available to start (?:this|the) (?:position|role|job)',label):return 'earliest_start'
    if re.search(r'\bhigh school\b',label):
        return 'high_school' if not re.search(r'gpa|grade|year|date|graduat|degree|diploma',label) else None
    for pattern,key in RULES:
        if re.fullmatch(pattern,label): return key
    if re.fullmatch(r'(?:phone )?(?:country|dial|calling) code',label):return 'phone'
    if label=='consent to receiving text messages':return 'sms'
    if re.fullmatch(r'where are you (?:currently )?(?:located|based|living)',label):return 'location'
    if re.fullmatch(r'(?:please )?(?:select|indicate|enter) the country where you (?:currently )?(?:reside|live)\.?',label):return 'country'
    if re.fullmatch(r'(?:what are your )?pronouns',label):return 'pronouns'
    # Applicants often misspell it "pronounciation"; it is never the pronouns question.
    if re.fullmatch(r'(?:(?:your |legal |preferred )?name )?prono?unciation(?: of your name)?|(?:how (?:do|should) (?:we|you|i) (?:pronounce|say) your name)',label):return 'name_pronunciation'
    # Only the generic question; a named destination stays a separate decision.
    if re.fullmatch(r'(?:are you )?(?:willing|open)(?: and able)? to relocat(?:e|ion)(?: for (?:this|the) (?:role|position|job|internship))?',label):return 'relocate'
    if re.fullmatch(r'what is your gender(?: identity)?',label):return 'gender'
    if re.fullmatch(r'how would you describe your gender identity',label):return 'gender'
    if re.fullmatch(r'what is your (?:cumulative )?gpa',label):return 'gpa'
    if re.fullmatch(r'what is your (?:race or ethnicity|race|ethnicity)',label):return 'race'
    if re.fullmatch(r'what is your disability status',label):return 'disability'
    if re.fullmatch(r'what is your (?:military|veteran|protected veteran) status',label):return 'veteran'
    if re.fullmatch(r'(?:please indicate |what is )?your (?:desired )?hourly (?:rate|pay)(?: requirement| expectation)?',label):return 'salary'
    if re.search(r'(?:which|what|indicate|select|enter).*(?:\bstate\b|province).*(?:resid|live)',label):return 'state'
    if re.search(r'(?:zip|postal) code.*(?:primary residence|home|address)',label):return 'postal_code'
    if re.search(r'confirm.*availability.*summer\s*2027',label):return 'summer_2027_available'
    if re.search(r"(?:which|what).*(?:college|university|school).*(?:attend|enroll)|name of (?:your |the )?(?:college|university|school)",label):return 'school'
    if re.fullmatch(r"(?:current |pursuing |academic )?degree(?: type)?",label):return 'degree'
    if re.search(r'\bdegree\b.*\b(?:currently pursuing|pursuing|working (?:toward|towards|on))\b',label) and not re.search(r'completed|earned|obtained',label):return 'degree'
    if re.search(r'when.*(?:expect|plan).*graduat|(?:expected|anticipated).*graduation|what year.*graduat|^when (?:do|will) you graduate$',label):return 'graduation'
    if 'highest' in label and re.search(r'education|degree',label):return 'highest_completed_degree'
    if re.search(r'(?:will|do).*(?:require|need).*sponsor|(?:require|need).*employment visa',label):return 'needs_sponsorship'
    if re.search(r'(?:authorized|eligible|authorization).*(?:work|employment).*(?:united states|u\.s\.|\bus\b)',label):return 'work_authorized_us'
    if re.search(r'currently located.*(?:office|on.site|in.person)',label):return None
    if re.search(r'(?:open|willing|comfortable).*(?:in.person|on.site|in office)|^i understand that this position requires me to work on.site|(?:can|able to).*work (?:from|at|in).*(?:office|headquarters|\bhq\b)',label):return 'onsite'
    if 'office' in label and re.search(r'willing and able to accommodate this work environment',label):return 'onsite'
    return None


def category(label):
    if re.search(r'what qualities.{0,150}(?:great|successful).{0,180}(?:how|skills|experiences)|(?:how|why).{0,100}(?:skills|experiences|background).{0,100}(?:best|good|strong|ideal) candidate',label,re.I):return 'experience'
    if re.search(r'what (?:are you )?(?:most )?excited (?:to|about)',label,re.I):return 'motivation'
    if re.search(r"why (?:do you want|are you interested|this (?:role|company))|what interests you|what (?:excites|motivates) you|what excites you|why are you excited|what makes you (?:excited|interested)|why.{0,50}(?:work|join)|why.{0,30}(?:choose|chose)|(?:professional|career|short.term) (?:goals|plans|aspirations)",label,re.I):return "motivation"
    if re.search(r"(?:tell|describe|share).{0,25}(?:project|something you (?:built|created))",label,re.I):return "project"
    if re.search(r"(?:tell us about yourself|summarize your (?:background|experience)|describe your (?:background|experience)|describe your prior experience)",label,re.I):return "experience"
    return None


def _option_value(key, value, options, context=None):
    if key=='fulltime_start' and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        return _option_value('graduation',value[:7],options,context)
    if key is None and value in options:
        if options.count(value)!=1:raise Blocked('option_mismatch')
        return value
    normalize=lambda v:re.sub(r"[^a-z0-9]", "", re.sub(r"\bdon['’]t\b",'do not',v.casefold()))
    if key=='programming_proficiency' and value in {'Beginner','Intermediate','Advanced','Expert'}:
        matches=[x for x in options if re.match(re.escape(value)+r'(?:\b|/)',x,re.I)]
        if len(matches)!=1:raise Blocked('option_mismatch')
        return matches[0]
    if key=='gpa' and re.fullmatch(r'\d+(?:\.\d+)?(?:\s*/\s*4(?:\.0+)?)?',value):
        from decimal import Decimal
        score=Decimal(value.split('/')[0].strip());matches=[]
        if not 0<=score<=4:raise Blocked('option_mismatch')
        for option in options:
            text=option.strip().casefold()
            if re.fullmatch(r'\d+(?:\.\d+)?',text) and Decimal(text)==score:matches.append(option)
            interval=re.fullmatch(r'(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)',text)
            cutoff=re.fullmatch(r'(\d+(?:\.\d+)?)\s+or\s+(higher|below|lower)',text)
            if interval:
                lo,hi=sorted(Decimal(x) for x in interval.groups())
                if lo<=score<=hi:matches.append(option)
            elif cutoff:
                point=Decimal(cutoff[1])
                if (score>=point if cutoff[2]=='higher' else score<=point):matches.append(option)
        if matches:
            if len(matches)!=1:raise Blocked('option_mismatch')
            return matches[0]
    if key in {'college_start','graduation'}:
        import calendar
        months={normalize(name):i for i,name in enumerate(calendar.month_name) if i}
        month=months.get(normalize(value))
        if month:
            choices={normalize(value),normalize(calendar.month_abbr[month]),str(month),f'{month:02d}'}
            matches=[x for x in options if normalize(x) in choices]
            if not matches:
                abbr=calendar.month_abbr[month].casefold()
                grouped=[x for x in options if re.fullmatch(r'[a-z]+\.?(?:\s*(?:/|,|or|&)\s*[a-z]+\.?)+',x.strip().casefold())
                         and abbr in {part.strip(' .')[:3] for part in re.split(r'/|,|\bor\b|&',x.casefold())}]
                matches=grouped
            if len(matches)!=1:raise Blocked('option_mismatch')
            return matches[0]
    aliases={
        'degree':{'bs':{"bachelor", "bachelors", "bachelorsdegree", "bachelorofscience", "bachelorofsciencebs", "bachelorsdegreebaorbs", "undergraduate"},
                  'ms':{"master", "masters", "mastersdegree", "masterofscience"}},
        'country':{'unitedstates':{"us", "usa", "unitedstatesofamerica","unitedstates1"}},
        'state':{'ca':{'california'},'california':{'ca'}},
        'pronouns':{'hehim':{'hehimhis'},'sheher':{'sheherhers'},'theythem':{'theythemtheirs'}},
        'location':{'berkeleyca':{'berkeleycaliforniaunitedstates','berkeleycaunitedstates','berkeleycalifornia'}},
        'citizenship':{'unitedstates':{"us", "usa", "unitedstatesofamerica"}},
        'school':{'universityofcaliforniaberkeley':{"ucberkeley","universitycaliforniaberkeley"},'universitycaliforniaberkeley':{"ucberkeley","universityofcaliforniaberkeley"}},
        'disability':{'noidonothaveadisabilityandhavenothadoneinthepast':{'noidonothaveadisability'},
                      'noidonothaveadisabilityorhavehadoneinthepast':{'noidonothaveadisability'}},
        'veteran':{'iamnotaveteran':{'iamnotaprotectedveteran'},'notaveteran':{'iamnotaprotectedveteran'}},
        'sms':{'yes':{'yesiconsenttoreceivingtextmessages'},'no':{'noidonotconsenttoreceivingtextmessages'}},
        'onsite':{'yes':{'yesiamableandwillingtoworkintheofficelocationlistedinthejobdescription'},
                  'no':{'noiamunableandorunwillingtoworkintheofficelocationlistedinthejobdescription'}},
    }
    if key=='graduation' and re.fullmatch(r'\d{4}-\d{2}',value):
        import calendar
        year,month=value.split('-');season='Spring' if 3<=int(month)<=5 else 'Summer' if 6<=int(month)<=8 else 'Fall' if 9<=int(month)<=11 else 'Winter'
        names=(calendar.month_name[int(month)],calendar.month_abbr[int(month)])
        supported={normalize(season+year),normalize(year+season),year,year+month,month+year,
                   *(normalize(name+year) for name in names),*(normalize(year+name) for name in names)}
        seasonal=[x for x in options if normalize(x) in supported]
        if len(seasonal)==1:return seasonal[0]
        # Month-range buckets: "May - Aug 2028", "January 2028 - July 2028".
        months={name:i for i,name in enumerate(('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'),1)}
        month_name=r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?'
        ranges=[]
        for option in options:
            bucket=re.fullmatch(month_name+r'(?:\s+(\d{4}))?\s*(?:[-–—]|to|through)\s*'+month_name+r'\s+(\d{4})',option.strip(),re.I)
            if not bucket:continue
            first,first_year,last,last_year=bucket.groups()
            start=f'{first_year or last_year}-{months[first.casefold()]:02d}';end=f'{last_year}-{months[last.casefold()]:02d}'
            if start<=end and start<=value<=end:ranges.append(option)
        if len(ranges)==1:return ranges[0]
    allowed={normalize(value)}|aliases.get(key,{}).get(normalize(value),set())
    if value in ('Yes','No'):
        matches=[x for x in options if normalize(x) in allowed]
        if not matches and key=='worked_outside_resume' and value=='No':
            from .util import company_normalizer
            company=company_normalizer({})((context or {}).get('company',''))
            matches=[]
            for option in options:
                statement=re.fullmatch(r'I have (?:not previously been employed|never worked)(?: (?:at|for) (.+))?',option,re.I)
                if statement and (not statement[1] or statement[1].casefold() in {'here','this company'}
                                  or company and company_normalizer({})(statement[1])==company):matches.append(option)
    else:matches=[x for x in options if normalize(x) in allowed]
    if not matches and key in {'school','major','degree'}:matches=[x for x in options if normalize(x)=='other']
    if len(matches)>1 and key in {'school','degree'} and len(set(matches))==len(matches):
        # The allowlist above establishes the same fact for every candidate.
        # Prefer the literal value, then a stable rendering; don't send known
        # synonyms to a model merely because the ATS lists several of them.
        exact=[x for x in matches if x.strip().casefold()==value.strip().casefold()]
        return exact[0] if len(exact)==1 else min(matches,key=lambda x:(len(x),x.casefold(),x))
    if len(matches)!=1:raise Blocked('option_mismatch')
    return matches[0]


# Identity, contact and free-text facts are entered verbatim, never translated into a choice.
UNMAPPED_FACTS={'full_name','first_name','last_name','preferred_name','native_name','name_pronunciation','email','alternate_email','phone','street','postal_code',
                'linkedin','github','website','skills','business_activity_details'}


def _option_mapping_id(key, fact, field):
    label=' '.join(field['label'].casefold().split()).rstrip(' *?:')
    return digest(['option_mapping',key,fact['value'],label,field.get('type',''),sorted(field.get('options',[])),field.get('help_text',''),field.get('help_links',[])])


def _mapped_option(store, key, field, provider=None):
    """Reuse or request a reviewed translation of a confirmed fact into one listed choice.

    The fact itself is unchanged; only its wording is mapped. Mappings are keyed
    by the exact fact value, question wording and choices, so a fact edit or a
    reworded question requires a new review, and package validation replays them
    without a model.
    """
    fact=store.facts().get(key);options=field.get('options',[])
    if not fact or key in UNMAPPED_FACTS or field.get('type')=='checkbox' or not field.get('required'):return None
    mapping=_option_mapping_id(key,fact,field)
    row=store.db.execute('SELECT value FROM option_mappings WHERE id=?',(mapping,)).fetchone()
    if row:return row['value'] if options.count(row['value'])==1 else None
    if not provider or not hasattr(provider,'map_option'):return None
    from .config import FACTS
    choice=provider.map_option(field,{'key':key,'label':FACTS[key],'value':fact['value']})
    if not isinstance(choice,str) or options.count(choice)!=1:return None
    from .util import now
    store.db.execute('INSERT OR REPLACE INTO option_mappings VALUES(?,?,?,?,?)',(mapping,key,fact['revision'],choice,now()))
    return choice


def _resume_internship(store, label):
    if not re.search(r'(?:prior|previous|past).*(?:internship|co.op).*(?:experience)|(?:have|completed).*(?:internship|co.op)',label,re.I):return None
    doc=store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
    if not doc:return None
    from .onboarding import selected_resume_text
    try:source=selected_resume_text(store)
    except ValueError as error:raise Blocked('document_tampered',str(error)) from None
    except Exception:return None
    text=source['text']
    section=re.split(r'\b(?:WORK )?EXPERIENCE\b',text,flags=re.I)
    if len(section)<2:return None
    work=re.split(r'(?m)^(?:PROJECTS|EDUCATION|SKILLS|PUBLICATIONS)\s*$',section[1],maxsplit=1)[0]
    lines=work.splitlines()
    from datetime import datetime,timezone
    month=datetime.now(timezone.utc).strftime('%Y-%m')
    for i,line in enumerate(lines):
        if re.search(r'\b(?:intern|internship)\b',line,re.I):
            dates=re.findall(r'\b(0[1-9]|1[0-2])/(20\d{2})\b',' '.join(lines[max(0,i-1):i+3]))
            if dates and min(y+'-'+m for m,y in dates)<=month:
                return {'value':'Yes','provenance':{'resume_hash':source['hash'],'resume_quote':line.strip()}}
    return None


def _discovery_answer(label, options, context):
    # Bounded gaps: an essay such as "how did you debug it ... what did you
    # learn" is not a referral-source question.
    if not re.search(r"how (?:did|have) you (?:first )?(?:hear(?:d)?|find out|learn(?:ed)?|come across|discover(?:ed)?)\s+(?:about|of)\b|how did you (?:first )?(?:find|discover|come across)\s+(?:us|(?:this|the|our)\s+(?:job|role|position|posting|opening|opportunity|internship|program|company|team))\b|where did you (?:first )?(?:find|hear|learn|see|discover)\b|how did you connect with|what led you to apply for (?:this|the) opportunity",label,re.I) and not (len(options)>=3 and sum(bool(re.search(r'linkedin|indeed|search engine|social media|news article',x,re.I)) for x in options)>=3 and all(x.casefold() in label.casefold() for x in options)):return None
    source=context.get('source','')
    if not source or source=='user':return None
    if not options:
        names={'gh':'Your Greenhouse careers page.','ash':'Your Ashby careers page.',
               'lv':'Your Lever careers page.','portal':'Your careers website.',
               'simplify':'The SimplifyJobs internship listings.'}
        answer=names.get(source.split(':',1)[0]) if ':' in source else None
        return {'value':answer,'provenance':{'job_source':source}} if answer else None
    normal=lambda x:re.sub(r'[^a-z0-9]','',x.casefold())
    preferred=['companywebsite','companycareerssite','companycareerspage'] if source.startswith(('gh:','ash:','lv:','portal:')) else ['jobboard','onlinejobboard']
    company=normal(context.get('company',''))
    if company and source.startswith(('gh:','ash:','lv:','portal:')):
        preferred=[company+'careerswebsite',company+'careerssite',company+'careerspage',*preferred]
    unions=[x for x in options if re.search(r'(?:/|\bor\b)\s*(?:online )?job board\s*$',x,re.I)]
    for candidate in preferred+['other']:
        matches=[x for x in options if normal(x)==candidate]
        if len(matches)==1:return {'value':matches[0],'provenance':{'job_source':source}}
    if len(unions)==1:return {'value':unions[0],'provenance':{'job_source':source}}
    return None


def _local_campus_answer(store,label):
    # UC Berkeley's official admissions brochure locates Berkeley 12 miles
    # from San Francisco. This generous, named radius is a geographic fact,
    # not a commute-time or relocation promise.
    if not re.fullmatch(r'Do you currently attend a college or university within an 80[- ]mile radius of San Francisco(?:, CA)?[? *]*',label.strip(),re.I):return None
    facts=store.facts();school=facts.get('school');start=facts.get('college_start');graduation=facts.get('graduation')
    name=re.sub(r'[^a-z0-9]','',school['value'].casefold()) if school else ''
    from .util import now
    month=now()[:7]
    if name not in {'universityofcaliforniaberkeley','universitycaliforniaberkeley','ucberkeley'} or not start or not graduation or not start['value']<=month<=graduation['value']:return None
    return {'value':'Yes','provenance':{'campus_radius':{'school_revision':school['revision'],'college_start_revision':start['revision'],
        'graduation_revision':graduation['revision'],'rule':'uc-berkeley-san-francisco-80-mile',
        'source':'https://admissions.berkeley.edu/wp-content/uploads/OUA_Outreach2022_GeneralBrochure_web.pdf'}}}


def _current_enrollment(store,label,options):
    """Enrollment follows from a confirmed university and today's date within its confirmed dates."""
    if not re.fullmatch(r'(?:are you )?currently enrolled in (?:a |an )?(?:degree program|undergraduate (?:degree )?program|bachelor.s (?:degree )?program)(?: at (?:a |an )?(?:registered |accredited )?(?:college or university|university or college|university|college))?[? *]*',label.strip(),re.I):return None
    if options and options.count('Yes')!=1:return None
    facts=store.facts();school=facts.get('school');start=facts.get('college_start');graduation=facts.get('graduation')
    from .util import now
    if not school or not start or not graduation or not start['value']<=now()[:7]<=graduation['value']:return None
    return {'value':'Yes','provenance':{'current_enrollment':{'school_revision':school['revision'],
        'college_start_revision':start['revision'],'graduation_revision':graduation['revision']}}}


def _us_citizen_or_resident(store,label,options):
    """A confirmed US citizen satisfies "citizen, green card holder, or permanent resident". Never infers No."""
    simple=re.fullmatch(r'(?:are you )?(?:a )?(?:u\.?s\.?|united states) citizen,? (?:(?:a )?green card holder,? )?or (?:a )?(?:lawful )?permanent resident[? *]*',label.strip(),re.I)
    export=re.fullmatch(r'I am one of the following: \(a\) a citizen of the United States; \(b\) a lawful permanent resident of the United States; or \(c\) a person admitted into the United States as an asylee or refugee[:? *]*',label.strip(),re.I)
    if not simple and not export:return None
    if options and options.count('Yes')!=1:return None
    citizenship=store.facts().get('citizenship')
    if not citizenship or re.sub(r'[^a-z]','',citizenship['value'].casefold()) not in {'unitedstates','us','usa','unitedstatesofamerica'}:return None
    return {'value':'Yes','provenance':{'citizenship_revision':citizenship['revision']}}


def _export_citizenship_answer(store,label,options):
    """US citizenship establishes the exact citizen/national export category."""
    if not re.search(r'\bITAR\b|International Traffic in Arms|export control',label,re.I):return None
    if not re.search(r'identify|select|which|statement',label,re.I):return None
    citizenship=store.facts().get('citizenship')
    if not citizenship or re.sub(r'[^a-z]','',citizenship['value'].casefold()) not in {'us','usa','unitedstates','unitedstatesofamerica'}:return None
    matches=[o for o in options if re.fullmatch(r'(?:A |I am a )?(?:United States|U\.?S\.?) citizen(?: or national)?\.?',o.strip(),re.I)]
    if len(matches)!=1:return None
    return {'value':matches[0],'provenance':{'citizenship_revision':citizenship['revision']}}


def _chronological_academic_year(store,label):
    match=re.fullmatch(r'Please indicate your level of education during the Fall (\d{4}) Semester[ :?*]*',label.strip(),re.I)
    if not match:return None
    facts=store.facts();start=facts.get('college_start');graduation=facts.get('graduation');degree=facts.get('degree')
    if not start or not graduation or not degree:return None
    normalized=re.sub(r'[^a-z]','',degree['value'].casefold())
    if normalized not in {'bs','bachelor','bachelors','bachelorsdegree','bachelorofscience'}:return None
    # Fall matriculation establishes chronological year of study. It does not
    # establish standing by credits, transfer credits, or an official class rank.
    if not re.fullmatch(r'\d{4}-(?:07|08|09)',start['value']):return None
    fall=f'{match[1]}-09';year=int(match[1])-int(start['value'][:4])+1
    if not start['value']<=fall<=graduation['value'] or not 1<=year<=4:return None
    return {'value':['Freshman','Sophomore','Junior','Senior'][year-1],
            'provenance':{'chronological_academic_year':{'college_start_revision':start['revision'],
                'graduation_revision':graduation['revision'],'degree_revision':degree['revision'],'fall':match[1]}}}


def selection_limit(label):
    """(fewest, most) choices a multi-select question asks for; None when it asks for one."""
    words={'two':2,'three':3,'four':4,'five':5}
    count=lambda text:int(words.get(text.casefold(),text))
    exact=re.search(r'\b(?:select|choose|pick)\s+(?:exactly\s+)?([2-9]|two|three|four|five)\b',label,re.I)
    if exact:return count(exact[1]),count(exact[1])
    most=re.search(r'\b(?:select|choose|pick)\s+up to\s+([1-9]|two|three|four|five)\b',label,re.I)
    if most:return 1,count(most[1])
    if re.search(r'all that apply|\b(?:indicate|select|check|choose) all\b',label,re.I):return 1,None
    return None


def choice_limit(field):
    """Checkbox groups permit several choices even when their label omits 'select all'."""
    if field.get('type')!='checkbox-group':return None
    explicit=selection_limit(field['label'])
    if explicit:return explicit
    if re.search(r'\b(?:select|choose|pick)\s+(?:only\s+)?(?:one|1)\b|\bsingle choice\b',field['label'],re.I):return 1,1
    return 1,None


def selections(value, options):
    """The exact listed choices of an answer, in the form's order; None if any is not listed once."""
    if options.count(value)==1:return [value]
    parts=value.split('; ')
    if len(parts)<2 or len(set(parts))!=len(parts) or any(options.count(part)!=1 for part in parts):return None
    return [option for option in options if option in parts]


def _multiple(field, chosen):
    """Join several choices when the question asks for that many, in the form's order."""
    limit=choice_limit(field)
    if not limit or not chosen or len(set(chosen))!=len(chosen) or any(field['options'].count(x)!=1 for x in chosen):return None
    if len(chosen)<limit[0] or limit[1] is not None and len(chosen)>limit[1]:return None
    return '; '.join(option for option in field['options'] if option in chosen)


def _listed_answer(field, value):
    """Exact listed choices, as many as a multi-select question asks for."""
    options=field.get('options',[])
    if choice_limit(field):
        chosen=selections(value,options)
        return bool(chosen) and value==_multiple(field,chosen)
    return options.count(value)==1


def _cohort_answer(store, field, context):
    """Cohorts that begin within the confirmed start window. Every choice must be a readable date range."""
    label=field['label']
    if not field.get('options') or not re.search(r'\bcohorts?\b|\bsessions?\b|\bstart dates?\b',label,re.I) or selection_limit(label)!=(1,None):return None
    facts=store.facts();early=facts.get('earliest_start');late=facts.get('latest_start')
    if not early or not late:return None
    months=('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec')
    title_years=set(re.findall(r'\b(20\d{2})\b',context.get('title','')))
    chosen=[]
    for option in field['options']:
        start=re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+20\d{2})?\s*(?:[-–—]|to\b|through\b)',option,re.I)
        years=re.findall(r'\b(20\d{2})\b',option) or sorted(title_years)
        if not start or len(set(years))!=1:return None
        month=f'{years[0]}-{months.index(start[1].casefold())+1:02d}'
        if early['value']<=month<=late['value']:chosen.append(option)
    value=_multiple(field,chosen)
    return {'value':value,'provenance':{'start_window':{'earliest_start_revision':early['revision'],'latest_start_revision':late['revision']}}} if value else None


def _context_preference(store, label, options, context, field=None):
    if not store.settings()['contextual_preferences'] or not options:return None
    low=label.casefold();facts=store.facts()
    value=None;evidence={}
    seasons=[x for x in options if re.fullmatch(r'(?:Spring|Summer|Fall|Winter) 20\d{2}',x)]
    if (field and choice_limit(field) and re.fullmatch(r'(?:what|which) (?:development|programming|coding) languages are you (?:most )?experienced with[? *]*',low)):
        skills=facts.get('skills')
        if skills:
            terms={x.strip().casefold() for x in re.split(r'[,;\n]',skills['value'])}
            chosen=[x for x in options if x.strip().casefold() in terms]
            value=_multiple(field,chosen)
            if value:evidence={'skills_revision':skills['revision']}
    elif employment_country_question(label):
        country=facts.get('country')
        if country:
            try:value=_option_value('country',country['value'],options)
            except Blocked:return None
            evidence={'country_revision':country['revision']}
        else:
            location=facts.get('location',{})
            if re.fullmatch(r'Berkeley,?\s+(?:CA|California)(?:,?\s+United States)?',location.get('value',''),re.I):
                try:value=_option_value('country','United States',options)
                except Blocked:return None
                evidence={'residence_location_revision':location['revision']}
    elif (re.fullmatch(r'are you looking for a summer internship[? *]*',low)
          and 'summer-internship' in store.settings()['seniority']
          and re.search(r'\bsummer\b',context.get('title',''),re.I)):
        value='Yes';evidence={'seniority':sorted(store.settings()['seniority']),'job_title':context['title']}
    elif re.search(r'preferred (?:programming|coding) language',low) and re.search(r'interviews?',low):
        # The opt-in permits routine preferences supported by confirmed skills.
        # Choose the first listed skill available as an exact language option.
        skills=facts.get('skills')
        if skills:
            normalize=lambda x:re.sub(r'[^a-z0-9+#]','',x.casefold())
            languages={'python','java','javascript','typescript','c','c++','c#','go','golang','rust','ruby','swift','kotlin','scala'}
            for skill in re.split(r'[,;\n]',skills['value']):
                term=normalize(skill.strip())
                matches=[x for x in options if normalize(x)==term]
                if term in languages and len(matches)==1:
                    value=matches[0];evidence={'skills_revision':skills['revision']};break
    elif (re.search(r'\bcohorts?\b',low) and re.search(r'\binternships?\b',low)
          and re.search(r'choose which cohort works best for you',low)
          and facts.get('summer_2027_available',{}).get('value')=='Yes'
          and facts.get('earliest_start',{}).get('value')=='2027-05'):
        # This chooses an approximate summer intake; it does not promise a
        # particular duration or availability for a second cohort.
        summer=[x for x in options if re.fullmatch(r'Summer \(May\s*[-–—]\s*September\)',x)]
        if len(summer)==1:
            value=summer[0];evidence={'summer_2027_available_revision':facts['summer_2027_available']['revision'],
                                    'earliest_start_revision':facts['earliest_start']['revision']}
    elif seasons and re.search(r'internship.*(?:available|position|season|term)|(?:season|term).*intern',low):
        start=facts.get('earliest_start',{}).get('value','')
        # Choose the explicitly targeted summer intake, not unrelated academic terms.
        summer=[x for x in seasons if start and x=='Summer '+start[:4] and 5<=int(start[5:])<=8]
        if len(summer)==1 and facts.get('summer_2027_relocate',{}).get('value')=='Yes':
            value=summer[0];evidence={'earliest_start':start,'summer_2027_relocate':'Yes'}
    elif re.search(r'(?:engineering work|engineering area|engineering team|internship (?:role|track)).*(?:choice|interested|excited)|(?:first|second) choice.*engineering',low):
        skills=facts.get('skills',{}).get('value','').casefold()
        areas={'product':('react','typescript','next','javascript'),'backend':('python','sql','fastapi','postgresql','node'),'infrastructure':('docker','aws','gcp','ci/','raspberry'),'security':('security','cryptography')}
        scores=[]
        for option in options:
            terms=set(t for area,ts in areas.items() if area in option.casefold() for t in ts)
            score=sum(t in skills for t in terms)
            if score:scores.append((score,option))
        scores.sort(key=lambda pair:(-pair[0],pair[1]))
        index=1 if 'second choice' in low else 0
        if len(scores)>index:
            value=scores[index][1];evidence={'skills_revision':facts['skills']['revision'],'rank':index+1}
    elif re.search(r'currently located.*(?:bay area|san francisco).*(?:office|in.person)',low):
        location=facts.get('location',{})
        if facts.get('onsite',{}).get('value')=='Yes' and re.search(r'\b(?:Berkeley|San Francisco|Bay Area)\b',location.get('value',''),re.I):
            yes=[x for x in options if re.match(r'^yes\b',x,re.I)]
            if len(yes)==1:value=yes[0];evidence={'location_revision':location['revision'],'onsite_revision':facts['onsite']['revision']}
    elif re.search(r'(?:require|need).*relocation.*(?:sf|san francisco|bay area)',low):
        location=facts.get('location',{})
        if re.search(r'\b(?:Berkeley|San Francisco|Bay Area)\b',location.get('value',''),re.I):
            no=[x for x in options if x.casefold()=='no']
            if len(no)==1:value=no[0];evidence={'location_revision':location['revision'],'already_in_bay_area':True}
    elif re.search(r'relocat',low) and re.search(r'\blocations?\b|\boffices?\b|\bcities\b',low) and selection_limit(label):
        # Destinations the applicant already chose to search, when they confirmed relocating.
        from .policy import LOCATIONS
        summer=re.search(r'summer\s*2027',context.get('title',''),re.I) and facts.get('summer_2027_relocate')
        relocate=facts.get('summer_2027_relocate') if summer else facts.get('relocate')
        settings=store.settings()
        preferred=[x for x in settings['locations']+(settings['summer_2027_locations'] if summer else []) if not x.casefold().startswith('remote')]
        if relocate and relocate['value']=='Yes':
            chosen=[x for x in options if any(re.search(LOCATIONS.get(p,re.escape(p)),x,re.I) for p in preferred)]
            value=_multiple(field or {'label':label,'options':options},chosen)
            if value:evidence={'relocation_revision':relocate['revision'],'locations':preferred}
    elif re.search(r'(?:select|choose).*(?:location).*(?:work)|location.*(?:select|choose).*(?:work)|(?:office|location).*(?:prefer|work|based)|(?:prefer|work).*(?:office|location)|which office.*applying|^san francisco hq',low):
        if facts.get('onsite',{}).get('value')=='Yes' and re.search(r'Berkeley|San Francisco|Bay Area',facts.get('location',{}).get('value',''),re.I):
            local=[x for x in options if re.search(r'San Francisco|Bay Area|Berkeley',x,re.I)]
            if len(local)==1:value=local[0];evidence={'location_revision':facts['location']['revision'],'onsite':'Yes'}
    return {'value':value,'provenance':{'contextual_preference':evidence}} if value else None


def _context_pronunciation(store, field):
    """Read an explicitly approved spelling; never invent pronunciation from a name."""
    if (field_key(field['label'])!='name_pronunciation' or field.get('type') not in {'text','textarea'}
            or field.get('options') or not store.settings()['tailored_writing']):return None
    matches=[]
    for template in store.templates():
        for line in template['body'].splitlines():
            match=re.fullmatch(r'\s*Name prono?unciation:\s*(.+?)\s*',line,re.I)
            if not match:continue
            value=match[1]
            if len(value)>1 and (value[0],value[-1]) in {('“','”'),('"','"'),("'","'")}:
                value=value[1:-1]
            if value.strip():matches.append((value,template))
    if not matches:return None
    if len({value for value,_ in matches})!=1:raise Blocked('mapping_review',field['label'])
    value,template=matches[0]
    return {'value':value,'provenance':{'context_fact':{'key':'name_pronunciation','template_id':template['id'],'revision':template['revision']}}}


def _graduation_confirmation(store, field):
    if not field.get('options') or not re.search(r'will you.*graduat|(?:do you|are you).*graduat|is your (?:expected |anticipated )?graduation date|i confirm.*graduation date',field['label'],re.I):return None
    from .graduation import qualifies
    graduation=store.facts().get('graduation')
    result=qualifies(graduation['value'],field['label']) if graduation else None
    if result is None:return None
    return {'value':'Yes' if result else 'No','provenance':{'graduation_window_revision':graduation['revision']}}


def _role_answer(label, options, context, store):
    if not re.search(r'(?:which|what).{0,40}(?:position|role|internship).{0,40}(?:apply|applying|interested)|(?:position|role|internship).{0,30}applying',label,re.I):return None
    title=context.get('title','')
    if not title:return None
    normalize=lambda x:re.sub(r'[^a-z0-9]','',x.casefold())
    matches=[x for x in options if normalize(x)==normalize(title)]
    if options and not matches:
        tokens=lambda x:set(re.findall(r'[a-z][a-z0-9+#]+',x.casefold()))-{'intern','internship','summer','position','role','engineering','engineer'}
        title_tokens=tokens(title)
        skills=tokens(store.facts().get('skills',{}).get('value',''))
        ranked=sorted(((len(tokens(x)&title_tokens)*3+len(tokens(x)&skills),x) for x in options),reverse=True)
        if ranked and ranked[0][0]>0 and (len(ranked)==1 or ranked[0][0]>ranked[1][0]):matches=[ranked[0][1]]
    if options and len(matches)!=1:return None
    return {'value':matches[0] if options else title,'provenance':{'job_title':title}}


def _sentence_cap(field, context=None):
    pattern=r'(\d+)(?:\s*[-–]\s*(\d+))?\s+sentences?'
    limit=re.search(pattern,field['label'],re.I)
    if not limit and re.search(r'^(?:first|second|third|fourth|\d+(?:st|nd|rd|th)?) example',field['label'],re.I):
        for label in (context or {}).get('form_questions',[]):
            shared=re.search(r'each (?:bullet|example|answer).{0,180}?'+pattern,label,re.I)
            if shared:limit=shared;break
    return int(limit[2] or limit[1]) if limit else None


def _fits_writing_limits(value, field, context=None):
    if field.get('maxlength',-1)>0 and len(value)>field['maxlength']:return False
    sentence_limit=_sentence_cap(field,context)
    if sentence_limit is not None and len(re.split(r'(?<=[.!?])\s+(?=[A-Z])',value))>sentence_limit:return False
    word_limit=re.search(r'(?:at most|up to|no more than|maximum|max\.?|under|limit(?: of)?)\s*(\d+)\s+words?',field['label'],re.I)
    return not word_limit or len(value.split())<=int(word_limit[1])


def _compatible_binding(key, label):
    terms={
        'hispanic_latino':r'hispanic|latino',
        'temporary_work_authorization':r'currently.*(?:hold|have).*temporary.*(?:work|employment).*authoriz',
        'programming_proficiency':r'(?:programming|program analysis).*proficien|proficien.*(?:programming|program analysis)',
        'full_name':r'\bname\b', 'first_name':r'\bfirst.*name\b', 'last_name':r'\blast.*name\b',
        'preferred_name':r'preferred.*name|nickname|(?:should|may|can) we call you', 'native_name':r'native.*name|name.*native',
        'email':r'\be.?mail\b', 'alternate_email':r'\b(?:alternate|alternative|secondary|additional|other|backup) e.?mail\b', 'phone':r'phone|mobile|dial|calling code',
        'location':r'location|located|based|resid|\bcity\b', 'street':r'street|address',
        'city':r'\bcity\b', 'state':r'\bstate\b|province', 'postal_code':r'postal|\bzip\b',
        'country':r'country|residen', 'linkedin':r'linkedin', 'github':r'github', 'website':r'website|portfolio',
        'high_school':r'high school', 'school':r'school|university|college|studying|institution',
        'degree':r'degree|education|qualification', 'highest_completed_degree':r'(?:highest|completed|earned).*(?:degree|education|qualification)',
        'major':r'major|field of study|discipline', 'gpa':r'\bgpa\b|grade point',
        'college_start':r'(?:college|university|education|school).*start|start.*(?:college|university|education|school)',
        'graduation':r'graduat', 'earliest_start':r'(?:earliest|available|availability).*start|start.*(?:earliest|available|availability)',
        'latest_start':r'latest.*start|start.*latest', 'skills':r'skills|technolog|languages',
        'race':r'race|ethnic', 'gender':r'gender', 'pronouns':r'\bpronouns?\b', 'name_pronunciation':r'prono?unciation|pronounce',
        'veteran':r'veteran|military', 'disability':r'disabil', 'salary':r'salary|compensation|pay expect|hourly (?:rate|pay)',
        'notice_period':r'notice', 'relocate':r'relocat',
        'summer_2027_relocate':r'relocat', 'summer_2027_available':r'availab|commit',
        'worked_outside_resume':r'work|employ', 'contacts_outside_resume':r'contact|know anyone|family|relative|related to|referred|spouse|partner',
        'needs_sponsorship':r'sponsor|visa|immigration|h.?1b',
        'work_authorized_us':r'authoriz|eligible to work|work permit|legally.*work|right.*work',
        'unrestricted_authorization':r'authoriz|work permit|legally.*work|right.*work',
        'us_person':r'(?:u\.?s\.?|united states) person|itar|export.*person',
        'citizenship':r'citizen|nationality',
        'professional_years':r'years?.*(?:experience|professional|work)|experience.*years?',
        'onsite':r'on.?site|in.person|office|hybrid|headquarters|\bhq\b',
        'background_check':r'background.*check|screening',
        'recording':r'record|video',
        'sms':r'sms|text message',
        'outside_business_activity':r'currently own,? operate,? or provide services.*business or organization',
        'business_activity_details':r'describe the nature of the activity.*your role.*overlap',
        'recruitment_data_consent':r'(?:store|process).*data.*(?:considering|consideration|eligibility).*application.*employment',
        'demographic_data_consent':r'consent.{0,80}(?:collect|stor|process).{0,120}(?:demographic|self.identif|voluntary.{0,20}survey)',
        'conflict_disclosures':r'(?:personal|familial) relationships?(?:.|\n){0,300}outside business activit',
        'government_official':r'government official|public (?:position|function)|politically exposed|holding such a position',
        'finra_registered':r'^are you (?:currently )?registered with finra[? *]*$',
        'securities_licenses':r'^(?:are you actively maintaining|do you (?:actively )?(?:hold|maintain)) (?:any )?securities licenses[? *]*$',
        'over_18':r'^(?:at the time of application,? )?are you (?:18\+ years of age|18 or older|at least 18 years old)[? *]*$',
        'fulltime_start':r'^when will you be available to work as a full.time,? permanent employee\?',
        'nights_weekends':r'^are you willing and able to work nights and weekends[? *]*$',
        'robots_experience':r'^do you have experience working with robots[? *]*$',
        'humanoids_experience':r'^have you worked with humanoids[? *]*$',
    }
    if key=='degree' and (re.search(r'completed|earned|obtained',label,re.I) or re.search(r'highest',label,re.I) and field_key(label)!='degree'):return False
    if key=='highest_completed_degree' and field_key(label)=='degree':return False
    if key=='email' and field_key(label)=='alternate_email':return False
    return key in terms and bool(re.search(terms[key],label,re.I))


def _field_fact_key(store,label,context):
    key=field_key(label)
    if key=='earliest_start' and store.facts().get('fulltime_start') and context.get('title') and not re.search(r'internship|co.op|part.time',label,re.I):
        from .policy import employment_kind
        if employment_kind(context)=='new-grad':return 'fulltime_start'
    return key


def _compatible_field(key, field, context=None):
    label=field['label']
    if key=='fulltime_start' and _compatible_binding('earliest_start',label):
        from .policy import employment_kind
        return bool((context or {}).get('title') and employment_kind(context)=='new-grad' and not re.search(r'internship|co.op|part.time',label,re.I))
    if key=='degree' and any(re.fullmatch(r'Freshman|Sophomore|Junior|Senior',o,re.I) for o in field.get('options',[])):return False
    if key=='race' and re.search(r'hispanic|latino',label,re.I):return False
    if key in {'work_authorized_us','needs_sponsorship','unrestricted_authorization'} and re.search(r'temporary.*authoriz',label,re.I):return False
    if key in {'recruitment_data_consent','demographic_data_consent'} and re.search(r'marketing|advertis|sell|sale|third.part',label,re.I):return False
    if key in {'summer_2027_available','summer_2027_relocate'} and not re.search(r'summer\s*2027',label+' '+(context or {}).get('title',''),re.I):return False
    if key in {'work_authorized_us','needs_sponsorship','unrestricted_authorization'}:
        if key!='needs_sponsorship' and re.search(r'\b(?:not|never)\b.{0,20}\b(?:authorized|eligible)|\b(?:unauthorized|ineligible)\b',label,re.I):return False
        foreign=r'\b(?:canada|united kingdom|australia|germany|france|india|singapore|japan|china|brazil|mexico|ireland|netherlands)\b'
        if re.search(foreign,label,re.I):return False
        explicit_us=bool(re.search(r'United States|\bU\.?S\.?\b|\bUSA\b',label,re.I))
        generic_country=bool(GENERIC_WORK_COUNTRY.search(label))
        if re.search(r'work(?:ing)?\s+in\b',label,re.I) and not explicit_us and not generic_country:return False
        if generic_country and not _us_location(work_country_location(context or {})):return False
    if key in {'college_start','graduation'} and re.fullmatch(r'(?:start|end) date (?:month|year)\s*\*?',label,re.I):
        return field.get('section')=='education'
    return _compatible_binding(key,label)


def _us_location(location):
    if us_city_location(location):return True
    if re.search(r'\b(?:Canada|Costa Rica|Spain|United Kingdom|UK|U\.K\.|Australia|Germany|France|India|Singapore|Japan|China|Brazil|Mexico|Ireland|Netherlands)\b',location,re.I):return False
    return bool(re.search(r'\bUnited States\b|\bUSA?\b|,\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b|,\s*(?:California|Washington|New York)\b',location,re.I))


def _work_status_answer(store, field, key, context):
    """Exact descriptive ATS choices with revision-bound supporting premises.

    General authorization alone does not establish authorization for every
    employer. Citizenship or an unrestricted declaration supplies that premise.
    """
    if key not in {'work_authorized_us','needs_sponsorship','unrestricted_authorization'}:return None
    if not _compatible_field(key,field,context):return None
    facts=store.facts();fact=facts.get(key);citizenship=facts.get('citizenship')
    citizen=bool(citizenship and re.sub(r'[^a-z]','',citizenship['value'].casefold()) in {'us','usa','unitedstates','unitedstatesofamerica'})
    value=fact['value'] if fact else ('No' if key=='needs_sponsorship' else 'Yes') if citizen else None
    if value not in {'Yes','No'}:return None
    if not fact and not re.search(r'United States|\bU\.?S\.?\b|\bUSA\b',field['label'],re.I) and not _us_location(work_country_location(context)):return None
    if citizen and value!=('No' if key=='needs_sponsorship' else 'Yes'):raise Blocked('mapping_review','Conflicting US citizenship and work-status facts')
    provenance={key+'_revision':fact['revision']} if fact else {'citizenship_revision':citizenship['revision']}
    options=field.get('options',[])
    if not fact and (not options or set(options)=={'Yes','No'}):return {'value':value,'provenance':provenance}
    # A choice referring to the role's location needs US posting evidence even
    # when the question itself mentions the United States.
    if not _us_location(work_country_location(context)):return None
    patterns={
        'work_authorized_us':{
            'Yes':r'Yes, I am currently eligible to work in the location where this role is based\.?',
            'No':r'No, I am not currently eligible to work in the location where this role is based\.?'},
        'needs_sponsorship':{
            'Yes':r'Yes, I will require visa sponsorship now or in the future to continue working in the country where this role is based\.?',
            'No':r'No, I do not require visa sponsorship now or in the future to continue working in the country where this role is based\.?'}}
    matches=[o for o in options if re.fullmatch(patterns.get(key,{}).get(value,r'(?!)'),o.strip(),re.I)]
    if key in {'work_authorized_us','unrestricted_authorization'}:
        unrestricted=facts.get('unrestricted_authorization')
        if value=='Yes' and (citizen or unrestricted and unrestricted['value']=='Yes'):
            if citizen and unrestricted and unrestricted['value']=='No':raise Blocked('mapping_review','Conflicting unrestricted work-status facts')
            any_employer=[o for o in options if re.fullmatch(r'Yes, I am authorized to work in this country for any employer\.?',o.strip(),re.I)]
            if any_employer:
                matches+=any_employer
                provenance.update({'citizenship_revision':citizenship['revision']} if citizen else {'unrestricted_authorization_revision':unrestricted['revision']})
        elif value=='No':
            matches += [o for o in options if re.fullmatch(r'No, I am not authorized to work in this country for any employer\.?',o.strip(),re.I)]
    if len(matches)!=1:return None
    return {'value':matches[0],'provenance':provenance}


def _employer_scope(store,host,label,context):
    employer=host.split('|',1)[1] if '|' in host else store.company(context['company']) if context.get('company') else None
    prior={store.company(x) for x in store.settings()['prior_employers']}
    pattern=r'[^a-z0-9]*'.join(re.escape(char) for char in employer or '')
    named=bool(employer and (re.search(r'\b(?:this|your|the) company\b',label,re.I) or re.search(r'(?<![a-z0-9])'+pattern+r'(?![a-z0-9])',label.casefold())))
    words=re.findall(r'[a-z0-9]+',context.get('company','').casefold())
    short=words[0] if words and len(words[0])>=4 and words[0] not in {'software','university','research','group','technologies','capital'} else None
    if short and re.search(r'(?<![a-z0-9])'+re.escape(short)+r'(?![a-z0-9])',label.casefold()):named=True
    return named,employer in prior or bool(short and any(p.startswith(short) for p in prior))


def _foreign_targets(store, template, context):
    targets=re.findall(r"(?:^|[.!?]\s+)([A-Z][\w -]{1,50}?) (?:feels|especially caught|is interesting)",template['body']) if template['category']=='motivation' else []
    return [name for name in targets if store.company(name) not in store.company(context.get('company',''))]


def _resume_evidence(store):
    """Read the applicant's uploaded resume, binding excerpts to its exact bytes."""
    doc=store.db.execute("SELECT * FROM documents WHERE kind='resume'").fetchone()
    if not doc:return None,''
    from .util import safe_document
    data=safe_document(store.root/'documents'/doc['filename'],store.root/'documents').read_bytes()
    import hashlib
    if hashlib.sha256(data).hexdigest()!=doc['hash']:raise Blocked('document_tampered')
    if b'%%EOF' not in data:return doc['hash'],''
    from pypdf import PdfReader
    import io
    try:text='\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(data)).pages)
    except Exception:return doc['hash'],''
    return doc['hash'],text


def _approved_sentences(store, context):
    choices=[]
    for t in store.templates():
        # Identify an explicitly targeted employer in motivation samples, then omit
        # those sentences when adapting the sample for another employer.
        foreign=_foreign_targets(store,t,context)
        for i,sentence in enumerate(re.split(r'(?<=[.!?])\s+(?=[A-Z])',t['body'])):
            if any(re.search(r'\b'+re.escape(name)+r'\b',sentence,re.I) for name in foreign):continue
            choices.append({'id':t['id']+':'+str(i),'text':sentence,'template_id':t['id'],'revision':t['revision']})
    if store.settings()['tailored_writing']:
        resume_hash,text=_resume_evidence(store)
        for i,excerpt in enumerate(text.splitlines()):
            excerpt=excerpt.strip()
            if excerpt:
                choices.append({'id':'resume:'+str(i),'text':excerpt,'resume_hash':resume_hash})
    # Keep inference cost bounded as an applicant adds a larger source library.
    if sum(len(x['text']) for x in choices)>24000:
        terms=set(re.findall(r'[a-z]{4,}',(context.get('title','')+' '+context.get('description','')).casefold()))
        ranked=sorted(enumerate(choices),key=lambda item:(-len(terms&set(re.findall(r'[a-z]{4,}',item[1]['text'].casefold()))),item[0]))
        selected=[];budget=24000
        for index,choice in ranked:
            if len(choice['text'])<=budget:
                selected.append((index,choice));budget-=len(choice['text'])
        choices=[choice for _,choice in sorted(selected)]
    return choices


def _abstention_key(store, method, context):
    """Every input a structured model call sees. Retrying identical inputs only spends requests."""
    from .materials import writing_context_hash
    settings=store.settings()
    return digest(['model_abstention',method,context['field_context'],writing_context_hash(store,context),
                   {k:v['revision'] for k,v in store.facts().items()},sorted((t['id'],t['revision']) for t in store.templates()),
                   [r[0] for r in store.db.execute("SELECT hash FROM documents WHERE kind='resume'")],
                   [settings[k] for k in ('provider','provider_model','model_effort','model_escalation','tailored_writing')]])


def _abstained(store, key):
    return bool(key and store.db.execute('SELECT 1 FROM model_abstentions WHERE id=?',(key,)).fetchone())


def _abstain(store, key, method, label):
    from .util import now
    if key:store.db.execute('INSERT OR REPLACE INTO model_abstentions VALUES(?,?,?,?)',(key,method,label,now()))


def _validate_writing(store, answer):
    templates={t['id']:t for t in store.templates()}
    parts=answer['provenance'].get('sample_parts',[])
    if answer['provenance'].get('context_answer') and answer['provenance'].get('facts_hash')!=digest(store.facts()):raise Blocked('unsupported_or_stale_sample')
    if answer['provenance'].get('tailored'):
        if not store.settings()['tailored_writing'] or not parts or answer['provenance'].get('text_hash')!=digest(answer['value']):raise Blocked('unsupported_or_stale_sample')
    elif not parts or answer['value']!=' '.join(p['text'] for p in parts):raise Blocked('unsupported_or_stale_sample')
    resume=None
    for part in parts:
        if 'fact_key' in part:
            fact=store.facts().get(part['fact_key'])
            if not fact or fact['revision']!=part['revision'] or fact['value']!=part['text']:raise Blocked('unsupported_or_stale_sample')
            continue
        if 'resume_hash' in part:
            if not store.settings()['tailored_writing']:raise Blocked('unsupported_or_stale_sample')
            if resume is None:resume=_resume_evidence(store)
            if part['resume_hash']!=resume[0] or not part['text'] or part['text'] not in resume[1]:raise Blocked('unsupported_or_stale_sample')
            continue
        source=templates.get(part['template_id'])
        if not source or source['revision']!=part['revision'] or part['text'] not in source['body']:raise Blocked('unsupported_or_stale_sample')


def _explicit_context_answer(store, host, field, key, provider, context):
    topics={
        'veteran':r'military|veteran|armed forces|national guard|reservist',
        'government_official':r'government|politically exposed|public function|PEP',
        'conflict_disclosures':r'conflict|outside business|personal relationship|familial|retained IP',
        'demographic_data_consent':r'demographic.{0,40}consent|consent.{0,40}demographic',
        'name_pronunciation':r'pronunciation|pronounciation|pronounce',
        'over_18':r'18\+|18 or older|18 years|date of birth|age:',
        'fulltime_start':r'permanent|full.time.{0,40}(?:start|availab)',
        'nights_weekends':r'nights|weekends',
        'robots_experience':r'robot', 'humanoids_experience':r'humanoid',
    }
    if key not in topics or not provider or not hasattr(provider,'explicit_context_answer') or not store.settings()['tailored_writing']:return None
    # Only user-approved personal wording is eligible; resume/reference/style
    # sources do not silently establish sensitive declarations or commitments.
    choices=[c for c in _approved_sentences(store,{**context,'description':field['label']+' '+context.get('description','')}) if 'template_id' in c and re.search(topics[key],c['text'],re.I)]
    if not choices:return None
    abstention=_abstention_key(store,'explicit_context_answer',context)
    if _abstained(store,abstention):return None
    from .config import FACTS
    draft=provider.explicit_context_answer(field,choices,{k:{'label':FACTS[k],'value':v['value']} for k,v in store.facts().items()},context)
    by_id={c['id']:c for c in choices};ids=draft.get('sentence_ids',[]);value=draft.get('answer')
    if (not isinstance(value,str) or not value or not ids or len(ids)!=len(set(ids)) or any(x not in by_id for x in ids)
            or field.get('options') and not _listed_answer(field,value)):
        _abstain(store,abstention,'explicit_context_answer',field['label']);return None
    from .materials import writing_context_hash
    parts=[{k:v for k,v in by_id[x].items() if k!='id'} for x in ids]
    writing={'value':value,'provenance':{'tailored':True,'context_answer':True,'explicit_context':True,'sample_parts':parts,
        'text_hash':digest(value),'context_hash':writing_context_hash(store,context),'field_hash':digest(context['field_context']),'facts_hash':digest(store.facts())}}
    _validate_writing(store,writing);validate_numeric(value,field)
    if not _fits_writing_limits(value,field,context):raise Blocked('answer_too_long',field['label'])
    store.save_writing_answer(host,field['label'],field.get('options',[]),writing)
    store.resolve_known_question(host,field['label'],field.get('options',[]),field=field,context=context)
    return {'field':field,**writing}


def _unanswered_demographic_decline(store, field):
    """An explicit applicant preference may decline a survey without asserting a trait."""
    preference=store.facts().get('decline_unanswered_demographics')
    if not preference or preference['value']!='Yes' or not field.get('required'):return None
    if not re.fullmatch(r'(?:sexual orientation|gender(?: identity)?|pronouns?|race(?:\s*/\s*ethnicity)?|ethnicity|(?:protected )?veteran status|disability status)[? *]*',field['label'].strip(),re.I):return None
    decline=(r"(?:I (?:do not|don't) (?:wish|want) to answer(?: this question)?|"
             r"(?:I )?(?:prefer|choose) not to (?:answer|say|disclose|self.identify)|"
             r"(?:I )?decline to (?:specify|self.identify|identify my protected veteran status))")
    options=[o for o in field.get('options',[]) if re.fullmatch(decline,o.strip(),re.I)]
    if len(options)!=1 or not _listed_answer(field,options[0]):return None
    if not _fits_writing_limits(options[0],field,{}):return None
    return {'field':field,'value':options[0],'provenance':{'demographic_decline_revision':preference['revision']}}


def resolve(store, host, field, provider=None, context=None):
    label=field['label'];options=field.get('options',[]);context=dict(context or {})
    context['max_sentences']=_sentence_cap(field,context)
    context['single_line']=field.get('type')=='text'
    context['field_context']=field_context(host,field,context)
    if field.get('type')=='combobox' and not options:
        if field.get('required') or field.get('value'):raise Blocked('unsupported_widget',label+' — dropdown options were not available for validation')
        return None
    pending_binding=None
    from .materials import writing_context,writing_context_hash
    context.update(writing_context(store))
    if REFUSE.search(label):raise Blocked('human_work_sample',label)
    writing=store.writing_answer(host,label,options)
    if writing:
        try:
            _validate_writing(store,writing)
            if context['single_line'] and re.search(r'[\r\n]',writing['value']):raise Blocked('writing_upgrade_needed')
            if store.settings()['tailored_writing'] and not writing['provenance'].get('tailored'):raise Blocked('writing_upgrade_needed')
            if writing['provenance'].get('tailored') and writing['provenance'].get('context_hash')!=writing_context_hash(store,context):raise Blocked('stale_writing_context')
            if writing['provenance'].get('context_answer') and writing['provenance'].get('field_hash')!=digest(context['field_context']):raise Blocked('stale_writing_context')
            if not _fits_writing_limits(writing['value'],field,context):raise Blocked('answer_too_long',label)
            validate_numeric(writing['value'],field)
        except Blocked:
            if not provider:raise
            store.db.execute('DELETE FROM writing_answers WHERE id=?',(store.question_key(host,label,options),))
        else:
            store.resolve_known_question(host,label,options,field=field,context=context)
            return {'field':field,**writing}
    graduation_confirmation=_graduation_confirmation(store,field)
    # An old literal approval cannot override a comparison with the current date.
    saved=None if graduation_confirmation else store.saved_answer(host,label,options,field=field,context=context)
    if field.get('section_entry',0)>0 and field.get('section') in {'education','employment'} and not saved:
        if field.get('required') or field.get('value'):raise Blocked('repeated_entry_review','Confirm the answer for this specific '+field['section']+' entry')
        return None
    key=None;template=None;derived=None;draft_rejection=None
    if saved:
        exact=_field_fact_key(store,label,context)
        if field.get('section')=='education' and re.fullmatch(r'(start|end) date (month|year)\s*\*?',label,re.I):
            exact='college_start' if label.lower().startswith('start') else 'graduation'
        legacy_literal=not saved['fact_key'] and not store.db.execute('SELECT 1 FROM question_contexts WHERE id=?',(saved['id'],)).fetchone()
        if legacy_literal and (field.get('section') or context.get('company')):
            fact=store.facts().get(exact)
            if not fact or not _compatible_field(exact,field,context):raise Blocked('stale_answer',label)
            expected=present(exact,fact['value'],field)
            if options:
                try:expected=_option_value(exact,expected,options,context)
                except Blocked:raise Blocked('stale_answer',label)
            if saved['value']!=expected:raise Blocked('stale_answer',label)
        if saved['fact_key'] and (not _compatible_field(saved['fact_key'],field,context) or exact and exact!=saved['fact_key']):
            raise Blocked('stale_answer',label)
        if saved['fact_key']:
            fact=store.facts().get(saved['fact_key'])
            if not fact:raise Blocked('stale_answer',label)
            if saved['fact_key'] in {'worked_outside_resume','contacts_outside_resume'}:
                named,prior_match=_employer_scope(store,host,label,context)
                if fact['value']!='No' or not named or prior_match:raise Blocked('stale_answer',label)
        key=saved['fact_key']
        value=present(key,fact['value'],field) if key else saved['value']
        provenance=({'fact_key':key,'revision':fact['revision']} if key and fact['value']!=saved['value']
                    else {'answer_id':saved['id'],'revision':saved['revision']})
    else:
        key=_field_fact_key(store,label,context)
        if key and not _compatible_field(key,field,context):raise Blocked('mapping_review',label)
        if key=='name_pronunciation' and key not in store.facts():
            derived=_context_pronunciation(store,field)
            if derived:key=None
        if (key=='conflict_disclosures' and key not in store.facts()
                and re.search(r'do you have.{0,10}(?:a\)|any)',label,re.I)
                and not re.search(r'want to|intend to|plan to|continue',label,re.I)
                and store.facts().get('outside_business_activity',{}).get('value')=='Yes'):
            # A confirmed positive answers an any-of declaration. A negative
            # cannot establish the absence of the other listed conflicts.
            derived={'value':'Yes','provenance':{'outside_business_activity_revision':store.facts()['outside_business_activity']['revision']}}
            key=None
        if key and key not in store.facts():
            explicit=_explicit_context_answer(store,host,field,key,provider,context)
            if explicit:return explicit
        education_date=field.get('section')=='education' and bool(re.fullmatch(r'(start|end) date (month|year)\s*\*?',label,re.I))
        if education_date:key='college_start' if label.lower().startswith('start') else 'graduation'
        # A posting's distinctive first company word can establish a short-form
        # employer mention. Abstain if that same word appears in prior employers.
        employer_named,prior_match=_employer_scope(store,host,label,context)
        if employer_named and not prior_match:
            if re.search(r'(?:previously|ever|before).{0,20}(?:work|employ)|(?:work|employ).{0,30}(?:previously|before)',label,re.I) and store.facts().get('worked_outside_resume',{}).get('value')=='No':key='worked_outside_resume'
            elif re.search(r'(?:know anyone|family|spouse|partner|relative|related to|referred (?:to this role |for this role )?by).{0,70}(?:company|work|employ)|(?:know anyone|personal contacts)',label,re.I) and store.facts().get('contacts_outside_resume',{}).get('value')=='No':key='contacts_outside_resume'
        if (not key and re.search(r'(?:authorized|eligible) to work',label,re.I)
                and GENERIC_WORK_COUNTRY.search(label) and _us_location(work_country_location(context))
                and _compatible_field('work_authorized_us',field,context)):key='work_authorized_us'
        binding=contextual_binding(store,host,field,context)
        if binding and not education_date:
            # Exact semantic rules supersede older model-selected bindings.
            key=key or (binding['fact_key'] if _compatible_field(binding['fact_key'],field,context) else None)
            if not key:template=next((t for t in store.templates() if t['id']==binding['template_id']),None)
        if key=='country' and not store.facts().get('country'):
            location=store.facts().get('location',{})
            # Current residence is geography, not citizenship. A North American +1 alone is insufficient.
            if re.fullmatch(r'Berkeley,?\s+(?:CA|California)(?:,?\s+United States)?',location.get('value',''),re.I):
                derived={'value':'United States','provenance':{'residence_location_revision':location['revision']}}
        status_answer=_work_status_answer(store,field,key,context)
        if status_answer:derived=status_answer;key=None
        cat=category(label) if field.get('type') in ('text','textarea') and not options else None
        is_writing=not options and field.get('type') in ('text','textarea') and (cat or re.search(r'example|describe|tell us|why|what.*(?:interests|excites)|share.*(?:work|project)',label,re.I))
        if is_writing and not key and provider and store.settings()['tailored_writing']:
            choices=_approved_sentences(store,context)
            draft=provider.draft_answer(label,choices,context,field.get('maxlength',-1)) if choices else {}
            draft_rejection=draft.get('rejected')
            ids=draft.get('sentence_ids',[]);by_id={x['id']:x for x in choices}
            if draft.get('answer') and ids and all(x in by_id for x in ids):
                parts=[{k:v for k,v in by_id[x].items() if k!='id'} for x in ids]
                draft['answer']=re.sub(r'[\r\n]+', ' ', draft['answer']).strip() if context['single_line'] else draft['answer']
                writing={'value':draft['answer'],'provenance':{'tailored':True,'sample_parts':parts,'text_hash':digest(draft['answer']),'context_hash':writing_context_hash(store,context)}}
                _validate_writing(store,writing)
                if not _fits_writing_limits(writing['value'],field,context):raise Blocked('answer_too_long',label)
                store.save_writing_answer(host,label,options,writing);store.resolve_known_question(host,label,options,field=field,context=context)
                return {'field':field,**writing}
        if not template and cat:
            choices=[t for t in store.templates() if t['category']==cat]
            selected=choices[0]['id'] if len(choices)==1 else provider.choose_answer(label,choices) if choices and provider else None
            template=next((t for t in choices if t['id']==selected),None)
        if graduation_confirmation:
            key=None;template=None;derived=graduation_confirmation
        if not key and not derived:derived=_context_preference(store,label,options,context,field)
        if not key and not template:derived=derived or _role_answer(label,options,context,store) or _resume_internship(store,label) or _discovery_answer(label,options,context) or _local_campus_answer(store,label) or _chronological_academic_year(store,label) or _current_enrollment(store,label,options) or _us_citizen_or_resident(store,label,options) or _export_citizenship_answer(store,label,options) or _cohort_answer(store,field,context)
        if key and key not in store.facts() and provider and store.settings()['tailored_writing'] and key in {'school','degree','major','skills','location','city','state','alternate_email'}:
            key=None  # Approved context may state an ordinary fact not separately entered.
        match_abstention=_abstention_key(store,'match_field',context) if not key and not template and not derived and provider and field.get('required') else None
        if match_abstention and not _abstained(store,match_abstention):
            from .config import FACTS
            facts={k:{'label':FACTS[k],'value':v['value']} for k,v in store.facts().items() if k not in {'worked_outside_resume','contacts_outside_resume'}}
            if not re.search(r'summer\s*2027',context.get('title',''),re.I):facts.pop('summer_2027_relocate',None)
            matched=provider.match_field(field,facts,store.templates(),context)
            candidate_keys={k for k in facts if _compatible_field(k,field,context)}
            proposed=matched.get('fact_key')
            rejected=not proposed or proposed not in candidate_keys or bool(matched.get('template_id'))
            if (rejected and candidate_keys and not is_writing and store.settings()['model_escalation']
                    and not re.search(r'consent|agree|acknowledge|certify|assessment|work sample',label,re.I)
                    and hasattr(provider,'reconsider_field')):
                matched=provider.reconsider_field(field,{k:v for k,v in facts.items() if k in candidate_keys},[],context)
            proposed_key=matched.get('fact_key');tid=matched.get('template_id')
            if bool(proposed_key) != bool(tid):
                if proposed_key in facts:
                    # These two concepts cannot be conflated even by semantic matching.
                    if _compatible_field(proposed_key,field,context):
                        key=proposed_key;pending_binding={'key':key}
                elif tid:
                    template=next((t for t in store.templates() if t['id']==tid),None)
                    if template and (not is_writing or category(label) not in (None, template['category'])):template=None
                    if template:pending_binding={'template_id':tid}
            if not pending_binding:_abstain(store,match_abstention,'match_field',label)
        if template and (_foreign_targets(store,template,context) or not _fits_writing_limits(template['body'],field,context)):template=None
        if not template and not key and not derived and provider and is_writing:
            choices=_approved_sentences(store,context)
            ids=provider.choose_sentences(label,choices,context,field.get('maxlength',-1)) if choices else []
            by_id={x['id']:x for x in choices}
            if ids and len(ids)<=4 and len(ids)==len(set(ids)) and all(x in by_id for x in ids):
                parts=[{k:v for k,v in by_id[x].items() if k!='id'} for x in ids]
                writing={'value':' '.join(p['text'] for p in parts),'provenance':{'sample_parts':parts}}
                _validate_writing(store,writing)
                if not _fits_writing_limits(writing['value'],field,context):raise Blocked('answer_too_long',label)
                store.save_writing_answer(host,label,options,writing);store.resolve_known_question(host,label,options,field=field,context=context)
                return {'field':field,**writing}
        # Ordinary structured fields need the factual source library too. Legal,
        # consent, demographic and assessment questions remain explicit approvals.
        sensitive=r'consent|agree|acknowledge|certif|arbitrat|privacy|authoriz|citizen|sponsor|visa|hispanic|latino|ethnic|race|gender|pronoun|veteran|military|disabil|criminal|government|procurement|proficien|assessment|work sample|18 years|\bage\b|full.time.*availab|availab.*full.time'
        context_abstention=(_abstention_key(store,'context_answer',context)
            if (not key and not template and not derived and provider and field.get('required') and not is_writing
                and store.settings()['tailored_writing'] and hasattr(provider,'context_answer')
                and field.get('type') in {'text','textarea','number','select','radio','combobox','yesno','checkbox-group'}
                and not re.search(sensitive,label,re.I)) else None)
        if context_abstention and not _abstained(store,context_abstention):
            from .config import FACTS
            choices=_approved_sentences(store,{**context,'description':label+' '+context.get('description','')})
            choices += [{'id':'fact:'+k,'text':v['value'],'fact_key':k,'revision':v['revision']}
                        for k,v in store.facts().items() if k not in {'worked_outside_resume','contacts_outside_resume'}]
            draft=provider.context_answer(field,choices,{k:{'label':FACTS[k],'value':v['value']} for k,v in store.facts().items()},context) if choices else {}
            draft_rejection=draft.get('rejected') or draft_rejection
            by_id={x['id']:x for x in choices};ids=draft.get('sentence_ids',[])
            if (isinstance(draft.get('answer'),str) and draft['answer'] and ids and len(ids)==len(set(ids))
                    and all(x in by_id for x in ids) and (not options or _listed_answer(field,draft['answer']))):
                parts=[{k:v for k,v in by_id[x].items() if k!='id'} for x in ids]
                writing={'value':draft['answer'],'provenance':{'tailored':True,'context_answer':True,'sample_parts':parts,
                         'text_hash':digest(draft['answer']),'context_hash':writing_context_hash(store,context),'field_hash':digest(context['field_context']),'facts_hash':digest(store.facts())}}
                if field_key(label)=='alternate_email':
                    from .config import validate_fact
                    try:validate_fact('alternate_email',writing['value'])
                    except ValueError:raise Blocked('writing_unsupported','The alternate email is not a valid address') from None
                    if writing['value'].casefold()==store.facts().get('email',{}).get('value','').casefold():raise Blocked('missing_fact','Confirm an alternate email different from the primary address')
                _validate_writing(store,writing);validate_numeric(writing['value'],field)
                if not _fits_writing_limits(writing['value'],field,context):raise Blocked('answer_too_long',label)
                store.save_writing_answer(host,label,options,writing);store.resolve_known_question(host,label,options,field=field,context=context)
                return {'field':field,**writing}
            if not draft_rejection:_abstain(store,context_abstention,'context_answer',label)
        if template:
            value=re.sub(r'[\r\n]+',' ',template['body']).strip() if field.get('type')=='text' else template['body'];provenance={'template_id':template['id'],'revision':template['revision']}
        elif derived:
            value=derived['value'];provenance=derived['provenance']
        else:
            fact=store.facts().get(key)
            if not fact:
                declined=_unanswered_demographic_decline(store,field)
                if declined:
                    store.resolve_known_question(host,label,options,field=field,context=context)
                    return declined
                # Every grounded draft failed review: a model outcome, not a missing personal fact.
                if field.get('required') and draft_rejection:raise Blocked('writing_unsupported',draft_rejection)
                if field.get('required'):raise Blocked('missing_fact',label)
                store.resolve_known_question(host,label,options,field=field,context=context)
                return None
            value=fact['value']
            if key=='salary' and re.search(r'\bhourly (?:rate|pay)\b',label,re.I):
                hourly=re.fullmatch(r'\$?(\d+(?:\.\d+)?)\s*(?:/\s*(?:hr?|hours?)|per hour|hourly)',value,re.I)
                if not hourly:raise Blocked('missing_fact','Confirm an hourly pay requirement; an annual amount cannot be converted without a confirmed schedule')
                value=hourly[1]
            value=present(key,value,field)
            provenance={'fact_key':key,'revision':fact['revision']}
    if field.get('type')=='checkbox-group' and options and selections(value,options):
        # Exact choices for a question that states how many it wants.
        if not _listed_answer(field,value):raise Blocked('option_mismatch',label)
    elif field.get('type') in ('radio','select','combobox','checkbox','checkbox-group','yesno') and options:
        try:value=_option_value(key,value,options,context)
        except Blocked:
            mapped=_mapped_option(store,key,field,provider) if key and provenance.get('fact_key')==key else None
            if mapped is None:
                explicit=_explicit_context_answer(store,host,field,key,provider,context)
                if explicit:return explicit
                raise Blocked('option_mismatch',label)
            value=mapped;provenance={**provenance,'option_mapping':True}
    if field.get('date_format'):value=present(None,value,field)
    if field.get('maxlength',-1)>0 and len(value)>field['maxlength']:raise Blocked('answer_too_long',label)
    validate_numeric(value,field)
    if pending_binding:save_binding(store,host,field,context,**pending_binding)
    store.resolve_known_question(host,label,options,field=field,context=context)
    return {'field':field,'value':value,'provenance':provenance}


def validate_package(store, job, package):
    if package.get("job_id")!=job["id"] or package.get("url")!=job["url"]:
        raise Blocked("package_destination_mismatch")
    if not isinstance(package.get("answers"),list): raise Blocked("invalid_package")
    from .materials import writing_context_hash
    facts=store.facts()
    writing_context={"form_questions":[f["label"] for step in package.get("steps",[]) for f in step.get("fields",[])]}
    for index,answer in enumerate(package["answers"]):
        field=answer["field"]
        prov=answer.get("provenance",{})
        if not _fits_writing_limits(answer["value"],field,writing_context):raise Blocked("answer_too_long",field["label"])
        if 'sample_parts' in prov:
            _validate_writing(store,answer)
            if prov.get('tailored') and prov.get('context_hash')!=writing_context_hash(store,job):raise Blocked('stale_writing_context')
            if prov.get('context_answer') and prov.get('field_hash')!=digest(field_context(job.get('answer_scope',job['host']),field,job)):raise Blocked('stale_writing_context')
            validate_numeric(answer['value'],field)
            cached=store.writing_answer(job.get('answer_scope',job['host']),field['label'],field.get('options',[]))
            if cached!={'value':answer['value'],'provenance':prov}:raise Blocked('unsupported_or_stale_sample')
            continue
        if "template_id" in prov:
            template=next((x for x in store.templates() if x["id"]==prov["template_id"]),None)
            if not template or _foreign_targets(store,template,job) or (template["category"]!=category(field["label"]) and not ((contextual_binding(store,job.get("answer_scope",job["host"]),field,job) or {}).get("template_id")==template["id"])) or template["revision"]!=prov["revision"] or (re.sub(r"[\r\n]+"," ",template["body"]).strip() if field.get("type")=="text" else template["body"])!=answer["value"]:
                raise Blocked("unsupported_or_stale_template")
            continue
        expected=resolve(store,job.get("answer_scope",job["host"]),field,context={**job,'previous_answers':package['answers'][:index]})
        if expected != answer:
            raise Blocked("unsupported_or_stale_answer",field["label"])
    for doc in package.get("documents",[]):
        if doc.get('artifact_id'):
            from .application_artifacts import validate
            if doc.get('kind') != 'supplemental_response' or not validate(store,job,doc).startswith(b'%PDF-'):
                raise Blocked('artifact_changed')
            continue
        if doc.get("generated"):
            from .letters import validate_generated_document
            validate_generated_document(store,job,doc)
            continue
        actual=store.db.execute("SELECT * FROM documents WHERE kind=?",(doc["kind"],)).fetchone()
        if not actual or doc["hash"]!=actual["hash"] or doc.get("filename")!=actual["filename"]:
            raise Blocked("document_changed")
    if not any(d["kind"]=="resume" for d in package.get("documents",[])):
        raise Blocked("resume_not_in_package")
    for step in package.get("steps",[]):
        for field in step.get("fields",[]):
            if not field.get("required"):continue
            entries=package["documents"] if field["type"]=="file" else package["answers"]
            if not any(x["field"]==field for x in entries):raise Blocked("required_answer_missing",field["label"])
    if package.get("facts_hash")!=digest(facts):
        raise Blocked("facts_changed")
    if package.get('connection'):
        from .platform_browser import validate_connection_package
        validate_connection_package(store,job,package)
