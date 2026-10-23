"use strict";
(() => {
  const get=id=>document.getElementById(id), panel=get('history-import-panel'),form=get('history-import-form');
  const status=get('history-import-status'),fields=get('history-import-fields');
  const definitions={
    employment:[['organization','Organization'],['title','Job title'],['description','Responsibilities and accomplishments']],
    education:[['institution','Institution'],['degree','Degree'],['field_of_study','Field of study'],['gpa','GPA'],['expected_end','Expected end month']]
  };
  const common=[['location','Location'],['current','Current status'],['start','Start month'],['end','Actual end month']];
  let snapshot=null,source=null,busy=false,activeKey='',excerptHash='',selectionReady=false,drafts=new Map();
  function announce(text,error=false){status.textContent=text;status.setAttribute('role',error?'alert':'status');}
  function controls(){
    const readonly=!snapshot||snapshot.demo;
    get('history-import-load').disabled=busy||readonly;
    for(const control of form.elements)control.disabled=busy||readonly;
    get('history-import-confirm').disabled=busy||readonly||!selectionReady;
    for(const button of get('history-import-candidates').querySelectorAll('button'))button.disabled=busy||readonly;
  }
  function values(){
    const result={};
    for(const control of fields.querySelectorAll('input,select,textarea')){
      if(control.value==='')continue;
      result[control.name]=control.name==='current'?control.value==='true':control.value;
    }
    return result;
  }
  function saveDraft(){if(activeKey)drafts.set(activeKey,values());}
  function fill(kind,proposals){
    fields.replaceChildren();
    for(const [name,labelText] of [...definitions[kind],...common]){
      let control;
      if(name==='description'){control=el('textarea');control.rows=5;control.maxLength=8000;}
      else if(name==='current'){
        control=el('select');
        for(const [value,label] of [['','Not specified'],['true','Current'],['false','Not current']]){
          const option=el('option',label);option.value=value;control.append(option);
        }
      }else{control=el('input');control.type=['start','end','expected_end'].includes(name)?'month':'text';control.maxLength=1000;}
      control.name=name;control.value=proposals[name]===undefined?'':String(proposals[name]);
      control.required=['organization','title','institution'].includes(name);
      const label=el('label',labelText,name==='description'?'wide':undefined);label.append(control);fields.append(label);
    }
    form.elements.confirmed.checked=false;
    const similar=(source?.history||[]).filter(entry=>entry.kind===kind&&['organization','title','institution'].some(key=>proposals[key]&&entry.values[key]?.toLowerCase()===proposals[key].toLowerCase()));
    get('history-import-duplicates').textContent=similar.length?'Similar saved entries exist. Check Education and employment before adding another role or school. Identical confirmed entries are reused; existing entries are never overwritten.':'Existing entries are kept. You can edit or withdraw an imported entry in Education and employment.';
  }
  function choose(proposal){
    saveDraft();form.hidden=false;form.elements.kind.value=proposal.kind;form.elements.start_line.value=proposal.start_line;form.elements.end_line.value=proposal.end_line;
    activeKey=[proposal.kind,proposal.start_line,proposal.end_line].join(':');
    excerptHash=proposal.excerpt_hash||'';selectionReady=!!excerptHash;
    get('history-import-excerpt').textContent=proposal.excerpt;fill(proposal.kind,drafts.get(activeKey)||proposal.values);
    announce('Review the original excerpt and every proposed field before confirming.');controls();
  }
  async function load(){
    if(busy||snapshot?.demo)return;saveDraft();busy=true;controls();announce('Reading the selected resume locally…');
    try{
      const result=await api('/api/history-import');
      if(source&&source.resume_hash!==result.resume_hash){drafts.clear();activeKey='';form.hidden=true;}
      source=result;const list=get('history-import-candidates');list.replaceChildren();
      get('history-import-source').textContent=result.lines.map((line,index)=>(index+1)+': '+line).join('\n');
      for(const proposal of result.candidates){
        const label=proposal.values.organization||proposal.values.institution||'Unrecognized header';
        const button=el('button',proposal.kind+' · '+label+' · lines '+proposal.start_line+'–'+proposal.end_line,'secondary');button.type='button';
        button.onclick=()=>choose(proposal);list.append(button);
      }
      const manual=el('button','Choose an excerpt manually','secondary');manual.type='button';manual.onclick=()=>choose({kind:'employment',start_line:1,end_line:Math.min(result.lines.length,10),excerpt:result.lines.slice(0,10).join('\n'),values:{}});list.append(manual);
      announce(result.candidates.length+' proposed excerpts. Select one or choose lines manually. Nothing has been added to your history.');
    }catch(error){announce(error.message,true);}
    finally{busy=false;controls();}
  }
  get('history-import-select').onclick=async()=>{
    if(!source||busy)return;saveDraft();busy=true;controls();announce('Checking the selected source lines…');
    try{
      const selection={resume_hash:source.resume_hash,kind:form.elements.kind.value,start_line:Number(form.elements.start_line.value),end_line:Number(form.elements.end_line.value)};
      const result=await api('/api/history-import/suggest',selection);choose({...selection,...result});
    }catch(error){announce(error.message,true);}finally{busy=false;controls();}
  };
  form.elements.kind.onchange=()=>{saveDraft();activeKey='';selectionReady=false;fill(form.elements.kind.value,{});announce('Entry type changed. Propose fields from these source lines, then review them.');controls();};
  form.oninput=event=>{
    saveDraft();if(event.target.name!=='confirmed')form.elements.confirmed.checked=false;
    if(['start_line','end_line'].includes(event.target.name)){selectionReady=false;announce('Source lines changed. Propose fields from these lines before confirming.');controls();}
  };
  form.onsubmit=async event=>{
    event.preventDefault();if(!source||busy||snapshot.demo)return;
    if(!form.elements.confirmed.checked){announce('Review and explicitly confirm this entry first.',true);return;}
    if(!selectionReady)return;
    const data={resume_hash:source.resume_hash,excerpt_hash:excerptHash,kind:form.elements.kind.value,start_line:Number(form.elements.start_line.value),end_line:Number(form.elements.end_line.value),values:values(),confirmed:true};
    busy=true;controls();announce('Saving the applicant-confirmed entry…');
    try{
      const result=await api('/api/history-import/confirm',data);form.elements.confirmed.checked=false;
      await refresh();announce(result.existing?'This confirmed entry already exists. Its source and revision were preserved.':'Entry imported and confirmed. Edit or withdraw it in Education and employment.');
    }catch(error){announce(error.message,true);}finally{busy=false;controls();}
  };
  get('history-import-load').onclick=load;
  panel.ontoggle=()=>{if(panel.open&&!source)load();};
  window.historyImport={render(value){snapshot=value;if(source&&value.history)source.history=value.history;controls();}};
  if(typeof state!=='undefined'&&state)window.historyImport.render(state);
})();
