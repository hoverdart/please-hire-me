"use strict";
(() => {
  const nodes = [document.querySelector('.ledger-panel'), ...['add-posting-panel', 'posting-import-panel', 'prepare-panel'].map(id => document.getElementById(id))];
  const today = document.getElementById('today');
  const jobList = document.getElementById('job-list-workspace');
  const dialog = document.getElementById('job-dialog');
  document.getElementById('job-detail-workspace').append(dialog);
  const strip = document.getElementById('worker-strip');
  strip.append(document.querySelector('.worker-indicator'), document.querySelector('.page-head .actions'));
  const model = document.getElementById('providers');
  model.classList.remove('view'); model.hidden = false;
  document.getElementById('connections').append(model);
  const paths = {
    today:'M3 4h18v16H3z M3 9h18 M8 2v4 M16 2v4',
    jobs:'M3 6h18v14H3z M8 6V3h8v3 M3 12h18 M10 12v3h4v-3',
    attention:'M12 3 2 21h20L12 3z M12 9v5 M12 17v1',
    materials:'M6 2h9l5 5v15H6z M15 2v6h5 M9 12h8 M9 16h8',
    profile:'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M4 22v-3a8 8 0 0 1 16 0v3',
    connections:'M8 8 5 5a3 3 0 0 0-4 4l4 4 M16 16l3 3a3 3 0 0 0 4-4l-4-4 M8 16l8-8',
    settings:'M3 6h18 M3 12h18 M3 18h18 M8 3v6 M16 9v6 M10 15v6',
    search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
    setup:'M4 3h16v18H4z M8 8h8 M8 12h8 M8 16h5'
  };
  for (const target of document.querySelectorAll('[data-icon]')) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('aria-hidden','true');
    const path = document.createElementNS(svg.namespaceURI,'path');
    path.setAttribute('d',paths[target.dataset.icon]); svg.append(path); target.append(svg);
  }
  let connectionsSignature = '', attentionSignature = '', healthSignature = '';
  let artifactOffset = 0, artifactBusy = false, artifactResult = null;
  let selectedJob = null, jobRequest = 0;
  const drafts = new Map();
  const pendingConnections = new Set();
  const pendingSuggestions = new Set();
  const artifactForm = document.getElementById('artifact-form');

  function placeLedger(name) {
    if (!['today','opportunities'].includes(name)) return;
    const destination = name === 'opportunities' ? jobList : today;
    for (const node of nodes) if (node.parentElement !== destination) destination.append(node);
  }
  function feedback(parent, message, error=false) {
    parent.textContent = message; parent.setAttribute('role',error?'alert':'status');
  }
  function renderConnections(snapshot) {
    const parent = document.getElementById('platform-connections');
    const signature = JSON.stringify([snapshot.platform_connections,snapshot.demo,snapshot.worker_running]);
    if (signature === connectionsSignature || pendingConnections.size || parent.querySelector('form[data-saving=true]') || [...parent.querySelectorAll('form')].some(f=>dirtyForms.has(f)||f.contains(document.activeElement))) return;
    connectionsSignature = signature;
    parent.replaceChildren();
    for (const connection of snapshot.platform_connections || []) {
      const section = el('section',undefined,'platform-connection section');
      section.dataset.connectionId = connection.id;
      const head = el('div',undefined,'section-head');
      head.append(el('h2',connection.name),el('span',connection.session_ready?'Signed in':connection.session_state==='connecting'?'Sign-in window open':'Sign-in needed','state'));
      section.append(head,el('p',connection.detail || 'Sign in on the computer running the worker. Connections start disabled.','help'));
      const form = el('form',undefined,'connection-controls');
      for (const [key,labelText] of [['enabled','Enable connection'],['discovery_enabled','Search automatically'],['native_apply_enabled','Apply automatically on this platform']]) {
        const label = el('label'); const input = el('input');
        input.type = 'checkbox'; input.name = key; input.checked = connection[key];
        input.disabled = snapshot.demo || snapshot.worker_running;
        label.append(input,document.createTextNode(labelText)); form.append(label);
      }
      const save = el('button','Save connection'); save.type='submit'; save.disabled=snapshot.demo||snapshot.worker_running;
      form.append(save); section.append(form);
      const capabilities=el('dl',undefined,'capability-list');
      for (const [key,label] of [['discovery','Automatic search'],['inspection','Posting inspection'],['preparation','Application preparation'],['submission','Native applications'],['verification','Outcome verification']]) {
        const item=el('div');item.append(el('dt',label),el('dd',connection.capabilities[key].message));capabilities.append(item);
      }
      section.append(capabilities);
      const actions=el('div',undefined,'actions');const statusLine=el('p',undefined,'help');
      for (const [action,label] of [['connect','Sign in'],['check','Check connection'],['discover','Search now'],['disconnect','Disconnect']]) {
        const button=el('button',label,'secondary');button.type='button';button.dataset.action=action;
        button.disabled=snapshot.demo||snapshot.worker_running||(action==='discover'&&(!connection.enabled||!connection.discovery_enabled||!connection.capabilities.discovery.available));
        button.onclick=async()=> {
          pendingConnections.add(connection.id);section.querySelectorAll('button,input').forEach(c=>c.disabled=true);
          feedback(statusLine,action==='connect'?'Opening the dedicated sign-in window…':'Working…');
          try {const result=await api(`/api/connections/${connection.id}/${action}`,{});feedback(statusLine,result.message||'Connection updated.');await refresh();}
          catch(error){feedback(statusLine,error.message,true);}
          finally{pendingConnections.delete(connection.id);restoreConnectionControls(section,connection.id);connectionsSignature='';renderConnections(state);}
        };actions.append(button);
      }
      form.onsubmit=async event=> {
        event.preventDefault();if(snapshot.demo||pendingConnections.has(connection.id))return;
        const data=Object.fromEntries([...form.querySelectorAll('input')].map(input=>[input.name,input.checked]));
        pendingConnections.add(connection.id);form.dataset.saving='true';section.querySelectorAll('button,input').forEach(c=>c.disabled=true);
        try{await api(`/api/connections/${connection.id}/configure`,data);saved(form);await refresh();note('Connection settings saved. Unverified capabilities remain unavailable.');}
        catch(error){feedback(statusLine,error.message,true);}
        finally{form.dataset.saving='false';pendingConnections.delete(connection.id);restoreConnectionControls(section,connection.id);if(!dirtyForms.has(form)){connectionsSignature='';renderConnections(state);} }
      };
      section.append(actions,statusLine,el('p',connection.checked?`Last checked: ${date(connection.checked)}`:'Not checked on this machine yet.','help'));
      const help=el('details');help.append(el('summary','Connect from the Pi or terminal'),el('p','On a Pi, open its desktop through Pi Connect and run this command there. Finish sign-in, then return here to check the connection.','help'));
      const pre=el('pre');pre.tabIndex=0;pre.append(el('code',connection.connect_command));help.append(pre);section.append(help);
      parent.append(section);
    }
  }
  function restoreConnectionControls(section,id) {
    const c=state.platform_connections.find(item=>item.id===id);
    for(const control of section.querySelectorAll('button,input'))control.disabled=state.demo||state.worker_running||(control.dataset.action==='discover'&&(!c.enabled||!c.discovery_enabled||!c.capabilities.discovery.available));
  }
  function render(snapshot) {
    renderConnections(snapshot);
    const health=document.getElementById('today-connections');
    const healthKey=JSON.stringify([snapshot.platform_connections,snapshot.provider,snapshot.gmail]);
    if(healthKey!==healthSignature){healthSignature=healthKey;health.replaceChildren();
      for(const c of snapshot.platform_connections||[]){const row=el('div',undefined,'health-row');row.append(el('strong',c.name),el('span',!c.enabled?'Disabled':!c.session_ready?'Sign-in needed':!c.capabilities.submission.available?'Validation needed':'Ready','help'));health.append(row);}
      const services=el('p','Model and email settings are available in Connections.','help');health.append(services);
    }
    const parent=document.getElementById('today-attention');
    const jobs=snapshot.jobs.filter(j=>j.requires_attention).sort((a,b)=>Number(['unknown','awaiting_verification'].includes(b.status))-Number(['unknown','awaiting_verification'].includes(a.status))).slice(0,5);
    const key=JSON.stringify([jobs,snapshot.summary?.question_count]);
    if(key!==attentionSignature){attentionSignature=key;parent.replaceChildren();
      if(snapshot.summary?.question_count){const button=el('button',`${snapshot.summary.question_count} unanswered application questions`,'attention-row secondary');button.onclick=()=>show('questions');parent.append(button);}
      for(const job of jobs){const button=el('button',undefined,'attention-row secondary');button.append(el('strong',`${job.company} · ${job.title}`),el('span',job.next_step||job.reason_label||'Review the application record.','help'));button.onclick=()=>openOpportunity(job);parent.append(button);}
      if(!parent.children.length)parent.append(el('p','No action needed in your loaded queue. Your desk will flag missing information or uncertain outcomes here.','help'));
    }
    for(const id of ['suggest-handshake','suggest-workatastartup'])document.getElementById(id).disabled=snapshot.demo||snapshot.worker_running||pendingSuggestions.has(id.replace('suggest-',''));
    for(const control of artifactForm.elements)control.disabled=snapshot.demo||artifactForm.dataset.saving==='true';
  }
  function artifactCard(artifact) {
    const card=el('article',undefined,'artifact-row');
    card.append(el('h3',`${artifact.company||'Profile'} · ${artifact.kind.replaceAll('_',' ')}`),el('p',`${artifact.title||'Platform profile'} · Revision ${artifact.revision} · ${date(artifact.created)}`,'help'));
    const text=el('textarea');text.readOnly=true;text.rows=5;text.value=artifact.content;text.setAttribute('aria-label','Generated '+artifact.kind.replaceAll('_',' '));card.append(text);
    const actions=el('div',undefined,'actions');const copy=el('button','Copy text','secondary');copy.type='button';
    copy.onclick=async()=>{try{await navigator.clipboard.writeText(artifact.content);note('Text copied.');}catch{text.focus();text.select();note('Text selected. Use your browser’s copy command.');}};actions.append(copy);
    if(['supplemental_response','cover_letter'].includes(artifact.kind)){const download=el('button',artifact.kind==='cover_letter'?'Download cover letter':'Download response PDF','secondary');download.type='button';download.onclick=async()=>{
      download.disabled=true;
      try{const response=await fetch(`/api/artifact-document/${artifact.id}/${artifact.hash}`,{headers:{'X-Hireme-Token':token}});if(!response.ok){const error=await response.json();throw new Error(error.error);}
        const url=URL.createObjectURL(await response.blob());const link=el('a');link.href=url;link.download=artifact.kind==='cover_letter'?'cover-letter.pdf':'application-response.pdf';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      }catch(error){note(error.message,true);}finally{download.disabled=false;}
    };actions.append(download);}
    card.append(actions);return card;
  }
  async function loadArtifacts() {
    if(artifactBusy)return;artifactBusy=true;document.getElementById('artifact-previous').disabled=true;document.getElementById('artifact-next').disabled=true;document.getElementById('artifact-library').setAttribute('aria-busy','true');
    try{const result=await api('/api/artifacts?offset='+artifactOffset);artifactResult=result;const parent=document.getElementById('artifact-library');parent.replaceChildren();
      if(!result.artifacts.length)parent.append(el('p','No generated materials yet. Open a job and choose Prepare application materials.','help'));
      result.artifacts.forEach(a=>parent.append(artifactCard(a)));
      document.getElementById('artifact-page').textContent=result.total?`${result.offset+1}–${Math.min(result.offset+result.limit,result.total)} of ${result.total} materials`:'No generated materials';
    }catch(error){artifactOffset=artifactResult?.offset||0;note(error.message,true);}finally{artifactBusy=false;document.getElementById('artifact-library').removeAttribute('aria-busy');document.getElementById('artifact-previous').disabled=!artifactResult||artifactResult.offset===0;document.getElementById('artifact-next').disabled=!artifactResult||artifactResult.offset+artifactResult.limit>=artifactResult.total;}
  }
  async function openJob(job) {
    selectedJob=job;const request=++jobRequest;
    const context=document.getElementById('connected-job-context');context.replaceChildren(el('h3','Application route'));
    context.append(el('p',`Apply through ${job.application_destination||job.host}`,'help'));
    for(const origin of job.origins||[]){const link=el('a',origin.connection_id==='handshake'?'Handshake listing':'Startup listing');link.href=origin.listing_url;link.target='_blank';link.rel='noopener noreferrer';context.append(link);}
    for(const conflict of job.identity_conflicts||[]){const candidate=conflict.candidate;context.append(el('h3','Identity review required'),el('p',conflict.detail,'help'),el('p',`${candidate.company} · ${candidate.title} · ${candidate.url}`,'help'));}
    if(job.application_capability&&!job.application_capability.available)context.append(el('p',job.application_capability.message,'help'));
    for(const question of state.questions.filter(q=>q.job_id===job.id)){const button=el('button',question.label,'secondary');button.type='button';button.onclick=async()=>{show('questions');if(!questionDrafts().length){document.getElementById('question-search').value=job.company;await loadQuestionLedger(true,true);}};context.append(el('p','Unresolved requirement: '+question.label,'help'),button);}
    const requirements=job.requirements||jobPayload(job).requirements||[];
    if(requirements.length){context.append(el('h3','Required materials'));for(const item of requirements)context.append(el('p',item.label||String(item),'help'));}
    const draft=drafts.get(job.id);artifactForm.elements.kind.value=draft?.kind||'introduction';artifactForm.elements.prompt.value=draft?.prompt||`Share something about you, what you are looking for, and why ${job.company} interests you for this ${job.title} role.`;
    writingPurpose();document.getElementById('artifact-status').textContent='';
    const record=(ledgerState?.applications||state.applications).find(a=>a.job_id===job.id);
    const evidence=document.getElementById('job-record-evidence');evidence.replaceChildren();evidence.dataset.loaded='false';evidence.dataset.loading='false';evidence.dataset.applicationId=record?.id||'';
    const recordPanel=document.getElementById('job-record-panel');recordPanel.open=false;
    recordPanel.ontoggle=()=>{if(recordPanel.open){if(record)loadEvidence(record,evidence);else evidence.replaceChildren(el('p','No application package has been recorded for this job.','help'));}};
    document.getElementById('job-artifacts').replaceChildren(el('p','Loading this job’s materials…','help'));
    try{const result=await api('/api/artifacts?job_id='+encodeURIComponent(job.id));if(request!==jobRequest)return;const parent=document.getElementById('job-artifacts');parent.replaceChildren();result.artifacts.forEach(a=>parent.append(artifactCard(a)));if(!result.total)parent.append(el('p','No materials generated for this job yet.','help'));}
    catch(error){if(request===jobRequest)feedback(document.getElementById('job-artifacts'),error.message,true);}
  }
  function writingPurpose(){const cover=artifactForm.elements.kind.value==='cover_letter';artifactForm.elements.prompt.closest('label').hidden=cover;artifactForm.elements.prompt.required=!cover;}
  artifactForm.elements.kind.addEventListener('change',writingPurpose);
  artifactForm.oninput=()=>{if(selectedJob)drafts.set(selectedJob.id,{kind:artifactForm.elements.kind.value,prompt:artifactForm.elements.prompt.value});};
  artifactForm.onsubmit=async event=>{
    event.preventDefault();if(!selectedJob||state.demo||artifactForm.dataset.saving==='true')return;
    const id=selectedJob.id;const data={job_id:id,kind:artifactForm.elements.kind.value,prompt:artifactForm.elements.prompt.value};
    artifactForm.dataset.saving='true';for(const c of artifactForm.elements)c.disabled=true;
    feedback(document.getElementById('artifact-status'),'Generating and checking source support…');
    try{const result=await api('/api/artifacts/generate',data);if(selectedJob?.id===id){document.getElementById('job-artifacts').prepend(artifactCard(result));if(drafts.get(id)?.kind===data.kind&&drafts.get(id)?.prompt===data.prompt)drafts.delete(id);feedback(document.getElementById('artifact-status'),'Material saved. Review the response before using it.');}await refresh();}
    catch(error){if(selectedJob?.id===id)feedback(document.getElementById('artifact-status'),error.message,true);}
    finally{artifactForm.dataset.saving='false';render(state);}
  };
  for(const connectionId of ['handshake','workatastartup'])document.getElementById('suggest-'+connectionId).onclick=async event=>{
    const button=event.currentTarget;pendingSuggestions.add(connectionId);button.disabled=true;const status=document.getElementById('profile-suggestion-status');feedback(status,'Drafting from your approved sources…');
    try{const result=await api(`/api/connections/${connectionId}/profile-suggestion`,{});const target=document.getElementById('profile-suggestion');target.value=result.content;target.hidden=false;document.getElementById('copy-profile-suggestion').hidden=false;feedback(status,'Draft saved. Review it before updating the platform yourself.');}
    catch(error){feedback(status,error.message,true);}finally{pendingSuggestions.delete(connectionId);button.disabled=state.demo||state.worker_running;}
  };
  document.getElementById('copy-profile-suggestion').onclick=async()=>{const target=document.getElementById('profile-suggestion');try{await navigator.clipboard.writeText(target.value);note('Suggestion copied.');}catch{target.focus();target.select();note('Suggestion selected. Use your browser’s copy command.');}};
  document.getElementById('reload-artifacts').onclick=loadArtifacts;
  document.getElementById('artifact-previous').onclick=()=>{artifactOffset=Math.max(0,artifactOffset-25);loadArtifacts();};
  document.getElementById('artifact-next').onclick=()=>{artifactOffset+=25;loadArtifacts();};
  document.getElementById('open-attention').onclick=()=>show('questions');
  document.getElementById('open-connections').onclick=()=>show('connections');
  for(const id of ['connection-source','connection-destination','connection-fit'])document.getElementById(id).onchange=()=>loadLedger(true);
  document.querySelector('[data-view=materials]').addEventListener('click',loadArtifacts);
  window.addEventListener('beforeunload',event=>{if(drafts.size){event.preventDefault();event.returnValue='';}});
  window.connectedWorkspace={render,placeLedger,openJob,loadArtifacts};
  if(state)render(state);
})();
