"""Offline, source-bound history proposals. Only applicant review creates confirmed entries."""
from __future__ import annotations

import json
import re

from .onboarding import selected_resume_text, _selected_resume_bytes
from .util import digest
from .workday_history import _validate

SECTIONS = {'experience':'employment','work experience':'employment','professional experience':'employment',
            'employment':'employment','education':'education'}
STOPS = {'projects','skills','technical skills','publications','awards','leadership','certifications','interests'}
MONTHS = {name:index for index,names in enumerate((
    ('jan','january'),('feb','february'),('mar','march'),('apr','april'),('may',),('jun','june'),
    ('jul','july'),('aug','august'),('sep','sept','september'),('oct','october'),('nov','november'),('dec','december')),1) for name in names}
DATE = r'(?:[A-Za-z]{3,9}\.?\s+20\d{2}|20\d{2}-(?:0[1-9]|1[0-2]))'
RANGE = re.compile(r'('+DATE+r')\s*(?:[-–—]|\bto\b)\s*('+DATE+r'|present|current)\b',re.I)


def _month(value):
    if re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])',value):return value
    match=re.fullmatch(r'([A-Za-z]+)\.?\s+(20\d{2})',value)
    if match and match[1].casefold() in MONTHS:return f'{match[2]}-{MONTHS[match[1].casefold()]:02}'
    return None


def _values(lines,kind):
    values={};clean=[line.strip() for line in lines if line.strip()]
    if not clean:return values
    if kind=='employment' and sum('|' in line and not re.match(r'^[•*-]\s',line) for line in clean)>1:
        return {}  # Never associate two apparent employer headers with one accomplishment list.
    header=[]
    for line in clean[:3]:
        match=RANGE.search(line)
        if match:
            start,end=_month(match[1]),_month(match[2])
            if start:values['start']=start
            if match[2].casefold() in {'present','current'}:values['current']=True
            elif end:values['expected_end' if kind=='education' and 'expected' in line.casefold() else 'end']=end
        expected=re.search(r'\bexpected\s+('+DATE+r')',line,re.I)
        if kind=='education' and expected and _month(expected[1]):values['expected_end']=_month(expected[1]);values.pop('end',None)
        text=RANGE.sub('',line)
        text=re.sub(r'\bexpected\s+'+DATE,'',text,flags=re.I).strip(' |•–—-')
        if text and not re.match(r'^[•*-]\s',line):header.append(text)
    parts=[part.strip() for part in re.split(r'\s*[|•]\s*',header[0]) if part.strip()] if header else []
    if kind=='employment':
        if len(parts)>=2:values.update(organization=parts[0],title=parts[1])
        elif len(header)>=2:values.update(organization=header[0],title=header[1])
        body=[line for line in clean if re.match(r'^[•*-]\s',line)]
        if body and len('\n'.join(body))<=8000:values['description']='\n'.join(body)
    else:
        if parts:values['institution']=parts[0]
        if len(parts)>=2:values['degree']=parts[1]
        if len(parts)>=3:values['field_of_study']=parts[2]
        gpa=re.search(r'\bGPA:\s*([0-9.]+(?:\s*/\s*[0-9.]+)?)','\n'.join(clean),re.I)
        if gpa:values['gpa']=gpa[1]
    return values


def candidates(lines):
    sections=[];active=None
    for index,line in enumerate(lines):
        heading=line.strip().casefold().rstrip(':')
        if heading in SECTIONS or heading in STOPS:
            if active:sections.append((*active,index))
            active=(SECTIONS[heading],index+1) if heading in SECTIONS else None
    if active:sections.append((*active,len(lines)))
    result=[]
    for kind,start,end in sections:
        anchors=[index for index in range(start,end) if not re.match(r'^\s*[•*-]\s',lines[index]) and
                 (RANGE.search(lines[index]) or kind=='employment' and '|' in lines[index] or
                  kind=='education' and re.search(r'\bexpected\s+'+DATE,lines[index],re.I))]
        starts=[]
        for index in anchors:
            before=lines[index].strip()
            if RANGE.fullmatch(before) or re.fullmatch(r'expected\s+'+DATE,before,re.I):
                for _ in range(2):
                    prior=lines[index-1].strip() if index>start else ''
                    if not prior or re.match(r'^[•*-]\s',prior) or RANGE.search(prior):break
                    index-=1
                    if '|' in prior or '•' in prior:break
            if not starts or index>starts[-1]:starts.append(index)
        if not starts:starts=[start]
        for index,first in enumerate(starts):
            last=starts[index+1] if index+1<len(starts) else end
            while first<last and not lines[first].strip():first+=1
            while last>first and not lines[last-1].strip():last-=1
            if first==last:continue
            # Long/ambiguous sections remain available through manual line selection.
            last=min(last,first+60)
            if len('\n'.join(lines[first:last]))>12000:continue
            result.append({'kind':kind,'start_line':first+1,'end_line':last,
                           'values':_values(lines[first:last],kind) if anchors or kind=='education' else {}})
    return result[:20]


