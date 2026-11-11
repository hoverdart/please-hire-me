"""Plan unresolved page questions together using reviewed, revision-bound sources."""
from __future__ import annotations

import json
from .util import Blocked, digest
from .field_context import field_context, validate_numeric

VERSION = 1
RECOVERABLE = {'missing_fact','mapping_review','option_mismatch','writing_unsupported',
               'unsupported_or_stale_sample','stale_writing_context','writing_upgrade_needed'}


def cache_key(host, field, context):
    return digest(['page_answer',VERSION,field_context(host,field,context)])


def cached(store, host, field, context):
    row=store.db.execute('SELECT body,provenance FROM writing_answers WHERE id=?',
                         (cache_key(host,field,context),)).fetchone()
    return {'value':row['body'],'provenance':json.loads(row['provenance'])} if row else None


def plan(store, host, fields, known, pending, provider, context):
    """Return grounded answers plus unresolved fields, without touching the page."""
    from .answers import (REFUSE, STUDENT_QUESTION, field_key, _approved_sentences,
        _listed_answer, _fits_writing_limits, _sentence_cap, _validate_writing)
    from .materials import writing_context, writing_context_hash
    from .config import FACTS
    if not pending or not store.settings()['tailored_writing'] or not hasattr(provider,'plan_form'):
        return [],pending
    candidates=[]
    for field,reason in pending:
        if (reason not in RECOVERABLE or field.get('type') not in {'text','textarea','number','select','radio','combobox','yesno','checkbox','checkbox-group'}
                or REFUSE.search(field['label']) or STUDENT_QUESTION.fullmatch(field['label'].strip())
                or field_key(field['label'])=='high_school_graduation_year'
                or field.get('section_entry',0)>0):continue
        candidates.append(field)
    if not candidates:return [],pending
    source_hash=writing_context_hash(store,context);facts=store.facts();facts_hash=digest(facts)
    sources=_approved_sentences(store,{**context,'field_context':{'label':' '.join(f['label'] for f in candidates)}})
    present={s['id'] for s in sources}
    sources += [{'id':'fact:'+key,'text':fact['value'],'fact_key':key,'fact_label':FACTS[key],
                 'revision':fact['revision']} for key,fact in facts.items() if 'fact:'+key not in present]
    if not sources:return [],pending
    # IDs belong to this exact observed page; the model never supplies selectors.
    targets=[{'field_id':str(fields.index(f)),'field':f} for f in candidates]
    model_context={**context,**writing_context(store),'known_answers':[
        {'question':a['field']['label'],'answer':a['value']} for a in known],
        'all_questions':fields,'confirmed_facts':{k:{'label':FACTS[k],'value':v['value']} for k,v in facts.items()}}
    result=provider.plan_form(targets,sources,model_context)
    if source_hash!=writing_context_hash(store,context) or facts_hash!=digest(store.facts()):
        raise Blocked('stale_writing_context','Applicant context changed during page planning')
    by_id={s['id']:s for s in sources};by_field={x['field_id']:x['field'] for x in targets}
    items=result.get('answers',[]);identifiers=[x.get('field_id') for x in items]
    if len(identifiers)!=len(set(identifiers)) or any(i not in by_field for i in identifiers):
        raise Blocked('provider_invalid_output','Page plan contains unknown or repeated field IDs')
    resolved=[]
    for item in items:
        field=by_field[item['field_id']];value=item.get('answer');ids=item.get('sentence_ids',[])
        if (item.get('classification') not in {'known','inferred'} or not isinstance(value,str) or not value.strip()
                or not ids or len(ids)!=len(set(ids)) or any(i not in by_id for i in ids)):continue
        if field.get('options') and not _listed_answer(field,value):continue
        if field.get('type')=='checkbox' and value not in {'Yes','No'}:continue
        prior=[a for a in known+resolved if fields.index(a['field'])<fields.index(field)]
        bound_context={**context,'previous_answers':prior}
        specific={**bound_context,'field_context':field_context(host,field,bound_context),
                  'single_line':field.get('type')=='text','max_sentences':_sentence_cap(field,context)}
        if specific['single_line'] and '\n' in value:continue
        if not _fits_writing_limits(value,field,specific):continue
        try:validate_numeric(value,field)
        except Blocked:continue
        parts=[{k:v for k,v in by_id[i].items() if k not in {'id','fact_label'}} for i in ids]
        answer={'value':value,'provenance':{'tailored':True,'context_answer':True,'page_plan':VERSION,
            'classification':item['classification'],'sample_parts':parts,'text_hash':digest(value),
            'context_hash':source_hash,'field_hash':digest(specific['field_context']),'facts_hash':facts_hash}}
        _validate_writing(store,answer)
        store.db.execute('INSERT OR REPLACE INTO writing_answers VALUES(?,?,?)',
                         (cache_key(host,field,bound_context),value,json.dumps(answer['provenance'])))
        store.resolve_known_question(host,field['label'],field.get('options',[]),field=field,context=bound_context)
        resolved.append({'field':field,**answer})
    remaining=[(f,r) for f,r in pending if not any(a['field']==f for a in resolved)]
    return resolved,remaining
