from __future__ import annotations
import json


def import_legacy(store,path):
    from .config import validate_fact
    mapping={'name_combined':'full_name','first_name':'first_name','last_name':'last_name','email':'email','phone':'phone',
       'location':'location','school':'school','major':'major','gpa':'gpa','linkedin':'linkedin','github':'github','website':'website'}
    p=path/'config/profile.json';values={}
    if p.is_file():
        old=json.loads(p.read_text())
        for k,v in old.items():
            if k in mapping and isinstance(v,str):
                try:values[mapping[k]]=validate_fact(mapping[k],v)
                except ValueError:pass
        store.put_facts(values,'legacy:unverified',False)
    records=[]
    for folder in ('applications','logs','screenshots'):
        directory=path/folder
        if not directory.is_dir():continue
        for p in directory.iterdir():
            if p.is_symlink() or not p.is_file():continue
            if p.suffix=='.md':records.append({'path':str(p),'text':p.read_text()[:500000]})
            elif folder=='screenshots':records.append({'path':str(p),'text':p.stem})
    # Historical formats are inconsistent: never pretend uncertain Markdown is a reliable count.
    # Freeze submission until user confirms the imported history in the dashboard.
    if records:
        from .util import atomic_json
        atomic_json(store.root/'config/legacy-history.json',records)
        store.update_settings({'live_enabled':False,'onboarding_complete':False})
        store.ask('legacy-history','local','Which companies already received applications? Add all to the company block list before completing setup.',[],'legacy_history_review')
        store.event('legacy_history_imported','history',{'records':len(records)})
    return {'candidate_facts':len(values),'history_records':len(records),'next':'Confirm facts and historical company blocks in the dashboard'}