def _selection(source,start,end):
    lines=source['text'].splitlines()
    if type(start) is not int or type(end) is not int or not 1<=start<=end<=len(lines) or end-start>=60:
        raise ValueError('Choose between 1 and 60 existing resume lines')
    excerpt='\n'.join(lines[start-1:end])
    if not excerpt.strip() or len(excerpt)>12000:raise ValueError('Choose a nonempty excerpt up to 12,000 characters')
    return excerpt


def preview(store):
    source=selected_resume_text(store);lines=source['text'].splitlines()
    proposals=candidates(lines)
    for proposal in proposals:
        proposal['excerpt']=_selection(source,proposal['start_line'],proposal['end_line'])
        proposal['excerpt_hash']=digest(proposal['excerpt'])
    return {'resume_hash':source['hash'],'lines':lines,'candidates':proposals,
            'history':[{key:row[key] for key in ('id','kind','values','confirmed','revision')}
                       for row in store.history_entries(confirmed_only=False)]}


def suggest(store,data):
    if not isinstance(data,dict) or set(data)-{'resume_hash','start_line','end_line','kind'} or data.get('kind') not in {'employment','education'}:
        raise ValueError('Choose an education or employment excerpt')
    source=selected_resume_text(store)
    if data.get('resume_hash')!=source['hash']:raise ValueError('Your selected resume changed. Reload its excerpts before importing.')
    excerpt=_selection(source,data.get('start_line'),data.get('end_line'))
    return {'excerpt':excerpt,'excerpt_hash':digest(excerpt),'values':_values(excerpt.splitlines(),data['kind'])}


def confirm(store,data):
    allowed={'resume_hash','start_line','end_line','kind','values','confirmed','excerpt_hash'}
    if not isinstance(data,dict) or set(data)-allowed or data.get('confirmed') is not True:
        raise ValueError('Review and explicitly confirm this history entry before importing')
    kind=data.get('kind')
    if not isinstance(kind,str):raise ValueError('Choose education or employment')
    values=_validate(kind,data.get('values'))
    source=selected_resume_text(store)
    if data.get('resume_hash')!=source['hash']:raise ValueError('Your selected resume changed. Reload its excerpts before importing.')
    start,end=data.get('start_line'),data.get('end_line')
    excerpt=_selection(source,start,end)
    if data.get('excerpt_hash')!=digest(excerpt):raise ValueError('The selected excerpt changed. Propose and review these lines again.')
    identity=digest([source['hash'],start,end,kind])
    with store.transaction():
        selected,_=_selected_resume_bytes(store)
        if selected['hash']!=source['hash']:raise ValueError('Your selected resume changed. Reload its excerpts before importing.')
        previous=store.db.execute("SELECT detail FROM events WHERE kind='history_import_confirmed' AND subject=? ORDER BY seq DESC LIMIT 1",(identity,)).fetchone()
        entries=store.history_entries(confirmed_only=False)
        if previous:
            saved=json.loads(previous['detail'])
            entry=next((row for row in entries if row['id']==saved['entry_id']),None)
            if not entry or not entry['confirmed'] or entry['revision']!=saved['entry_revision'] or entry['values']!=values:
                raise ValueError('This excerpt was already imported and its entry changed or was withdrawn. Edit the existing history entry instead.')
            return {'entry_id':entry['id'],'existing':True}
        # Identical manual/imported entries are reused without changing their source or revision.
        existing=next((row for row in entries if row['confirmed'] and row['kind']==kind and row['values']==values),None)
        entry=existing or store.put_history_entry(kind,values,
            source=f'Applicant confirmed resume {source["hash"]}, lines {start}-{end}',confirmed=True)
        store.event('history_import_confirmed',identity,{'entry_id':entry['id'],'entry_revision':entry['revision'],
            'resume_hash':source['hash'],'start_line':start,'end_line':end,'excerpt':excerpt,'kind':kind,'values':values})
        return {'entry_id':entry['id'],'existing':bool(existing)}
