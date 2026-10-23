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
    if(busy)return;busy=true;controls();list.setAttribute('aria-busy','true');
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
  window.recruitingFeedback={open,render(value){snapshot=value;controls();}};
})();
