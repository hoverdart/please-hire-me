"use strict";
(() => {
  const panel=document.getElementById('recruiting-insights-panel'), form=document.getElementById('insights-filters');
  const status=document.getElementById('insights-status'), evidence=document.getElementById('insights-evidence');
  let snapshot=null,busy=false,result=null,metric='attempts',group='',offset=0,groupOffset=0,evidenceRequest=0;
  const primary=['mature','responded','assessment','interview','offer','rejection','awaiting'];
  const get=id=>document.getElementById(id);
  function announce(text,error=false){status.textContent=text;status.setAttribute('role',error?'alert':'status');}
  function controls(){
    for(const control of form.elements)control.disabled=busy;
    for(const button of panel.querySelectorAll('[data-insight-count]'))button.disabled=busy;
    get('insights-previous').disabled=busy||!result||offset===0;
    get('insights-next').disabled=busy||!result||offset+25>=result.applications.total;
    get('insights-group-previous').disabled=busy||!result||groupOffset===0;
    get('insights-group-next').disabled=busy||!result||groupOffset+20>=result.groups.total;
    get('insights-clear-group').disabled=busy;
  }
  function count(key,value,label,groupId=group){
    const button=el('button',label+' · '+value,'secondary');button.type='button';
    button.dataset.insightCount=key;button.dataset.groupId=groupId;
    button.setAttribute('aria-pressed',String(key===metric&&groupId===group));
    button.onclick=()=>{metric=key;group=groupId;offset=0;load(true);};return button;
  }
  async function showEvidence(record){
    const request=++evidenceRequest,windowDays=result.filters.window;
    evidence.hidden=false;evidence.dataset.loaded='false';evidence.dataset.loading='false';
    evidence.tabIndex=-1;await loadEvidence(record,evidence);
    if(request!==evidenceRequest)return;
    const context=el('div');context.append(el('h3','Comparison evidence'),el('p','This comparison uses reviewed messages received in the first '+windowDays+' days. The full application history below can include later responses.','help'));
    for(const item of record.counted_feedback)context.append(el('p',item.category+' · '+date(item.received)+' · '+item.subject,'help'));
    evidence.prepend(context);evidence.scrollIntoView({block:'start',behavior:'auto'});evidence.focus({preventScroll:true});
  }
  function render(data){
    get('insights-method').textContent='Scope: '+data.scope+'. Response milestones and mail-review coverage use confirmed submissions with a complete '+data.filters.window+'-day window; only mail arriving in those first '+data.filters.window+' days is eligible. Recent submissions and submission gaps are listed separately. An offer does not imply an observed interview.';
    for(const id of ['insights-metrics','insights-coverage'])get(id).replaceChildren();
    for(const [key,label] of Object.entries(data.metrics))get(primary.includes(key)?'insights-metrics':'insights-coverage').append(count(key,data.counts[key],label));
    get('insights-quality').textContent='Use the metadata and timing metrics above to inspect evidence gaps. Unknown or future submission dates remain available for inspection and never enter response denominators.';
    const groups=get('insights-groups');groups.replaceChildren();
    for(const item of data.groups.items){
      const tr=el('tr'), name=el('td');name.dataset.label='Group';
      const select=el('button',item.label,'secondary');select.type='button';select.disabled=busy;select.dataset.insightCount='attempts';select.dataset.groupId=item.id;
      select.onclick=()=>{group=item.id;metric='attempts';offset=0;load(true);};name.append(select);
      if(item.small_sample)name.append(el('p','Small sample: '+item.counts.mature+' mature submissions.','help'));
      tr.append(name);
      for(const [key,label] of [['mature','Full window'],['responded','Responses'],['interview','Interviews'],['offer','Offers']]){
        const td=el('td');td.dataset.label=label;const button=count(key,item.counts[key],label,item.id);
        button.textContent=String(item.counts[key]);
        button.setAttribute('aria-label',item.label+': '+label+' '+item.counts[key]);td.append(button);tr.append(td);
      }
      const share=el('td',item.interview_share===null?'Unavailable (no denominator)':item.counts.interview+' / '+item.counts.mature+' ('+item.interview_share+'%)');share.dataset.label='Recorded interview share';tr.append(share);groups.append(tr);
    }
    if(!data.groups.total){const tr=el('tr'),td=el('td','No recorded attempts in this period.');td.colSpan=6;tr.append(td);groups.append(tr);}
    const gp=data.groups;get('insights-group-page').textContent=gp.total?(gp.offset+1)+'–'+Math.min(gp.offset+20,gp.total)+' of '+gp.total+' groups':'No groups';
    get('insights-selection').textContent=data.metrics[metric]+' · '+data.scope;
    get('insights-clear-group').hidden=!group;
    const list=get('insights-applications');list.replaceChildren();
    for(const record of data.applications.items){
      const article=el('article',undefined,'insight-attempt');article.append(el('h4',record.company+' · '+record.title));
      article.append(el('p',record.state.replaceAll('_',' ')+' · '+(record.age_days===null?'Submission date unavailable':record.age_days+' days since final intent')+' · '+record.source,'help'));
      if(!['submit_intent','prepared'].includes(record.context_source))article.append(el('p','Posting identity is current metadata or unavailable; it was not captured for this attempt.','help'));
      article.append(el('p','In the observation window: '+(record.counted_feedback.map(item=>item.category).filter((value,index,array)=>array.indexOf(value)===index).join(', ')||'No reviewed response')+'.','help'));
      if(record.pending_feedback.length)article.append(el('p',record.pending_feedback.length+' matching messages still need review; they do not count as recruiting outcomes.','help'));
      const button=el('button','View application evidence','secondary');button.type='button';button.onclick=()=>showEvidence(record);article.append(button);list.append(article);
    }
    if(!data.applications.total)list.append(el('p','No attempts match this metric and group. Missing feedback is not a rejection.','help'));
    const page=data.applications;get('insights-page').textContent=page.total?(page.offset+1)+'–'+Math.min(page.offset+25,page.total)+' of '+page.total+' attempts':'No matching attempts';
  }
  async function load(jumpToEvidence=false){
    if(busy||!snapshot)return;busy=true;controls();announce('Reading saved application and reviewed-mail evidence…');
    evidence.hidden=true;evidenceRequest++;const focused=document.activeElement?.dataset.insightCount;
    try{
      const query=new URLSearchParams({group_by:form.elements.group_by.value,lookback:form.elements.lookback.value,window:form.elements.window.value,metric,group_id:group,offset:String(offset),group_offset:String(groupOffset)});
      const data=await api('/api/recruiting-insights?'+query);result=data;offset=data.applications.offset;groupOffset=data.groups.offset;render(data);
      announce('Saved data as of '+date(data.as_of)+'. Shares are descriptive; role, company, timing and review coverage can explain differences.');
      if(jumpToEvidence){const heading=get('insights-selection');heading.tabIndex=-1;heading.scrollIntoView({block:'start',behavior:'auto'});heading.focus({preventScroll:true});}
      else if(focused&&document.activeElement===document.body)panel.querySelector('[data-insight-count="'+focused+'"][data-group-id="'+group+'"]')?.focus({preventScroll:true});
    }catch(error){
      result=null;for(const id of ['insights-metrics','insights-coverage','insights-groups','insights-applications'])get(id).replaceChildren();
      get('insights-method').textContent='Results unavailable for these filters. Refresh insights to try again.';
      for(const id of ['insights-group-page','insights-page','insights-quality'])get(id).textContent='';
      announce(error.message,true);
    }
    finally{busy=false;controls();}
  }
  form.onsubmit=event=>{event.preventDefault();offset=0;load();};
  form.onchange=()=>{group='';offset=groupOffset=0;load();};
  get('insights-clear-group').onclick=()=>{group='';offset=0;load();};
  get('insights-previous').onclick=()=>{offset=Math.max(0,offset-25);load();};get('insights-next').onclick=()=>{offset+=25;load();};
  get('insights-group-previous').onclick=()=>{groupOffset=Math.max(0,groupOffset-20);load();};get('insights-group-next').onclick=()=>{groupOffset+=20;load();};
  panel.ontoggle=()=>{if(panel.open)load();};
  window.recruitingInsights={render(value){const initial=!snapshot;snapshot=value;controls();if(initial&&panel.open)load();}};
  if(typeof state!=='undefined'&&state)window.recruitingInsights.render(state);
})();
