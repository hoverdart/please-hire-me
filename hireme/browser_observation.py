"""Accessible observations for the application UI agent.

Selectors and DOM inspection are owned by the executor. Models receive opaque
action IDs and readable observations, never authority to execute page code.
"""
from __future__ import annotations

import time

from .util import digest


CONTROLS = ('input:not([type=hidden]):not([type=submit]):not([type=button]),textarea,select,'
    '[role=combobox]:not(input):not(select),button[aria-haspopup=listbox]:not([role=combobox]),'
    'button[data-automation-id$=Dropdown]:not([role=combobox]):not([aria-haspopup=listbox]),'
    '[role=checkbox]:not(input),[role=radio]:not(input),[contenteditable=true]')
ACTIONS = 'button,a[href],input[type=submit],[role=button]:not(button)'


def retry_dom_read(page,read,*,checkpoint=None,timeout=5):
    """Reobserve after a navigation replaces the document; never retry actions."""
    from playwright.sync_api import Error
    deadline=time.monotonic()+timeout
    while True:
        if checkpoint:checkpoint()
        try:return read()
        except Error as error:
            transient=any(message in str(error) for message in (
                'Execution context was destroyed','Cannot find context with specified id'))
            if getattr(page,'_retry_detached_reads',False) is True:
                transient=transient or any(message in str(error) for message in (
                    'Element is not attached to the DOM','Unable to adopt element handle from a different document',
                    'Frame was detached','Frame has been detached'))
            if not transient or page.is_closed() or time.monotonic()>=deadline:raise
            page.wait_for_timeout(50)

