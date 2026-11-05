"""Local ranking from confirmed sources. Ranking never grants application authority."""
from __future__ import annotations

import re


# Domain matches need both posting text and applicant-confirmed experience.
DOMAINS = {
    'backend': r'\bback[- ]?end\b|\bapi\b|\bserver[- ]side\b',
    'production systems': r'\bproduction\b|\bdistributed systems\b|\breliability\b|\binfrastructure\b',
    'AI engineering': r'\bmachine learning\b|\bllms?\b|\bartificial intelligence\b|\bai\b',
    'manufacturing software': r'\bmanufactur\w*\b|\bshop floor\b|\bindustrial automation\b',
    'research': r'\bresearch\b|\bexperiments?\b',
}


def relevance(job, score, matched_skills, history):
    posting=job.get('title','')+'\n'+job.get('description','')
    matches=[]
    for entry in history:
        if entry['kind']!='employment':continue
        values=entry['values']
        source=values.get('title','')+'\n'+values.get('description','')
        domains=[name for name,pattern in DOMAINS.items()
                 if re.search(pattern,posting,re.I) and re.search(pattern,source,re.I)]
        if domains:matches.append({'history_id':entry['id'],'revision':entry['revision'],'domains':domains})
    bonus=min(10,3*len({domain for match in matches for domain in match['domains']}))
    return min(100,score+bonus),{'matched_skills':matched_skills,'experience':matches}
