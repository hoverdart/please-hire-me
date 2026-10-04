"""Conservative month/year graduation comparisons; never invent date precision."""
import re
from .util import Blocked

MONTHS={name.lower():i for i,name in enumerate(('January','February','March','April','May','June','July','August','September','October','November','December'),1)}
MONTHS.update({k[:3]:v for k,v in list(MONTHS.items())})
DATE=r'(?:[A-Za-z]+\.?\s+)?(?:20\d{2})'

def endpoint(text):
    match=re.fullmatch(r'(?:(\w+)\.?\s+)?(20\d{2})',text.strip())
    if not match:return None
    month=MONTHS.get((match[1] or '').lower())
    if match[1] and not month:return None
    year=int(match[2])*12
    return (year+(month or 1)-1,year+(month or 12)-1)

def window(text):
    """Return inclusive month bounds or None for unsupported/ambiguous wording."""
    if not re.search(r'graduat',text,re.I):return None
    text=re.sub(r'\s+',' ',text)
    match=re.search(r'graduat\w*.{0,60}?(?:between|from)\s+('+DATE+r')\s*(?:and|to|[-–])\s*('+DATE+r')',text,re.I)
    if match:
        first,last=endpoint(match[1]),endpoint(match[2])
        return (first[0],last[1]) if first and last and first[0]<=last[1] else None
    match=re.search(r'graduat\w*.{0,60}?\b(20\d{2})\s+or\s+(later|earlier)\b',text,re.I)
    if match:
        point=endpoint(match[1])
        return (point[0],None) if match[2].lower()=='later' else (None,point[1])
    match=re.search(r'graduat\w*.{0,40}?\b(before|after|by|on or before|on or after)\s+('+DATE+r')',text,re.I)
    if not match:return None
    point=endpoint(match[2])
    if not point:return None
    direction=match[1].lower()
    return (None,point[0]-1) if direction=='before' else (point[1]+1,None) if direction=='after' else (point[0],None) if direction=='on or after' else (None,point[1])

def matches(value,bounds):
    if not re.fullmatch(r'20\d{2}(?:-(?:0[1-9]|1[0-2]))?',value):raise Blocked('graduation_window_review','Confirm a graduation date with sufficient precision')
    point=endpoint(value[:4]) if len(value)==4 else (int(value[:4])*12+int(value[5:])-1,)*2
    lo,hi=bounds
    outcomes=[(lo is None or p>=lo) and (hi is None or p<=hi) for p in point]
    if outcomes[0]!=outcomes[1]:raise Blocked('graduation_window_review','A month is required to compare this graduation cutoff')
    return outcomes[0]

def required(text,value):
    for sentence in re.split(r'[\n.!?]+',text):
        if re.search(r'graduation date.{0,60}(?:indicated|included|listed|stated).{0,30}(?:resume|cv)',sentence,re.I):
            continue # Document-content requirement, not a graduation cutoff.
        if not re.search(r'(?:must|required|eligible|between|before|after|by).{0,60}graduat|graduat.{0,60}(?:must|between|before|after|by)',sentence,re.I):continue
        bounds=window(sentence)
        if bounds is None:raise Blocked('graduation_window_review','An unparsed graduation requirement needs review')
        if not matches(value,bounds):raise Blocked('graduation_mismatch')