OBSERVE = r"""({controlsSelector,actionsSelector}) => {
 const controls=Array.from(document.querySelectorAll(controlsSelector));
 const seen=new Set(),fields=[];
 const clean=s=>(s||'').replace(/\s+/g,' ').trim();
 const visible=e=>{
   if(e.closest('[hidden],[inert],[aria-hidden="true"]'))return false;
   const native=e.matches('input[type=radio],input[type=checkbox]');
   if(!e.checkVisibility({visibilityProperty:true,opacityProperty:!native}))return false;
   if(native)for(let parent=e.parentElement;parent;parent=parent.parentElement)
     if(Number(getComputedStyle(parent).opacity)===0)return false;
   return true;
 };
 const ref=e=>({id:e.id||'',name:e.getAttribute('name')||'',tag:e.tagName.toLowerCase(),
   automation:e.getAttribute('data-automation-id')||''});
 function text(node){if(!node)return '';const copy=node.cloneNode(true);
   copy.querySelectorAll('input,textarea,select,button,[role=combobox],[role=checkbox],[role=radio]').forEach(n=>n.remove());
   return clean(copy.textContent);}
 function labelled(e){return clean(e.getAttribute('aria-label')||
   (e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ')||
   Array.from(e.labels||[]).map(text).join(' '));}
 function wrapper(e){return e.closest('[data-automation-id^="formField"],.field,.field-wrapper,[data-field-path],[class*=application-question]');}
 function label(e){const field=wrapper(e);
   const workday=field?.matches('[data-automation-id^="formField"]')?text(field.querySelector('label')||field.querySelector('legend')):'';
   return workday||clean(Array.from(e.labels||[]).map(text).join(' '))||labelled(e)||text(field?.querySelector('label'))||
   text(e.closest('fieldset')?.querySelector('legend'))||e.getAttribute('placeholder')||'';}
 function history(e){
   const fkit=n=>n.closest('[data-fkit-id]')?.getAttribute('data-fkit-id')||n.id||'';
   const record=fkit(e).match(/^(education|workExperience)-(\d+)--/);
   if(record){
     const prefix=record[1]+'-'+record[2]+'--';
     const peers=Array.from(new Set(controls.filter(visible).map(fkit)
       .map(id=>id.match(new RegExp('^'+record[1]+'-\\d+--'))?.[0]).filter(Boolean)));
     return {section:record[1]==='education'?'education':'employment',section_entry:Math.max(0,peers.indexOf(prefix)),history_ref:prefix};
   }
   const selectors={education:'[data-automation-id="educationSection"],[data-automation-id="education"],[data-automation-id^="education-"],.education--form',
     employment:'[data-automation-id="workExperienceSection"],[data-automation-id="workExperience"],[data-automation-id^="workExperience-"],.employment--form,.experience--form'};
   for(const [kind,selector] of Object.entries(selectors)){
     let row=e.closest(selector);if(!row)continue;
     // Prefer a numbered record to its outer section.
     const numbered=e.closest('[data-automation-id^="education-"],[data-automation-id^="workExperience-"]');
     if(numbered)row=numbered;
     const peers=Array.from(document.querySelectorAll(selector)).filter(n=>visible(n)&&
       (numbered?n.getAttribute('data-automation-id')?.match(/-\d+$/):n.tagName===row.tagName&&n.getAttribute('data-automation-id')===row.getAttribute('data-automation-id')));
     return {section:kind,section_entry:Math.max(0,peers.indexOf(row))};
   }
   if(e.matches('button,[role=button]')){
     for(let parent=e.parentElement;parent&&!parent.matches('[data-automation-id=applyFlowMyExpPage]');parent=parent.parentElement){
       const records=Array.from(new Set(Array.from(parent.querySelectorAll('[data-fkit-id]'))
         .map(n=>n.getAttribute('data-fkit-id')?.match(/^(education|workExperience)-\d+--/)?.[0]).filter(Boolean)));
       if(records.length>1)break;
       if(records.length===1){
         const prefix=records[0],kind=prefix.startsWith('education-')?'education':'employment';
         const peers=Array.from(new Set(controls.filter(visible).map(fkit).map(id=>id.match(
           kind==='education'?/^education-\d+--/:/^workExperience-\d+--/)?.[0]).filter(Boolean)));
         return {section:kind,section_entry:Math.max(0,peers.indexOf(prefix)),history_ref:prefix};
       }
     }
   }
   // Empty Workday sections have an Add control before any numbered row.
   // Stop at the shared page boundary so unrelated uploads/social fields do
   // not inherit the first work-history heading elsewhere on the page.
   for(let parent=e.parentElement;parent&&!parent.matches('[data-automation-id=applyFlowMyExpPage]');parent=parent.parentElement){
     const heading=clean(parent.querySelector('h2,h3,h4')?.textContent);
     if(/^(?:Work Experience|Education)$/.test(heading))
       return {section:heading==='Education'?'education':'employment',section_entry:0};
   }
   return {};
 }
 function options(e){
   const widget=e.getAttribute('data-uxi-multiselect-id');
   if(widget)return Array.from(document.querySelectorAll('[role=option]')).map((n,index)=>({n,index}))
     .filter(({n})=>visible(n)&&n.getAttribute('aria-disabled')!=='true'&&
       n.querySelector('[data-uxi-multiselect-id]')?.getAttribute('data-uxi-multiselect-id')===widget)
     .map(({n,index})=>({label:clean(n.querySelector('[data-automation-id=promptOption]')?.textContent||n.innerText),
       value:clean(n.querySelector('[data-automation-id=promptOption]')?.textContent||n.innerText),index,
       group:n.querySelector('[data-uxi-multiselectlistitem-hassidecharm=true]')!==null}));
   let menu=null;
   for(const id of (e.getAttribute('aria-controls')||e.getAttribute('aria-owns')||'').split(/\s+/)){
     const n=document.getElementById(id);if(n&&visible(n)){menu=n;break;}
   }
   const menus=Array.from(document.querySelectorAll('[role=listbox]:not([data-automation-id=selectedItemList])')).filter(visible);
   if(!menu&&menus.length===1&&(e===document.activeElement||e.contains(document.activeElement)||e.getAttribute('aria-expanded')==='true'))menu=menus[0];
   if(!menu)return [];
   return Array.from(menu.querySelectorAll('[role=option]')).filter(n=>visible(n)&&n.getAttribute('aria-disabled')!=='true')
     .map(n=>({label:clean(n.innerText),value:n.getAttribute('data-value')||n.getAttribute('data-automation-id')||clean(n.innerText)}));
 }
 controls.forEach((e,index)=>{
   const file=e.type==='file';
   if(e.disabled||e.closest('[hidden],[inert],[aria-hidden="true"]'))return;
   if(file){for(let n=e.parentElement;n;n=n.parentElement){const s=getComputedStyle(n);
     if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0)return;}}
   else if(!visible(e))return;
   if(e.matches('input[data-uxi-widget-type=selectinput]:not([data-automation-id=searchBox])')){
     const search=e.closest('[data-automation-id=multiSelectContainer]')?.querySelector('input[data-automation-id=searchBox]');
     if(search&&visible(search))return;
   }
   if(file&&e.closest('[class*=autofill],[data-automation-id*=autofill]'))return;
   let type=e.tagName==='SELECT'?'select':e.tagName==='TEXTAREA'||e.isContentEditable?'textarea':
     e.getAttribute('role')==='checkbox'?'checkbox':e.getAttribute('role')==='radio'?'radio':
     e.getAttribute('role')==='combobox'||e.matches('input[data-uxi-widget-type=selectinput],button[aria-haspopup=listbox],button[data-automation-id$=Dropdown]')?'combobox':e.type||'text';
   if(['button','submit','reset','search'].includes(type))return;
   let title=label(e),indices=[index],choices=[],native=[],value=e.isContentEditable?e.innerText:e.value||'';
   let required=e.required||e.getAttribute('aria-required')==='true'||/[✱*]/.test(title)||
     /\bRequired\s*$/i.test(e.getAttribute('aria-label')||'');
   if(type==='radio'){
     const root=e.closest('[role=radiogroup],fieldset');
     const key=root||((e.form?.id||'')+':'+e.name);
     if(!key||seen.has(key))return;seen.add(key);
     const group=controls.filter(n=>visible(n)&&!n.disabled&&
       (root?root.contains(n)&&(n.type==='radio'||n.getAttribute('role')==='radio'):n.type==='radio'&&n.name===e.name&&n.form===e.form));
     indices=group.map(n=>controls.indexOf(n));choices=group.map(n=>labelled(n)||clean(n.innerText)||n.value);
     native=group.map(n=>n.value||n.getAttribute('data-value')||clean(n.innerText));
     title=text(root?.querySelector('legend'))||labelled(root||e)||title;
     required=required||group.some(n=>n.required||n.getAttribute('aria-required')==='true');
     value=group.filter(n=>n.checked||n.getAttribute('aria-checked')==='true').map(n=>choices[group.indexOf(n)]).join('; ');
   }else if(type==='checkbox'){
     choices=['Yes','No'];value=e.checked||e.getAttribute('aria-checked')==='true'?'Yes':'No';
   }else if(type==='select'){
     const opts=Array.from(e.options).filter(n=>!n.disabled&&n.value);
     choices=opts.map(n=>clean(n.textContent));native=opts.map(n=>n.value);
     value=e.selectedOptions.length?clean(e.selectedOptions[0].textContent):'';
     if(!e.value)value='';
   }else if(type==='combobox'){
     const opts=options(e).filter(n=>!n.group);choices=opts.map(n=>n.label);native=opts.map(n=>n.value);
     value=e.tagName==='BUTTON'?clean(e.innerText):value;
     if(e.matches('[data-uxi-widget-type=selectinput]')){
       const container=e.closest('[data-automation-id=multiSelectContainer]');
       const selected=Array.from(container?.querySelectorAll('[data-automation-id=selectedItem] [data-automation-id=promptOption]')||[]);
       value=selected.length?selected.map(n=>clean(n.textContent)).join('; '):clean(container?.querySelector('[data-automation-id=promptSelectionLabel]')?.textContent);
     }
     if(/^(?:select|choose|search|start typing)(?:\s|\.|$)/i.test(value))value='';
   }
   const field=wrapper(e);
   if(file&&field?.getAttribute('data-fkit-id')==='resumeAttachments--attachments'){
     for(let parent=field.parentElement;parent&&!parent.matches('[data-automation-id=applyFlowMyExpPage]');parent=parent.parentElement){
       const heading=clean(parent.querySelector('h2,h3,h4')?.textContent);
       if(/^Resume\s*\/\s*CV$/i.test(heading)){title='Resume/CV';break;}
     }
   }
   const automation=e.getAttribute('data-automation-id')||'';
   let datePart=null;
   if(/dateSectionMonth-input$/i.test(automation)){title=clean(title+' month');datePart='month_number';}
   if(/dateSectionYear-input$/i.test(automation)){title=clean(title+' year');datePart='year';}
   const descriptions=(e.getAttribute('aria-describedby')||'').split(/\s+/).filter(id=>id&&!/-(?:placeholder|error|live-region)$/.test(id))
     .map(id=>document.getElementById(id)).filter(n=>n&&!(datePart&&n.id.startsWith('helpText-'))&&
       !n.matches('[role=alert],[role=log],.select__placeholder,[data-automation-id=promptAriaInstruction],[data-automation-id=promptSelectionLabel]'));
   const agreement=type==='checkbox'?e.closest('[data-fkit-id="termsAndConditions--null"]'):null;
   if(agreement&&!descriptions.includes(agreement))descriptions.push(agreement);
   const help=clean(descriptions.map(n=>n.textContent).join(' '));
   const helpLinks=descriptions.flatMap(n=>Array.from(n.querySelectorAll('a[href]')).map(a=>({text:clean(a.textContent),url:a.href})));
   fields.push({index,indices,form_index:e.form?Array.from(document.forms).indexOf(e.form):-1,
     ref:ref(e),refs:indices.map(i=>ref(controls[i])),label:clean(title),type,
     options:choices,...(native.length?{option_values:native}:{}),required:!!required,
     ...(type==='combobox'?{options_visible:options(e).length>0}:{}),
     ...(e.matches('[data-uxi-widget-type=selectinput]')?{workday_prompt:true,
       prompt_ref:{id:field?.getAttribute('data-fkit-id')||'',automation:field?.getAttribute('data-automation-id')||''},
       selected_options:Array.from(e.closest('[data-automation-id=multiSelectContainer]')?.querySelectorAll('[data-automation-id=selectedItem] [data-automation-id=promptOption]')||[]).map(n=>clean(n.textContent)),
       search_on_enter:e.getAttribute('enterkeyhint')==='search',
       option_indices:options(e).filter(n=>!n.group).map(n=>n.index),
       option_groups:options(e).filter(n=>n.group).map(n=>n.label),
       group_indices:options(e).filter(n=>n.group).map(n=>n.index)}:{}),
     maxlength:e.maxLength>0?e.maxLength:-1,min:e.getAttribute('min'),max:e.getAttribute('max'),
     step:e.getAttribute('step'),pattern:e.getAttribute('pattern'),value:type==='textarea'?value:clean(value),multiple:!!e.multiple,
     ...(help?{help_text:help}:{}),...(helpLinks.length?{help_links:helpLinks}:{}),...history(e),
     ...(datePart?{history_date_part:datePart}:{}),
     ...(/--phoneNumber$/.test(e.id)&&document.getElementById(e.id.replace(/--phoneNumber$/,'--countryPhoneCode'))?{phone_component:'national'}:{}),
     ...(e.isContentEditable?{contenteditable:true}:{}),
     ...(type==='date'?{date_format:'YYYY-MM-DD'}:{}),
     ...(field?.querySelector('[role=alert]')?{error:clean(field.querySelector('[role=alert]').innerText)}:{})});
 });
 const actions=Array.from(document.querySelectorAll(actionsSelector)).map((e,index)=>({e,index})).filter(({e})=>
   visible(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true'&&!e.matches(controlsSelector)).map(({e,index})=>({
     index,ref:ref(e),label:labelled(e)||clean(e.innerText||e.value),href:e.tagName==='A'?e.href:'',
     submit:e.type==='submit',in_form:!!e.closest('form'),form_index:e.form?Array.from(document.forms).indexOf(e.form):-1,...history(e),
     context:clean(e.closest('section,fieldset,[role=dialog]')?.querySelector('h1,h2,h3,legend')?.innerText||'')
   })).filter(a=>a.label).slice(0,100);
 const headings=Array.from(document.querySelectorAll('h1,h2,h3,[role=heading]')).filter(visible).map(e=>clean(e.innerText));
 const errors=Array.from(document.querySelectorAll('[role=alert],[aria-live=assertive]')).filter(visible)
   .filter(e=>!(e.getAttribute('data-automation-id')==='ariaLiveMessage'&&e.getAttribute('aria-live')==='polite'&&
     /^[a-f0-9]{64}\.pdf successfully uploaded\.?$/i.test(clean(e.innerText))))
   .map(e=>clean(e.innerText)).filter(Boolean);
 return {fields,actions,headings,errors,text:document.body?.innerText.slice(0,50000)||'',url:location.href};
}"""


