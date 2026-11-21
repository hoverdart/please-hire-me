"""Plan unresolved page questions together using reviewed, revision-bound sources."""
from __future__ import annotations

import json
from .util import Blocked, digest, now
from .field_context import field_context, validate_numeric

VERSION = 2
RECOVERABLE = {'missing_fact','mapping_review','option_mismatch','writing_unsupported',
               'unsupported_or_stale_sample','stale_writing_context','writing_upgrade_needed',
               'required_answer_missing','answer_too_long','numeric_answer_needed'}
PROVIDER_FAILURES = {'provider_timeout','provider_error','provider_invalid_output',
                     'provider_unavailable','provider_cli_incompatible'}


def cache_key(host, field, context):
    return digest(['page_answer',VERSION,field_context(host,field,context)])


def cached(store, host, field, context):
    identity=field_context(host,field,context)
    # Previously prepared packages must still be able to validate their exact cache entry.
    row=store.db.execute('SELECT body,provenance FROM writing_answers WHERE id IN (?,?) ORDER BY id=? DESC',
                         (cache_key(host,field,context),digest(['page_answer',1,identity]),cache_key(host,field,context))).fetchone()
    return {'value':row['body'],'provenance':json.loads(row['provenance'])} if row else None


def plan(store, host, fields, known, pending, provider, context, *, previous=()):
    """Return grounded answers plus unresolved fields, without touching the page."""
    from .answers import (REFUSE, STUDENT_QUESTION, field_key, _approved_sentences,
        _listed_answer, _fits_writing_limits, _sentence_cap, _validate_writing)
    from .materials import writing_context, writing_context_hash
    from .config import FACTS
    settings=store.settings()
    if not pending or not settings['tailored_writing'] or not hasattr(provider,'plan_form'):
        return [],pending
    candidates=[]
    for field,reason in pending:
        if (reason not in RECOVERABLE or field.get('type') not in {'text','textarea','number','url','select','radio','combobox','yesno','checkbox','checkbox-group'}
                or REFUSE.search(field['label']) or STUDENT_QUESTION.fullmatch(field['label'].strip())
                or field_key(field['label'])=='high_school_graduation_year'
                or field.get('history_entry_id') or field.get('section') in {'employment','education'}
                or field.get('date_format') or field.get('history_date_part')
                or field.get('type') in {'select','radio','combobox','checkbox-group'} and not field.get('options')):continue
        candidates.append(field)
    if not candidates:return [],pending
    source_hash=writing_context_hash(store,context);facts=store.facts();facts_hash=digest(facts)
    sources=_approved_sentences(store,{**context,'field_context':{'label':' '.join(f['label'] for f in candidates)}})
    present={s['id'] for s in sources}
    sources += [{'id':'fact:'+key,'text':fact['value'],'fact_key':key,'fact_label':FACTS[key],
                 'revision':fact['revision']} for key,fact in facts.items() if 'fact:'+key not in present]
    history=store.history_entries()
    history_hash=digest(history)
    for entry in history:
        summary={key:value for key,value in entry['values'].items() if key!='description'}
        sources.append({'id':'history:'+entry['id'],'text':json.dumps(summary,ensure_ascii=False),
                        'history_source_id':entry['id'],'revision':entry['revision']})
    if not sources:return [],pending
    # IDs belong to this exact observed page; the model never supplies selectors.
    targets=[{'field_id':str(fields.index(f)),'field':f} for f in candidates]
    model_context={**context,**writing_context(store),'known_answers':[
        {'question':a['field']['label'],'answer':a['value']} for a in [*previous,*known]],
        'all_questions':fields,'confirmed_facts':{k:{'label':FACTS[k],'value':v['value']} for k,v in facts.items()}}
    model_identity=[settings[key] for key in ('provider','provider_model','model_effort','model_escalation')]
    attempt=digest(['page_plan_abstention',VERSION,context.get('id'),source_hash,history_hash,model_identity,
                    [field_context(host,f,context) for f in fields],
                    [t['field_id'] for t in targets],model_context['known_answers'],sources])
    if store.db.execute('SELECT 1 FROM model_abstentions WHERE id=?',(attempt,)).fetchone():return [],pending
    result=provider.plan_form(targets,sources,model_context)
    if (source_hash!=writing_context_hash(store,context) or facts_hash!=digest(store.facts())
            or history_hash!=digest(store.history_entries())):
        raise Blocked('stale_writing_context','Applicant context changed during page planning')
    by_id={s['id']:s for s in sources};by_field={x['field_id']:x['field'] for x in targets}
    items=result.get('answers',[]);identifiers=[x.get('field_id') for x in items]
    if len(identifiers)!=len(set(identifiers)) or any(i not in by_field for i in identifiers):
        raise Blocked('provider_invalid_output','Page plan contains unknown or repeated field IDs')
    resolved=[]
    for item in sorted(items,key=lambda item:int(item['field_id'])):
        field=by_field[item['field_id']];value=item.get('answer');ids=item.get('sentence_ids',[])
        if (item.get('classification') not in {'known','inferred'} or not isinstance(value,str) or not value.strip()
                or not ids or len(ids)!=len(set(ids)) or any(i not in by_id for i in ids)):continue
        if field.get('options') and not _listed_answer(field,value):continue
        if field.get('type')=='checkbox' and value not in {'Yes','No'}:continue
        if field.get('type')=='url':
            from .resume_styles import link_target
            if not value.startswith('https://') or link_target(value)!=value or not any(value in by_id[i]['text'] for i in ids):continue
        prior=[*previous,*(a for a in known+resolved if fields.index(a['field'])<fields.index(field))]
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
    if not resolved:
        store.db.execute('INSERT OR IGNORE INTO model_abstentions VALUES(?,?,?,?)',
                         (attempt,'page_plan','; '.join(f['label'] for f in candidates),now()))
    gaps=[{'label':by_field[item['field_id']]['label'],'detail':' '.join(str(item.get('reason','')).split())[:300]}
          for item in items if item.get('classification')=='missing']
    if gaps:store.event('page_answer_gaps',context.get('id',host),{'fields':gaps})
    return resolved,remaining
