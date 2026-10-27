"use strict";
(() => {
  const panel=document.getElementById('recruiting-feedback-panel');
  const list=document.getElementById('feedback-list');
  const status=document.getElementById('feedback-status');
  const filter=document.getElementById('feedback-filter');
  const syncButton=document.getElementById('feedback-sync');
  const reload=document.getElementById('feedback-reload');
  const previous=document.getElementById('feedback-previous');
  const next=document.getElementById('feedback-next');
  const labels={acknowledgement:'Application acknowledgement',assessment:'Assessment',interview:'Interview',rejection:'Rejection',offer:'Offer',unclassified:'Needs classification'};
  const drafts=new Map();
  let snapshot=null,busy=false,syncing=false,offset=0,result=null;

  function announce(text,error=false){status.textContent=text;status.setAttribute('role',error?'alert':'status');}
  function controls(){
    const readonly=!snapshot||snapshot.demo;
    syncButton.disabled=readonly||busy||syncing||!snapshot.settings.gmail_feedback||!snapshot.gmail.connected;
    reload.disabled=busy||syncing;
    filter.disabled=busy||syncing;
    previous.disabled=busy||syncing||offset===0;
    next.disabled=busy||syncing||!result||offset+result.limit>=result.total;
  }
  function field(labelText,input){const label=el('label',labelText);label.append(input);return label;}
  function card(message){
    const article=el('article',undefined,'artifact-row feedback-row');
    article.append(el('h3',message.subject||'Recruiting message'),el('p',`${message.sender||'Unknown sender'} · ${date(message.received)} · ${message.state}`,'help'));
    article.append(el('p',message.excerpt),el('p',message.match_reason,'help'));
    article.append(el('p',message.sender_verified?'Sender domain passed Gmail DKIM checks. Review the message before recording its meaning.':'Sender authentication was not verified. Inspect the original in Gmail before confirming.','help'));
    const mail=el('a','Open original in Gmail');mail.href=`https://mail.google.com/mail/u/?authuser=${encodeURIComponent(snapshot.gmail.email||'')}#all/${encodeURIComponent(message.message_id)}`;mail.target='_blank';mail.rel='noopener noreferrer';article.append(mail);
    const form=el('form',undefined,'form-grid');
    const category=el('select');category.name='category';
    for(const [value,label] of Object.entries(labels)){const option=el('option',label);option.value=value;category.append(option);}
    const application=el('select');application.name='application_id';
    const placeholder=el('option','Choose the application attempt');placeholder.value='';application.append(placeholder);
    for(const candidate of message.candidates){const option=el('option',`${candidate.company} · ${candidate.title} · ${date(candidate.attempted)}`);option.value=candidate.id;application.append(option);}
    const draft=drafts.get(message.id);
    category.value=draft?.revision===message.revision?draft.category:message.category;
    application.value=draft?.revision===message.revision?draft.application_id:(message.application_id||'');
    form.append(field('Feedback category',category),field('Application attempt',application));
    const attempt=el('p',undefined,'help wide feedback-attempt');attempt.setAttribute('aria-live','polite');
    const describeAttempt=()=>{
      const selected=message.candidates.find(candidate=>candidate.id===application.value);
      attempt.textContent=selected?`${selected.company} · ${selected.title} · Attempted ${date(selected.attempted)} · Recorded outcome: ${selected.state||'see application history'} · Attempt ID: ${selected.id}`:'Select an attempt to inspect its full details before confirming.';
    };
    application.addEventListener('change',describeAttempt);describeAttempt();form.append(attempt);
    const actions=el('div',undefined,'actions wide');
    const confirm=el('button',message.state==='confirmed'?'Update confirmed feedback':'Confirm feedback');confirm.type='submit';
    const dismiss=el('button','Dismiss message','secondary');dismiss.type='button';actions.append(confirm,dismiss);form.append(actions);
    const response=el('p',undefined,'help wide');response.setAttribute('role','status');form.append(response);
    const saveDraft=()=>drafts.set(message.id,{category:category.value,application_id:application.value,revision:message.revision});
    form.addEventListener('input',saveDraft);form.addEventListener('change',saveDraft);
    async function save(state){
      if(snapshot.demo||form.dataset.saving==='true')return;
      if(state==='confirmed'&&(!application.value||category.value==='unclassified')){response.textContent='Choose the category and exact application attempt before confirming.';response.setAttribute('role','alert');return;}
      saveDraft();form.dataset.saving='true';for(const input of form.elements)input.disabled=true;
      try{
        await api('/api/recruiting-feedback/review',{id:message.id,revision:message.revision,state,category:category.value,application_id:application.value});
        drafts.delete(message.id);announce('Feedback saved. The original submission outcome is unchanged.');await load(true);
      }catch(error){response.textContent=error.message;response.setAttribute('role','alert');}
      finally{form.dataset.saving='false';for(const input of form.elements)input.disabled=!!snapshot.demo;}
    }
    form.onsubmit=event=>{event.preventDefault();save('confirmed');};dismiss.onclick=()=>save('dismissed');
    for(const input of form.elements)input.disabled=!!snapshot.demo;
    article.append(form);return article;
  }
  async function load(preserveMessage=false){
    if(busy||!snapshot)return;busy=true;controls();list.setAttribute('aria-busy','true');
    if(!preserveMessage)announce('Loading recruiting feedback…');
    try{
      const response=await api(`/api/recruiting-feedback?state=${encodeURIComponent(filter.value)}&offset=${offset}`);
      result=response;list.replaceChildren();
      for(const message of response.messages)list.append(card(message));
      if(!response.messages.length)list.append(el('p',snapshot.demo?'This sample has no recruiting messages. Your own workspace can link Gmail feedback to recorded application attempts.':'No matching feedback on this page. Enable recruiting feedback in Email automation, then sync. Only messages matching recorded application attempts are kept.','help'));
      document.getElementById('feedback-page').textContent=response.total?`${Math.min(offset+1,response.total)}–${Math.min(offset+response.limit,response.total)} of ${response.total} messages`:'No messages';
      if(!preserveMessage)announce(`${response.counts.pending||0} messages need review. Confirmed feedback remains separate from submission receipts.`);
    }catch(error){announce(error.message,true);}
    finally{busy=false;list.removeAttribute('aria-busy');controls();}
  }
  syncButton.onclick=async()=>{
    if(syncButton.disabled)return;syncing=true;controls();announce('Reading a bounded page of recruiting mail. No email is sent.');
    try{const result=await api('/api/recruiting-feedback/sync',{});announce(`${result.added} new messages saved for review.${result.more_available?' More mail is available; sync again to continue.':''}`);await load(true);}
    catch(error){announce(error.message,true);}finally{syncing=false;controls();}
  };
  reload.onclick=()=>load();filter.onchange=()=>{offset=0;load();};
  previous.onclick=()=>{offset=Math.max(0,offset-25);load();};next.onclick=()=>{offset+=25;load();};
  panel.ontoggle=()=>{if(panel.open)load();};
  function open(){show('connections');panel.open=true;panel.querySelector('summary').focus();panel.scrollIntoView({block:'start',behavior:'auto'});}
  document.getElementById('open-recruiting-feedback').onclick=open;
  function render(value){
    const first=!snapshot;snapshot=value;controls();
    if(first&&panel.open)load();
  }
  window.recruitingFeedback={open,render};
  if(state)render(state);
})();

