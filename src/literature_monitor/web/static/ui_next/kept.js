import {KeptPlayer} from './kept-player.js';
import {sceneReady,shots} from './kept/scene';
const $=id=>document.getElementById(id),bridge=window.inboxKept;
const reduced=matchMedia('(prefers-reduced-motion: reduce)'),dialog=$('kept-flow');
const ui={filter:'all',sort:0,selected:null,reader:false,listScroll:0,bodyScroll:0,bodyPaper:null,missingLinkPaper:false};
let data,metadata,active=null,lastRun=null,workflow=null,pollTimer=null,submitting=false;
const allEntries=()=>bridge.papers();
const key=entry=>entry.journal.key || entry.journal.name;
function groups() {
  const buckets=new Map(),ranks=new Map(data.journals.map((j,i)=>[j.key,i]));
  for(const entry of allEntries()) {
    const id=key(entry);
    if(!buckets.has(id)) buckets.set(id,{id,journal:entry.journal,entries:[]});
    buckets.get(id).entries.push(entry);
  }
  return [...buckets.values()].sort((a,b)=>(ranks.get(a.journal.key)??Infinity)-(ranks.get(b.journal.key)??Infinity));
}
function orderedEntries() {
  return groups().filter(g=>ui.filter==='all' || g.id===ui.filter).flatMap(g=>[...g.entries].sort((a,b)=>{
    if(ui.sort) return (ui.sort===1?1:-1)*(a.paper.title.localeCompare(b.paper.title) || a.paper.id.localeCompare(b.paper.id));
    return (b.paper.publication_date||'').localeCompare(a.paper.publication_date||'') ||
      (b.paper.discovered_at||'').localeCompare(a.paper.discovered_at||'') || a.paper.id.localeCompare(b.paper.id);
  }));
}
function refreshActions() {
  if(!data) return;
  const busy=bridge.busy() || Boolean(active) || submitting;
  $('kept-start').disabled=$('kept-import-only').disabled=busy || workflow?.active || !allEntries().length;
  $('kept-sort').disabled=busy;
  $('kept-reader-back').disabled=busy;
  for(const row of $('kept-journals').children)row.disabled=busy;
}
function renderRecord() {
  const results=lastRun?.results || [],exported=results.filter(r=>r.outcome==='exported').length;
  $('kept-record-count').textContent=lastRun?`${exported} / ${lastRun.total}`:'—';
  $('kept-record-summary').textContent=lastRun?.message || '本次尚未导入。';
  $('kept-record-items').replaceChildren(...results.map(result=>{
    const item=document.createElement('li'),title=document.createElement('strong'),reason=document.createElement('small');
    item.dataset.outcome=result.outcome;title.textContent=result.title;
    reason.textContent=result.outcome==='exported'
      ? (result.pdf_outcome==='verified failure'?'文献已导入，PDF 未保存，请在 Zotero 中查看':'导入成功，请在 Zotero 中查看')
      : result.reason;
    const details=document.createElement('details'),summary=document.createElement('summary'),raw=document.createElement('p');
    details.className='import-result-details';summary.textContent='详情';
    const pdf=result.pdf_outcome;
    raw.textContent=result.reason+(pdf && !result.reason.toLowerCase().includes(`pdf ${pdf}`)?`\nPDF: ${pdf}`:'');
    details.append(summary,raw);item.append(title,reason,details);return item;
  }));
}
function render() {
  const journals=groups();
  if(ui.filter!=='all' && !journals.some(g=>g.id===ui.filter)) ui.filter='all';
  const entries=orderedEntries(),total=allEntries().length;
  if(!entries.some(e=>e.paper.id===ui.selected)) ui.selected=ui.missingLinkPaper?null:(entries[0]?.paper.id || null);
  const choices=[{id:'all',name:'All Kept',count:total},...journals.map(g=>({id:g.id,name:g.journal.name,count:g.entries.length}))];
  $('kept-journals').replaceChildren(...choices.map(choice=>{
    const button=document.createElement('button');button.className='kept-manager-row';button.title=choice.name;
    button.setAttribute('aria-current',String(choice.id===ui.filter));
    for(const text of [choice.name,String(choice.count)]){const span=document.createElement('span');span.textContent=text;button.append(span);}
    button.addEventListener('click',()=>{
      if(bridge.busy())return;
      backReader();ui.filter=choice.id;ui.selected=null;ui.missingLinkPaper=false;ui.listScroll=0;render();
    });return button;
  }));
  const selected=choices.find(choice=>choice.id===ui.filter);
  $('kept-title').textContent=selected.name;$('kept-title').title=selected.name;
  $('kept-summary').textContent=`${entries.length} Papers`;
  $('kept-all-count').textContent=`ALL KEPT · ${total} PAPERS`;
  document.querySelector('.kept-card h2').textContent=total?'Ready to archive.':'Kept is clear.';
  $('kept-source').textContent='WORKSPACE';
  $('kept-sort').textContent=['SORT / DATE ↓','SORT / TITLE ↑','SORT / TITLE ↓'][ui.sort];
  const nodes=entries.map(entry=>{
    const button=document.createElement('button'),title=document.createElement('span');
    button.className='kept-paper-row';button.dataset.paperId=entry.paper.id;button.setAttribute('aria-current',String(entry.paper.id===ui.selected));
    button.dataset.withSource=String(ui.filter==='all');
    title.className='kept-paper-title';title.textContent=entry.paper.title;title.title=entry.paper.title;button.append(title);
    if(ui.filter==='all'){
      const source=document.createElement('small');source.textContent=entry.journal.name;button.append(source);
    }
    button.addEventListener('click',()=>openReader(entry.paper.id));return button;
  });
  $('kept-list').replaceChildren(...nodes);$('kept-list').hidden=ui.reader || !entries.length;
  if(!ui.reader)$('kept-list').scrollTop=ui.listScroll;
  $('kept-empty').hidden=entries.length>0 || ui.reader;
  $('kept-empty').textContent='尚无保留文献。审阅时点击 Keep，论文会出现在这里。';
  renderRecord();refreshActions();
}
function openReader(id,restore=false) {
  if(bridge.busy())return;
  if(!ui.reader)ui.listScroll=$('kept-list').scrollTop;
  ui.selected=id;ui.missingLinkPaper=false;ui.reader=true;
  $('kept-list').hidden=true;$('kept-empty').hidden=true;$('kept-reader').hidden=false;
  bridge.openReader(orderedEntries(),id);
  $('paper-detail').scrollTop=restore || ui.bodyPaper===id?ui.bodyScroll:0;
  $('paper-detail').focus({preventScroll:true});
}
function backReader() {
  if(bridge.busy())return false;
  if(ui.reader){ui.bodyScroll=$('paper-detail').scrollTop;ui.bodyPaper=ui.selected;bridge.closeReader();ui.reader=false;}
  $('kept-reader').hidden=true;render();return true;
}
function openKept() {
  if(!data || !metadata || !bridge.open())return;
  render();
  if(ui.reader && ui.selected)openReader(ui.selected,true);else $('kept-title').focus({preventScroll:true});
}
function inbox() {
  if(bridge.busy())return;
  if(!$('kept-view').hidden){
    if(ui.reader){ui.bodyScroll=$('paper-detail').scrollTop;ui.bodyPaper=ui.selected;}else ui.listScroll=$('kept-list').scrollTop;
    bridge.closeReader();$('kept-reader').hidden=true;bridge.close();
  } else bridge.showInbox();
}
function publishResults(state) {
  const batch=state.batch;
  const titles=new Map([...(active?.queue || []),...allEntries()].map(e=>[e.paper.id,e.paper.title]));
  const results=(batch?.results || []).map(result=>({...result,title:titles.get(result.paper_id) || data.titles[result.paper_id] || result.paper_id}));
  lastRun={total:batch?.total || active?.queue.length || results.length,results,message:state.message};
  renderRecord();
}
function identity(state) {return {workflow_id:state.workflow_id,generation:state.generation};}
function closeRun(run) {
  if(active!==run)return;
  run.player.stop();active=null;dialog.close();render();$('kept-title').focus({preventScroll:true});
  if(workflow?.navigation && !workflow.active)void uiNext.request('/api/kept-workflow/acknowledge',identity(workflow)).catch(error=>uiNext.notice(error.message));
}
function terminal(run) {
  if(active!==run || run.ending)return;
  run.ending=true;run.gate=null;$('kept-flow-actions').hidden=true;
  if(reduced.matches){closeRun(run);return;}
  // Continue a skipped WELCOME from its current frame rather than replaying it.
  void run.player.play('welcome',run.scanSkipped?run.player.liveTime:0);
}
function onClipEnd(run,clip) {
  if(active!==run)return;
  if(run.ending){if(clip==='welcome')closeRun(run);return;}
  if(clip==='access')run.accessDone=true;
  if(clip==='identity')run.identityDone=true;
  if(clip==='scan' && !run.scanSkipped)void run.player.play('scan-loop');
  reconcile(run);
}
function displayGate(run,state) {
  const preparation=state.preparation;
  const manual=new Map((preparation?.manual_services || []).map(service=>[service.id,service]));
  const messages=[state.waiting_for_login?'请手动切换到 Chrome 任务窗口完成机构登录。':state.message];
  if(state.phase==='transition')messages.push(`${Math.max(0,state.transition_deadline-Date.now()/1000).toFixed(1)} s`);
  for(const result of preparation?.results || []) {
    const service=preparation.services.find(s=>s.id===result.service_id);
    messages.push(`${service?.name || result.service_id}: ${result.outcome} · ${result.reason}`);
    if(service && result.outcome!=='Opened')manual.set(service.id,service);
  }
  $('kept-gate-message').textContent=messages.filter(Boolean).join('\n');
  $('kept-manual-services').replaceChildren(...[...manual.values()].map(service=>{
    const link=document.createElement(service.access_url?'a':'span');link.textContent=service.name+(service.access_url?' ↗':' · 未配置访问链接');
    if(service.access_url){link.href=service.access_url;link.target='_blank';link.rel='noopener noreferrer';}return link;
  }));
  $('kept-flow-gate').hidden=state.phase==='importing';
  $('kept-continue').hidden=!state.can_continue;$('kept-continue').disabled=submitting;
  $('kept-leave').disabled=submitting || !state.can_cancel;
}
function reconcile(run) {
  if(active!==run || run.ending)return;
  const state=run.state;
  displayGate(run,state);
  if(!state.active){terminal(run);return;}
  if(state.phase==='stopping'){closeRun(run);return;}
  if(run.scanSkipped)return;
  const manual=state.preparation?.manual_services?.length;
  if(!run.accessDone)return;
  // Ordinary explicit continuation holds ACCESS; Manual links hold PROCESSING.
  if(!manual && !run.continued && (!state.settings.auto_import_after_preparation || state.waiting_for_login) && !state.import_started)return;
  if(!run.identityDone){if(run.player.clip!=='identity')void run.player.play('identity');return;}
  if(state.phase==='importing') {
    if(!['scan','scan-loop'].includes(run.player.clip))void run.player.play('scan',state.mode==='import_only'?.32:0);
    $('kept-skip-slot').hidden=false;
  } else if(run.player.clip!=='processing')void run.player.play('processing');
}
function openWorkflow(state,restored=false) {
  if(active)return;
  const run=active={state,queue:[...allEntries()],accessDone:restored || state.mode==='import_only',
    identityDone:restored || state.mode==='import_only',continued:false,scanSkipped:false,ending:false};
  run.player=new KeptPlayer($('kept-film'),metadata,state.settings.display_name || 'LOCAL RESEARCHER',reduced.matches,clip=>onClipEnd(run,clip));
  $('kept-flow-actions').hidden=false;$('kept-flow-gate').hidden=true;$('kept-skip-slot').hidden=true;
  $('kept-skip').hidden=false;$('kept-skip-status').hidden=true;dialog.showModal();refreshActions();
  if(restored && state.phase==='waiting_for_user' && !state.preparation?.manual_services?.length && !state.import_started) {
    run.player.clip='access';run.player.draw(shots.access.duration);
  }
  if(run.accessDone)reconcile(run);else void run.player.play('access');
}
async function acceptState(state,{restored=false,open=false}={}) {
  workflow=state;publishResults(state);
  if(open && state.active && state.phase!=='stopping')openWorkflow(state,restored);
  if(active && active.state.workflow_id===state.workflow_id){active.state=state;reconcile(active);}
  const completed=(state.batch?.results || []).filter(r=>r.outcome==='exported').map(r=>r.paper_id);
  const previous=new Set(bridge.papers().map(e=>e.paper.id));
  bridge.exported(completed);
  if(completed.some(id=>previous.has(id)) || !state.active) {
    await window.uiWorkspace.refresh();data=bridge.data();render();
  }
  if(state.navigation && !active && !state.active) {
    await uiNext.request('/api/kept-workflow/acknowledge',identity(state));
  }
  refreshActions();
}
async function pollWorkflow() {
  clearTimeout(pollTimer);
  try{await acceptState(await uiNext.request('/api/kept-workflow/state'));}
  catch(error){uiNext.notice(`流程状态读取失败：${error.message}`);}
  if(workflow?.active)pollTimer=setTimeout(()=>void pollWorkflow(),1000);
}
async function start(mode) {
  if(active || submitting || workflow?.active || bridge.busy() || !allEntries().length)return;
  backReader();submitting=true;refreshActions();uiNext.notice('');
  try {
    // Always obtain the current target-bound one-use token before an explicit start.
    await window.uiWorkspace.refresh();data=bridge.data();
    const state=await uiNext.request('/api/kept-workflow/start',{mode,import_token:uiNext.data.import_token});
    await acceptState(state,{open:true});
    if(!state.active)uiNext.notice(state.message);
    void pollWorkflow();
  }catch(error){uiNext.notice(error.message);if(error.state?.workflow_id){await acceptState(error.state);void pollWorkflow();}}
  finally{submitting=false;refreshActions();if(active)displayGate(active,active.state);}
}
async function continueImport() {
  const run=active;if(!run || submitting || !run.state.can_continue)return;
  submitting=true;displayGate(run,run.state);
  try {
    const state=await uiNext.request('/api/kept-workflow/continue',identity(run.state));
    run.continued=true;await acceptState(state);void pollWorkflow();
  }catch(error){uiNext.notice(error.message);if(error.state?.workflow_id)await acceptState(error.state);}
  finally{submitting=false;if(active)displayGate(active,active.state);}
}
function skipScan() {
  const run=active;if(!run || run.scanSkipped || run.state.phase!=='importing')return;
  run.scanSkipped=true;$('kept-skip').hidden=true;$('kept-skip-status').hidden=false;$('kept-skip-status').textContent='等待导入完成…';
  void run.player.play('welcome',0,shots.welcome.holdAt);
}
async function leaveWorkflow() {
  const run=active;if(!run || submitting)return;
  if(run.ending){closeRun(run);return;}
  if(!run.state.can_cancel)return;
  const state=run.state;closeRun(run);submitting=true;refreshActions();
  try {await acceptState(await uiNext.request('/api/kept-workflow/cancel',identity(state)));void pollWorkflow();}
  catch(error){uiNext.notice(`停止请求未确认：${error.message}`);await restoreWorkflow();}
  finally{submitting=false;refreshActions();}
}
async function restoreWorkflow() {
  try {
    const state=await uiNext.request('/api/kept-workflow/state');workflow=state;
    if(state.active && !$('run-view').open)openKept();
    await acceptState(state,{restored:true,open:!$('run-view').open});
    if(state.active)void pollWorkflow();
  }catch(error){uiNext.notice(error.message);}
}
async function initialize() {
  data=bridge.data();metadata=await uiNext.request(new URL('assets/kept/clips.json',document.baseURI));
  await sceneReady();render();await restoreWorkflow();
  const initial=window.uiNextInitialLink;
  if(initial?.view==='kept'){
    const found=!initial.paper || allEntries().some(entry=>entry.paper.id===initial.paper);
    if(!found){ui.missingLinkPaper=true;ui.selected=null;}
    if($('kept-view').hidden)openKept();else render();
    if(initial.paper && found && !dialog.open && !$('kept-view').hidden)openReader(initial.paper);
    if(!found)uiNext.notice('该 Paper 不在当前 Kept 中，可能已移出保留列表。');
  }
}
if(bridge.available())void initialize();else window.addEventListener('inbox-ready',()=>void initialize(),{once:true});
$('kept-open').addEventListener('click',openKept);$('inbox-open').addEventListener('click',inbox);
$('kept-reader-back').addEventListener('click',()=>backReader());
$('kept-sort').addEventListener('click',()=>{if(backReader()){ui.sort=(ui.sort+1)%3;render();}});
$('kept-start').addEventListener('click',()=>void start('full'));$('kept-import-only').addEventListener('click',()=>void start('import_only'));
$('kept-continue').addEventListener('click',()=>void continueImport());$('kept-leave').addEventListener('click',()=>void leaveWorkflow());
$('kept-skip').addEventListener('click',skipScan);
window.addEventListener('enrich-state',refreshActions);
window.addEventListener('kept-data-changed',()=>{data=bridge.data();if(data && !$('kept-view').hidden){if(!ui.reader)ui.listScroll=$('kept-list').scrollTop;render();}});
window.addEventListener('kept-reader-empty',()=>{bridge.closeReader();ui.reader=false;$('kept-reader').hidden=true;});
window.addEventListener('kept-paper-selected',event=>{ui.selected=event.detail;ui.bodyPaper=event.detail;ui.bodyScroll=0;});
window.addEventListener('resize',()=>active?.player.resize());reduced.addEventListener('change',event=>active?.player.setReduced(event.matches));
window.addEventListener('pagehide',()=>{clearTimeout(pollTimer);active?.player.stop();});
dialog.addEventListener('cancel',event=>{event.preventDefault();void leaveWorkflow();});
$('run-view').addEventListener('close',()=>void restoreWorkflow());
document.addEventListener('visibilitychange',()=>{if(!document.hidden){active?.player.resume();if(workflow?.active)void pollWorkflow();}});