def identity(field):
    data={k: field.get(k) for k in ('label','type','ref','section','section_entry',
        'required','min','max','maxlength','date_format','help_text','help_links')}
    if field.get('workday_prompt') and field.get('prompt_ref',{}).get('automation'):
        data['ref']=field['prompt_ref']
    return digest(data)


def observe(page):
    observation=retry_dom_read(page,lambda:page.evaluate(OBSERVE,{'controlsSelector':CONTROLS,'actionsSelector':ACTIONS}))
    from .browser import _document_kind
    forms={f['form_index'] for f in observation['fields'] if f['type']=='file' and _document_kind(f)=='resume'}-{-1}
    if len(forms)==1:
        form=next(iter(forms))
        observation['fields']=[f for f in observation['fields'] if f['form_index'] in {-1,form}]
        observation['actions']=[a for a in observation['actions'] if a['form_index'] in {-1,form}]
    for field in observation['fields']:
        field['ui_id']=identity(field)
    for action in observation['actions']:
        action['ui_id']=digest({k:action.get(k) for k in ('index','label','ref','href','submit','in_form','section','context')})
    observation['signature']=digest({'url':observation['url'],
        'fields':[(f['ui_id'],f['options'],f.get('option_values'),f.get('option_groups')) for f in observation['fields']],
        'actions':[a['ui_id'] for a in observation['actions']]})
    return observation