(() => {
  const panel=document.getElementById('prior-applications-panel'),list=document.getElementById('prior-list');
  const status=document.getElementById('prior-status'),form=document.getElementById('prior-scan-form');
  const scan=document.getElementById('prior-scan'),reload=document.getElementById('prior-reload');
  const restart=document.getElementById('prior-restart');
  const previous=document.getElementById('prior-previous'),next=document.getElementById('prior-next');
  let snapshot=null,busy=false,offset=0,result=null;
  function announce(text,error=false){status.textContent=text;status.setAttribute('role',error?'alert':'status');}
  function controls(){
    scan.disabled=busy||!snapshot||snapshot.demo||!snapshot.settings.gmail_prior_applications||!snapshot.gmail.connected;
    restart.disabled=scan.disabled;form.elements.company.disabled=busy;
    reload.disabled=busy;previous.disabled=busy||offset===0;next.disabled=busy||!result||offset+25>=result.total;
  }
  function card(message){
    const article=el('article',undefined,'artifact-row feedback-row');
    article.append(el('h3',message.subject||'Past application email'),el('p',`${message.sender} · ${date(message.received)} · ${message.state}`,'help'),el('p',message.excerpt));
    article.append(el('p',message.sender_verified?'Gmail reports an aligned sender signature. Review the original email and role before recording it.':'Sender authentication was not verified. Inspect the original email before recording it.','help'));
    const original=el('a','Open original in Gmail');original.href=`https://mail.google.com/mail/u/?authuser=${encodeURIComponent(snapshot.gmail.email||'')}#all/${encodeURIComponent(message.message_id)}`;original.target='_blank';original.rel='noopener noreferrer';article.append(original);
    const review=el('form',undefined,'form-grid'),label=el('label','Matching saved role'),select=el('select');
    select.name='job_id';const placeholder=el('option','Choose the exact role');placeholder.value='';select.append(placeholder);
    for(const job of message.candidates){const option=el('option',`${job.company} · ${job.title}`);option.value=job.id;select.append(option);}
    select.value=message.job_id||'';label.append(select);review.append(label);
    const identity=el('p',undefined,'help wide feedback-attempt');identity.setAttribute('aria-live','polite');
    const describe=()=>{const job=message.candidates.find(item=>item.id===select.value);identity.textContent=job?`${job.company} · ${job.title} · ${job.url} · Job ID: ${job.id}`:'Choose the saved role this email refers to. Existing uncertain applications remain held.';};select.onchange=describe;describe();review.append(identity);
    if(!message.candidates.length)review.append(el('p','No matching saved role yet. Keep this email; scan again after adding the role or confirming a company alias. You can also record Applied manually on an exact job after checking the original email.','help wide'));
    const actions=el('div',undefined,'actions wide'),confirm=el('button','Record past application'),dismiss=el('button','Dismiss email match','secondary');
    confirm.type='submit';dismiss.type='button';actions.append(confirm,dismiss);review.append(actions);
    const response=el('p',undefined,'help wide');response.setAttribute('role','status');review.append(response);
    async function save(state){
      if(snapshot.demo||review.dataset.saving==='true')return;
      if(state==='confirmed'&&!select.value){response.textContent='Choose the exact company and role before recording a past application.';response.setAttribute('role','alert');return;}
      review.dataset.saving='true';for(const input of review.elements)input.disabled=true;
      try{await api('/api/prior-applications/review',{id:message.id,revision:message.revision,state,job_id:select.value||null});announce(state==='confirmed'?'Past application recorded. The matching role will not be applied to again.':'Email match dismissed. Existing submission and uncertainty protections are unchanged.');await load(true);}
      catch(error){response.textContent=error.message;response.setAttribute('role','alert');}
      finally{review.dataset.saving='false';for(const input of review.elements)input.disabled=!!snapshot.demo;}
    }
    review.onsubmit=event=>{event.preventDefault();save('confirmed');};dismiss.onclick=()=>save('dismissed');
    for(const input of review.elements)input.disabled=!!snapshot.demo;
    article.append(review);return article;
  }
  async function load(preserve=false){
    if(busy||!snapshot)return;busy=true;controls();list.setAttribute('aria-busy','true');
    try{result=await api(`/api/prior-applications?offset=${offset}`);list.replaceChildren();for(const message of result.messages)list.append(card(message));
      if(!result.messages.length)list.append(el('p','No past application emails saved on this page. Enable checking Gmail for past applications, then scan a company or your application mail.','help'));
      document.getElementById('prior-page').textContent=result.total?`${offset+1}–${Math.min(offset+25,result.total)} of ${result.total} emails`:'No emails';
      if(!preserve)announce(`${result.total} application emails saved. Role-specific matches hold duplicate applications; company-only messages need review.`);
    }catch(error){announce(error.message,true);}finally{busy=false;list.removeAttribute('aria-busy');controls();}
  }
  async function scanMail(restartHistory=false){
    if(scan.disabled)return;busy=true;controls();announce('Scanning application mail. No email is sent.');
    try{const answer=await api('/api/prior-applications/scan',{company:form.elements.company.value.trim(),restart:restartHistory});announce(`${answer.processed} emails checked; ${answer.added} new application emails saved.${answer.more_available?' More mail remains. Scan again to continue.':' This scan is complete.'}`);busy=false;offset=0;await load(true);}
    catch(error){announce(error.message,true);}finally{busy=false;controls();}
  }
  form.onsubmit=event=>{event.preventDefault();scanMail();};restart.onclick=()=>scanMail(true);
  reload.onclick=()=>load();previous.onclick=()=>{offset=Math.max(0,offset-25);load();};next.onclick=()=>{offset+=25;load();};
  panel.ontoggle=()=>{if(panel.open)load();};
  document.getElementById('open-prior-applications').onclick=()=>{show('connections');panel.open=true;panel.querySelector('summary').focus();panel.scrollIntoView({block:'start',behavior:'auto'});};
  window.priorApplicationMail={render(value){const first=!snapshot;snapshot=value;controls();if(first&&panel.open)load();}};
  if(state)window.priorApplicationMail.render(state);
})();
