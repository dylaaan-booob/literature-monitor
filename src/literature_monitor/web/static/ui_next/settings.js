// Prototype-derived configuration UI. Production operations follow SPEC §63.
// RhinE / RhineLabUI MIT adaptation; see SOURCES.md and licenses/.

const root=document.getElementById('settings-view');
root.innerHTML=`
  <header class="s-header">
    <label class="s-header-search"><input id="s-search" type="search" placeholder="SEARCH / JOURNALS & ACCESS" aria-label="Settings search" autocomplete="off" disabled><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8" cy="8" r="4.5"></circle><path d="m11.3 11.3 4.5 4.5"></path></svg></label>
    <div class="s-header-actions"><span id="s-dirty" class="s-dirty">Saved baseline</span>
      <div class="s-files"><button id="s-files" class="s-button" aria-expanded="false" aria-controls="s-file-menu">FILES ↓</button>
        <div id="s-file-menu" class="s-file-menu" popover="auto" aria-label="Files">
          <button data-file-action="import">Import complete list.md</button>
          <button data-file-action="export">Export saved list.md</button>
          <button data-file-action="pack">Export AI Mapping Pack</button>
          <button data-file-action="reload">Reload Settings from Disk</button>
          <button id="s-upgrade" data-file-action="upgrade" hidden>Legacy Schema Upgrade</button>
        </div>
      </div><button id="s-save" class="s-button s-primary">SAVE ↗</button>
    </div>
  </header>
  <p id="s-message" class="s-message" role="status" aria-live="polite" hidden></p>
  <div class="s-content">
    <section id="s-workflow" class="s-workflow-section" aria-label="Workflow">
      <div class="s-workflow">
          <label>Monitor Name<input data-monitor="name" autocomplete="off"></label>
          <label>Keyword Expression<input data-monitor="expression" autocomplete="off" spellcheck="false"></label>
          <div class="s-field-row"><label>Publication Start Month<input type="month" data-monitor="from"></label><label>Publication End Month<input type="month" data-monitor="to"></label></div>
          <label>Output Workspace<input data-monitor="workspace" autocomplete="off" spellcheck="false"></label>
          <label>Log Level<select data-monitor="log"><option>DEBUG</option><option>INFO</option><option>WARNING</option><option>ERROR</option></select></label>
          <label class="s-check"><input type="checkbox" data-monitor="autoImport">Kept Auto Import after Preparation</label>
          <div class="s-field-row"><label>Transition Delay / seconds<input type="number" data-monitor="delay" min="0" step="any"></label><label>Display Name<input data-monitor="displayName" maxlength="120" autocomplete="off"></label></div>
      </div>
    </section>
    <section id="s-collection" class="s-panel s-editor-grid" aria-labelledby="journals-open" hidden>
      <aside class="s-directory" aria-label="Journal Groups"><div class="s-directory-heading"><span>GROUPS</span><span id="s-group-count" hidden></span></div><div id="s-groups" class="s-directory-list"></div>
        <div class="s-directory-tools"><button id="s-new-group" class="s-button">+ NEW GROUP</button><div class="s-actions"><button id="s-rename-group" class="s-button">Rename</button><button id="s-group-up" class="s-button" aria-label="Move Group up">↑</button><button id="s-group-down" class="s-button" aria-label="Move Group down">↓</button><button id="s-delete-group" class="s-button">Delete</button></div></div>
      </aside>
      <div class="s-browser"><div class="s-browser-head"><div class="s-browser-title"><h2 id="s-collection-title">All Journals</h2><div class="s-actions"><button id="s-covers-toggle" class="s-button" aria-expanded="false" aria-controls="s-cover-panel">COVERS <span id="s-cover-summary"></span> ↓</button><button id="s-add-journal" class="s-button">+ JOURNAL</button></div></div>
        <label class="s-check s-select-visible s-browser-meta"><input id="s-select-journals" type="checkbox" aria-label="Select all visible Journals">Select visible · <span id="s-visible-journals"></span></label>
        <div id="s-cover-panel" hidden><div class="s-cover-job"><div class="s-context-title">COVER RESOURCES / INDEPENDENT</div><p id="s-cover-job-status" class="s-cover-status" role="status"></p><progress id="s-cover-progress" value="0" max="1" aria-label="Cover operation progress"></progress><div class="s-cover-controls"><button id="s-cover-missing" class="s-button">Search missing</button><button id="s-cover-continue" class="s-button">Continue</button><button id="s-cover-retry" class="s-button">Retry</button><button id="s-cover-cancel" class="s-button">Cancel</button><button id="s-cover-reset" class="s-button">Reset</button></div></div></div>
      </div><div id="s-journals" class="s-journal-grid"></div></div>
      <aside class="s-context" aria-label="Journal context"><div id="s-journal-detail"></div>
        <div id="s-journal-assignment" class="s-assignment" hidden><div id="s-journal-selection" class="s-selection" role="status"></div><label>Group<select id="s-group-target" aria-label="Assign Journals to Group"></select></label><div class="s-actions"><button id="s-assign-journals" class="s-button">Assign</button><button id="s-clear-journals" class="s-button">Clear selection</button></div></div><div id="s-journal-actions" hidden></div>
      </aside>
    </section>
    <section id="s-access" class="s-panel s-editor-grid" aria-labelledby="access-open" hidden>
      <aside class="s-directory" aria-label="Access Services"><div class="s-directory-heading"><span>SERVICES</span><span id="s-service-count" hidden></span></div><div id="s-services" class="s-directory-list"></div><div class="s-directory-tools"><button id="s-new-service" class="s-button">+ NEW SERVICE</button><div class="s-actions"><button id="s-rename-service" class="s-button">Rename</button><button id="s-service-up" class="s-button" aria-label="Move Service up">↑</button><button id="s-service-down" class="s-button" aria-label="Move Service down">↓</button><button id="s-delete-service" class="s-button">Delete</button></div></div></aside>
      <div class="s-browser"><div class="s-browser-head"><div class="s-browser-title"><h2 id="s-access-title">All Publishers</h2></div>
        <label class="s-check s-select-visible s-browser-meta"><input id="s-select-publishers" type="checkbox" aria-label="Select all visible Publishers">Select visible · <span id="s-visible-publishers"></span></label></div><div id="s-publishers" class="s-publisher-list"></div></div>
      <aside class="s-context" aria-label="Access context"><div id="s-service-detail"></div><div id="s-publisher-detail" hidden></div>
        <div id="s-publisher-assignment" class="s-assignment" hidden><div id="s-publisher-selection" class="s-selection" role="status"></div><label>Default Service<select id="s-service-target" aria-label="Assign Publishers to Service"></select></label><div class="s-actions"><button id="s-assign-publishers" class="s-button">Assign</button><button id="s-clear-publishers" class="s-button">Clear selection</button></div></div>
        <div id="s-institution" class="s-institution"><label>Institution name / public identifier<input data-monitor="institutionName" maxlength="120" autocomplete="off"></label><label>Public IdP entity ID<input data-monitor="idp" placeholder="HTTPS entity ID / URN" autocomplete="off" spellcheck="false"></label><label class="s-check"><input type="checkbox" data-monitor="consent">Automatic Attribute Release Consent</label></div>
      </aside>
    </section>
    <section id="s-system" class="s-system" aria-label="System">
      <div class="s-system-intro"><h2 class="s-system-title">System</h2></div>
      <details><summary>Zotero Integration</summary><div class="s-diagnostic"><p id="s-zotero-result">Not checked. Connector 与 Desktop 状态未检查。</p><button data-diagnostic="zotero" class="s-button">Refresh</button></div></details>
      <details><summary>Workspace Health</summary><div class="s-diagnostic"><p id="s-health-result">Not checked. 使用已保存路径。</p><button data-diagnostic="health" class="s-button">Refresh</button></div></details>
      <details><summary>Current Run</summary><div class="s-diagnostic"><p id="s-run-result">Not checked.</p><button data-diagnostic="run" class="s-button">Refresh</button></div></details>
      <div class="s-reset"><div><span class="s-eyebrow">WORKSPACE RESET / SEPARATE ACTION</span><h2>Reset saved workspace</h2><code id="s-reset-path"></code></div><div class="s-reset-confirm"><label>Type RESET<input id="s-reset-word" autocomplete="off" spellcheck="false" placeholder="RESET"></label><div class="s-actions"><button id="s-reset-cancel" class="s-button">Cancel</button><button id="s-reset-workspace" class="s-button s-primary" disabled>CONFIRM RESET ↗</button></div><p id="s-reset-result" class="s-help" role="status">尚未执行 Reset。</p></div></div>
    </section>
    <section id="s-file-preview" class="s-file-preview" hidden aria-label="Full file preview"><div class="s-preview-head"><div><span class="s-eyebrow">FILES / TRANSIENT PREVIEW</span><h2 id="s-preview-title"></h2></div><span id="s-preview-file" class="s-eyebrow"></span></div><div class="s-preview-body"><p id="s-preview-summary" class="s-preview-summary"></p><div id="s-preview-columns" class="s-preview-columns"></div></div><div id="s-preview-actions" class="s-preview-actions"></div></section>
  </div>
  <dialog id="s-dialog" class="s-dialog" aria-labelledby="s-dialog-title"><h2 id="s-dialog-title"></h2><div id="s-dialog-body" class="s-dialog-body"></div><div id="s-dialog-actions" class="s-actions"></div></dialog>`;

const $=id=>document.getElementById(id);
const shell=$('settings-shell'),header=root.querySelector('.s-header');
const toolbar=header.querySelector('.s-header-actions');
toolbar.prepend(toolbar.querySelector('.s-files'),toolbar.querySelector('#s-save'));
shell.append(header.querySelector('.s-header-search'));header.remove();
// Move the original forms and diagnostics once; editing never rebuilds them.
const settingsPage=document.createElement('section');
settingsPage.id='s-settings';settingsPage.className='s-panel s-settings-grid';settingsPage.hidden=true;settingsPage.setAttribute('aria-labelledby','settings-open');
settingsPage.innerHTML='<div class="s-settings-main"></div><aside class="s-system-sidebar" aria-label="Diagnostics and maintenance"></aside>';
root.querySelector('.s-content').prepend(settingsPage);
toolbar.classList.add('s-settings-tools');
settingsPage.querySelector('.s-system-sidebar').append(toolbar);
settingsPage.querySelector('.s-settings-main').append($('s-workflow'),$('s-institution'));
// Keep each original control inside its label for binding and inline validation.
const workflow=$('s-workflow').querySelector('.s-workflow');
const keys=['name','displayName','expression','from','to','workspace','log','delay','autoImport','institutionName','idp','consent'];
const labels=keys.map(key=>{
  const input=root.querySelector(`[data-monitor="${key}"]`),label=input.parentElement;
  const caption=document.createElement('span');caption.className='s-setting-label';
  caption.textContent=[...label.childNodes].filter(node=>node.nodeType===Node.TEXT_NODE).map(node=>node.textContent).join('').trim();
  label.dataset.field=key;label.classList.add('s-setting-row');label.replaceChildren(caption,input);
  return label;
});
const monitorGroup=document.createElement('fieldset'),workflowGroup=document.createElement('fieldset');
monitorGroup.className=workflowGroup.className='s-setting-group';
monitorGroup.innerHTML='<legend>Monitor</legend>';workflowGroup.innerHTML='<legend>Workflow</legend>';
monitorGroup.append(...labels.slice(0,5));workflowGroup.append(...labels.slice(5,9));
workflow.replaceChildren(monitorGroup,workflowGroup);
$('s-institution').classList.add('s-setting-group');
$('s-institution').replaceChildren(Object.assign(document.createElement('h2'),{className:'s-group-label',textContent:'Institution'}),...labels.slice(9));
for(const key of ['expression','workspace','autoImport','consent'])root.querySelector(`[data-monitor="${key}"]`).parentElement.classList.add('s-field-wide');
root.querySelector('[data-monitor="expression"]').title='Supports AND / OR. NOT is not supported.';
root.querySelector('[data-monitor="to"]').title='End month must not precede start month.';
root.querySelector('[data-monitor="delay"]').title='Transition delay must be greater than zero.';
root.querySelector('[data-monitor="consent"]').title='Opt in to automatic attribute-release consent during Preparation. May release personal attributes. ';
const dirtyMarker=document.createElement('span');dirtyMarker.className='nav-dirty';dirtyMarker.hidden=true;dirtyMarker.setAttribute('aria-hidden','true');$('settings-open').append(dirtyMarker);
settingsPage.querySelector('.s-system-sidebar').append($('s-system'));
for(const button of root.querySelectorAll('[data-diagnostic]')){
  const summary=button.closest('details').querySelector('summary'),name=document.createElement('span'),status=document.createElement('span');
  name.className='s-diagnostic-name';name.textContent=summary.textContent;
  status.id=`s-${button.dataset.diagnostic}-summary`;status.className='s-diagnostic-summary';
  status.textContent='Not checked';
  summary.replaceChildren(name,status);
}
const reset=root.querySelector('.s-reset'),resetDisclosure=document.createElement('details'),resetSummary=document.createElement('summary');
resetDisclosure.id='s-reset-section';resetDisclosure.className='s-reset';
resetSummary.append(Object.assign(document.createElement('span'),{className:'s-diagnostic-name',textContent:'Workspace Reset'}),$('s-reset-path'));
resetDisclosure.append(resetSummary,reset.querySelector('.s-reset-confirm'));reset.replaceWith(resetDisclosure);

const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const copy=value=>structuredClone(value);
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
const views=['collection','access','settings'];
const navigation={collection:'journals',access:'access',settings:'settings'};
const transitions=new Map(views.map(view=>[view,new SurfaceTransition($('s-'+view),$('s-'+view),180,180)]));
let saved=null,draft=null,view='settings',preview=null,revision=0,loading=true,operationPending=false;
let serverState=null;
const ui={group:'all',service:'all',journal:null,publisher:null,accessDetail:'service',journalAssignment:{scope:null,target:''},publisherAssignment:{scope:null,target:''},search:{collection:'',access:''},journals:new Set(),publishers:new Set()};
const covers=new Map();
let coverJob={phase:'Idle',completed:0,total:0,failed:0,busy:false,supported:false};
let coverTimer=null;
const dialog=$('s-dialog');let dialogFocus=null,dialogVersion=0;
const dirty=()=>Boolean(saved && draft && !same(saved,draft));

const list=()=>draft.list;
const journal=id=>list().journals.find(j=>j.id===id);
const publisher=id=>list().publishers.find(p=>p.id===id);
const service=id=>list().services.find(s=>s.id===id);
const group=id=>list().groups.find(g=>g.id===id);
const groupName=id=>group(id)?.name || 'Ungrouped / virtual';
const serviceName=id=>service(id)?.name || 'Unassigned';

function message(text,tone='normal') {$('s-message').textContent=text;$('s-message').dataset.tone=tone;$('s-message').hidden=!text;}
function updateDirty() {
  $('s-files').disabled=operationPending;
  for(const button of $('s-file-menu').querySelectorAll('[data-file-action]'))button.disabled=loading && button.dataset.fileAction!=='reload';
  $('s-dirty').textContent=dirty()?'Unsaved Changes':'Saved baseline';$('s-dirty').dataset.dirty=String(dirty());
  const unsaved=dirty();dirtyMarker.hidden=!unsaved;
  $('settings-open').setAttribute('aria-label',unsaved?'05 Settings · Unsaved changes. Open Settings to save.':'05 Settings');
  $('s-save').disabled=loading || operationPending || !unsaved || Boolean(preview);
  $('s-upgrade').hidden=!serverState?.needs_access_upgrade;
  $('s-reset-path').textContent=serverState?.saved_workspace || 'Unavailable';
  syncSearch();
}
function changed() {revision++;updateDirty();}
function showDialog(title,body,choices) {
  if(dialog.open)dialog.close();
  const version=++dialogVersion;
  dialogFocus=document.activeElement;$('s-dialog-title').textContent=title;$('s-dialog-body').innerHTML=body;
  $('s-dialog-actions').replaceChildren();
  for(const {label,run,primary=false} of choices) {
    const button=document.createElement('button');button.className='s-button'+(primary?' s-primary':'');button.textContent=label;
    button.addEventListener('click',async()=>{button.disabled=true;try {if(await run?.()!==false && dialogVersion===version && dialog.open)dialog.close();}catch(error){message(error.message,'error');}finally{button.disabled=false;}});$('s-dialog-actions').append(button);
  }
  dialog.showModal();
}
const cancel={label:'Cancel'};
dialog.addEventListener('close',()=>{
  if(dialog.open)return;
  const target=dialogFocus?.isConnected && !dialogFocus.disabled && !dialogFocus.closest('[hidden]')?dialogFocus:$('settings-open');
  if(!root.hidden)target.focus({preventScroll:true});
});
function confirm(title,text,run,label='Confirm') {showDialog(title,`<p>${esc(text)}</p>`,[cancel,{label,primary:true,run}]);}
function memberList(items) {return `<ul>${items.map(name=>`<li>${esc(name)}</li>`).join('')}</ul>`;}
function closeMenu() {
  if($('s-file-menu').matches(':popover-open'))$('s-file-menu').hidePopover();
  $('s-files').setAttribute('aria-expanded','false');
}
$('s-file-menu').addEventListener('toggle',event=>$('s-files').setAttribute('aria-expanded',String(event.newState==='open')));
settingsPage.addEventListener('scroll',closeMenu);
settingsPage.querySelector('.s-system-sidebar').addEventListener('scroll',closeMenu);
window.addEventListener('resize',closeMenu);
const scrollPositions=new Map();
function pageScrollers() {
  const panel=$('s-'+view);
  return [panel,...panel.querySelectorAll('.s-directory-list,.s-journal-grid,.s-publisher-list,.s-context,.s-settings-main,.s-system-sidebar,#s-cover-panel')];
}
function rememberScroll() {
  if(!root.hidden && !preview)for(const node of pageScrollers())scrollPositions.set(node,[node.scrollLeft,node.scrollTop]);
}
function restoreScroll() {
  for(const node of pageScrollers()){
    const position=scrollPositions.get(node);if(position){node.scrollLeft=position[0];node.scrollTop=position[1];}
  }
}
function switchView(next,instant=false) {
  if(preview || next===view)return;
  rememberScroll();transitions.get(view).dispose();$('s-'+view).hidden=true;
  view=next;syncSearch();
  transitions.get(view).show(instant || reduced());closeMenu();
}
function openSettings(next='settings',instant=false) {
  if(dialog.open || preview || operationPending)return false;
  const entering=root.hidden || next!==view;
  if(!window.uiNextSettings.open(navigation[next]))return false;
  switchView(next,instant);
  root.hidden=false;shell.hidden=false;$('s-'+view).hidden=false;
  document.body.dataset.settingsOpen='true';updateDirty();if(entering)restoreScroll();
  return true;
}
for(const name of views) $(navigation[name]+'-open').addEventListener('click',event=>openSettings(name,event.detail===0));
for(const name of ['inbox','kept']) $(''+name+'-open').addEventListener('click',event=>{
  if(!window.uiNextSettings.active())return;
  if(dialog.open || operationPending){event.stopImmediatePropagation();return;}
  if(preview){message('请先 Confirm 或 Cancel 当前文件 Preview。');event.stopImmediatePropagation();return;}
  rememberScroll();root.hidden=true;shell.hidden=true;delete document.body.dataset.settingsOpen;closeMenu();
  const origin=window.uiNextSettings.close();
  if(name===origin){event.stopImmediatePropagation();$(name+'-open').focus({preventScroll:true});}
},true);
for(const surface of [root,shell])surface.addEventListener('keydown',event=>{
  if(event.key==='Escape' && !dialog.open){if(preview)closePreview();else if($('s-file-menu').matches(':popover-open')){closeMenu();$('s-files').focus({preventScroll:true});}}
});
$('s-files').addEventListener('click',()=>{
  const menu=$('s-file-menu');
  if(menu.matches(':popover-open')){closeMenu();return;}
  const anchor=$('s-files').getBoundingClientRect();
  menu.style.top=`${anchor.bottom+8}px`;
  menu.style.left=`${Math.max(18,Math.min(anchor.left,innerWidth-318))}px`;
  menu.style.maxHeight=`${Math.max(80,innerHeight-anchor.bottom-26)}px`;
  menu.showPopover();$('s-files').setAttribute('aria-expanded','true');menu.querySelector('button').focus();
});
document.addEventListener('pointerdown',event=>{if(!event.target.closest('.s-files'))closeMenu();});

function syncSearch() {
  const input=$('s-search'),enabled=['collection','access'].includes(view) && !preview;
  input.disabled=!enabled || loading;input.value=enabled?ui.search[view]:'';
  input.placeholder=enabled?(view==='collection'?'SEARCH JOURNALS':'SEARCH PUBLISHERS'):'SEARCH / JOURNALS & ACCESS';
  input.setAttribute('aria-label',enabled?(view==='collection'?'Search Journals by name, ISSN-L or Publisher':'Search all Publishers by name or ID'):'Search available in Journals and Access');
}
function searchCurrentView() {
  ui.search[view]=$('s-search').value;
  if(view==='collection')renderJournals();else {renderServices();renderPublishers();}
}
$('s-search').addEventListener('input',searchCurrentView);
$('s-search').addEventListener('keydown',event=>{if(event.key==='Escape'){$('s-search').value='';searchCurrentView();event.preventDefault();}});

function syncMonitor() {
  root.querySelectorAll('[data-monitor]').forEach(input=>{const value=draft.monitor[input.dataset.monitor];if(input.type==='checkbox')input.checked=value;else input.value=value;});
}
root.querySelectorAll('[data-monitor]').forEach(input=>input.addEventListener(input.tagName==='SELECT' || input.type==='checkbox'?'change':'input',()=>{
  draft.monitor[input.dataset.monitor]=input.type==='checkbox'?input.checked:input.value;
  changed();
}));
function directoryRow(id,name,count,current,kind) {
  return `<button class="kept-manager-row s-directory-row" data-${kind}="${esc(id)}" aria-current="${current===id}" ${id!=='all' && id!=='none' && kind==='group'?'draggable="true"':''}><span>${esc(name)}</span><span>${count}</span></button>`;
}
function renderGroups() {
  const container=$('s-groups'),scroll=container.scrollTop,horizontal=container.scrollLeft,focused=document.activeElement?.dataset.group;
  container.innerHTML=directoryRow('all','All Journals',list().journals.length,ui.group,'group')+directoryRow('none','Ungrouped / virtual',list().journals.filter(j=>!j.groupId).length,ui.group,'group')+list().groups.map(g=>directoryRow(g.id,g.name==='Ungrouped'?'Ungrouped / named Group':g.name,list().journals.filter(j=>j.groupId===g.id).length,ui.group,'group')).join('');
  container.scrollTop=scroll;container.scrollLeft=horizontal;
  if(focused)container.querySelector(`[data-group="${CSS.escape(focused)}"]`)?.focus({preventScroll:true});
  $('s-group-count').textContent=list().groups.length;
  const selected=group(ui.group),index=list().groups.indexOf(selected);
  $('s-rename-group').disabled=$('s-delete-group').disabled=!selected;
  $('s-group-up').disabled=!selected || index===0;$('s-group-down').disabled=!selected || index===list().groups.length-1;
  $('s-collection-title').textContent=ui.group==='all'?'All Journals':ui.group==='none'?'Ungrouped / virtual':groupName(ui.group);
}
function visibleJournals() {
  const query=ui.search.collection.trim().toLowerCase();
  return list().journals.filter(j=>(ui.group==='all' || (ui.group==='none'?!j.groupId:j.groupId===ui.group)) && `${j.name} ${j.id} ${publisher(j.publisherId)?.name || ''} ${j.publisherId}`.toLowerCase().includes(query));
}
function syncJournalSelection() {
  const visible=visibleJournals(),count=visible.filter(j=>ui.journals.has(j.id)).length,input=$('s-select-journals');
  $('s-visible-journals').textContent=`${visible.length} Journals`;
  input.checked=visible.length>0 && count===visible.length;input.indeterminate=count>0 && count<visible.length;input.disabled=!visible.length;
}
function renderJournalAssignment() {
  const batch=ui.journals.size>0,rows=(batch?[...ui.journals]:[ui.journal]).map(journal).filter(Boolean);
  const panel=$('s-journal-assignment'),target=$('s-group-target'),pending=ui.journalAssignment;
  panel.hidden=!rows.length;panel.classList.toggle('s-context-divider',!batch);
  const scope=JSON.stringify(rows.map(j=>[j.id,j.groupId]));
  if(pending.scope!==scope){const values=new Set(rows.map(j=>j.groupId || ''));pending.scope=scope;pending.target=values.size===1?[...values][0]:'__mixed__';}
  if(pending.target && pending.target!=='__mixed__' && !group(pending.target))pending.target='__mixed__';
  target.innerHTML='<option value="__mixed__" disabled>Mixed · choose Group</option><option value="">Ungrouped / virtual</option>'+list().groups.map(g=>`<option value="${esc(g.id)}">${esc(g.name==='Ungrouped'?'Ungrouped / named Group':g.name)}</option>`).join('');
  target.value=pending.target;$('s-assign-journals').disabled=!rows.length || pending.target==='__mixed__';
  $('s-journal-selection').hidden=!batch;
  $('s-journal-selection').innerHTML=batch?`<div class="s-context-title">SELECTED JOURNALS</div><h2>${rows.length} Journals</h2>${memberList(rows.map(j=>j.name))}`:'';
  $('s-clear-journals').hidden=!batch;
}
$('s-group-target').addEventListener('change',event=>{ui.journalAssignment.target=event.target.value;$('s-assign-journals').disabled=event.target.value==='__mixed__';});
function coverOf(j) {return covers.get(j.id) || {image:null,phase:'Generated'};}
function renderJournals() {
  const container=$('s-journals'),scroll=container.scrollTop,items=visibleJournals(),focused=document.activeElement?.dataset.journal;
  container.innerHTML=items.length?items.map((j,i)=>{
    const cover=coverOf(j);
    return `<article class="s-journal-card" data-journal="${esc(j.id)}" data-current="${ui.journal===j.id}" draggable="true" tabindex="0" role="group" aria-label="${esc(j.name)} · Enter for details"><div class="s-card-head"><label class="s-check"><input type="checkbox" data-select-journal="${esc(j.id)}" ${ui.journals.has(j.id)?'checked':''} aria-label="Select ${esc(j.name)}"></label><span>${String(i+1).padStart(2,'0')}</span></div><div class="s-card-art">${cover.image?`<img src="${esc(cover.image)}" alt="${esc(j.name)} cover" loading="lazy">`:'<span class="s-eyebrow">GENERATED COVER</span>'}</div><h3>${esc(j.name)}</h3><p class="s-card-meta">${esc(j.id)}<br>${esc(groupName(j.groupId))}</p></article>`;
  }).join(''):'<p class="s-empty">'+(list().journals.length?'没有符合筛选的 Journals。':'没有 Journals。使用 + JOURNAL 添加期刊。')+'</p>';
  container.scrollTop=scroll;
  if(focused)container.querySelector(`[data-journal="${focused}"]`)?.focus({preventScroll:true});
  syncJournalSelection();
}
function renderJournalDetail() {
  renderJournalAssignment();
  const j=journal(ui.journal),detail=$('s-journal-detail'),actions=$('s-journal-actions');
  detail.hidden=ui.journals.size>0;actions.hidden=ui.journals.size>0 || !j;
  if(!j){detail.innerHTML='<div class="s-context-title">JOURNAL CONTEXT</div><p class="s-empty">选择 Journal 查看身份、关系和封面。</p>';return;}
  const p=publisher(j.publisherId),cover=coverOf(j),isSaved=saved.list.journals.some(row=>row.id===j.id),busy=coverJob.busy;
  detail.innerHTML=`<div class="s-context-title">JOURNAL / IDENTITY</div><h2>${esc(j.name)}</h2>${cover.image?`<img class="s-cover" src="${esc(cover.image)}" alt="${esc(j.name)} cover">`:'<p class="s-help">Generated cover</p>'}<dl><div><dt>ISSN-L / READ ONLY</dt><dd>${esc(j.id)}</dd></div><div><dt>DIRECT PUBLISHER / RELATIONSHIP</dt><dd>${esc(p?.name || 'None')}<br>${esc(j.publisherId || '—')}</dd></div><div><dt>DEFAULT ACCESS SERVICE</dt><dd>${esc(serviceName(p?.serviceId))}</dd></div></dl>`;
  actions.innerHTML=`<div class="s-cover-status" role="status">${esc(cover.phase)}${isSaved?'':' · Save Journal before Cover operations'}</div><div class="s-cover-controls"><button data-cover="search" class="s-button" ${!isSaved || busy || !coverJob.supported?'disabled':''}>Search</button><button data-cover="upload" class="s-button" ${!isSaved || busy?'disabled':''}>Upload</button></div><div class="s-context-divider"><button id="s-remove-journal" class="s-button">Remove Journal</button></div>`;
  $('s-remove-journal').addEventListener('click',()=>removeJournal(j));
  actions.querySelector('[data-cover=search]').addEventListener('click',()=>startCovers([j.id]));
  actions.querySelector('[data-cover=upload]').addEventListener('click',()=>uploadCover(j));
}
function renderCollection() {renderGroups();renderJournals();renderJournalDetail();renderCoverJob();}
$('s-journals').addEventListener('click',event=>{
  const card=event.target.closest('[data-journal]');if(!card || event.target.closest('label,input'))return;
  ui.journal=card.dataset.journal;renderJournals();renderJournalDetail();
});
$('s-journals').addEventListener('keydown',event=>{if(event.target.matches('[data-journal]') && ['Enter',' '].includes(event.key)){event.preventDefault();ui.journal=event.target.dataset.journal;renderJournals();renderJournalDetail();$('s-journals').querySelector(`[data-journal="${ui.journal}"]`)?.focus({preventScroll:true});}});
$('s-journals').addEventListener('change',event=>{const input=event.target.closest('[data-select-journal]');if(!input)return;if(input.checked)ui.journals.add(input.dataset.selectJournal);else ui.journals.delete(input.dataset.selectJournal);syncJournalSelection();renderJournalDetail();});
$('s-groups').addEventListener('click',event=>{const row=event.target.closest('[data-group]');if(!row)return;ui.group=row.dataset.group;renderGroups();renderJournals();});
function assignJournals(ids,target) {ids.forEach(id=>{const j=journal(id);if(j)j.groupId=target || null;});changed();renderCollection();message(`${ids.length} Journals assigned · Draft。`);}
$('s-assign-journals').addEventListener('click',()=>{const ids=ui.journals.size?[...ui.journals]:[ui.journal];if(ui.journalAssignment.target!=='__mixed__')assignJournals(ids,ui.journalAssignment.target);});
$('s-select-journals').addEventListener('change',event=>{visibleJournals().forEach(j=>{if(event.target.checked)ui.journals.add(j.id);else ui.journals.delete(j.id);});renderJournals();renderJournalDetail();});
$('s-clear-journals').addEventListener('click',()=>{ui.journals.clear();renderJournals();renderJournalDetail();$('s-select-journals').focus({preventScroll:true});});

function editGroup(existing=null) {
  showDialog(existing?'Rename Group':'New Group',`<label>Group name<input id="s-group-name" value="${esc(existing?.name || '')}" autocomplete="off"></label><p class="s-help">Ungrouped 可以作为真实 Group 名称；与虚拟目录区分。空 Group 不保存在 list.md。</p><p id="s-group-error" role="alert"></p>`,[cancel,{label:existing?'Rename':'Create',primary:true,run:()=>{
    const name=$('s-group-name').value.trim();
    if(!name || list().groups.some(g=>g.id!==existing?.id && g.name.toLowerCase()===name.toLowerCase())){$('s-group-error').textContent='名称不能为空或与已有 Group 重复。';return false;}
    if(existing){const old=existing.id;existing.name=name;existing.id=groupKey(name);for(const j of list().journals)if(j.groupId===old)j.groupId=existing.id;if(ui.group===old)ui.group=existing.id;}else {const g={id:groupKey(name),name};list().groups.push(g);ui.group=g.id;}
    changed();renderCollection();
  }}]);$('s-group-name').focus();
}
$('s-new-group').addEventListener('click',()=>editGroup());
$('s-rename-group').addEventListener('click',()=>editGroup(group(ui.group)));
function moveGroup(id,direction) {
  const index=list().groups.findIndex(g=>g.id===id),to=index+direction;
  if(index<0 || to<0 || to>=list().groups.length)return;
  const [g]=list().groups.splice(index,1);list().groups.splice(to,0,g);changed();renderGroups();
}
$('s-group-up').addEventListener('click',()=>moveGroup(ui.group,-1));$('s-group-down').addEventListener('click',()=>moveGroup(ui.group,1));
$('s-delete-group').addEventListener('click',()=>{
  const g=group(ui.group),members=list().journals.filter(j=>j.groupId===g.id);
  showDialog('Delete Group',`<p>${esc(g.name)} · ${members.length} Journals 将移至 Ungrouped。</p>${memberList(members.map(j=>j.name))}`,[cancel,{label:'Delete / move to Ungrouped',primary:true,run:()=>{members.forEach(j=>j.groupId=null);list().groups=list().groups.filter(row=>row.id!==g.id);ui.group='none';changed();renderCollection();}}]);
});
function removeJournal(j) {
  const p=publisher(j.publisherId),cascade=p && list().journals.filter(row=>row.publisherId===p.id).length===1;
  showDialog('Remove Journal',`<p>${esc(j.name)} / ${esc(j.id)}</p>${cascade?`<p>这是 Publisher 的最后一本 Journal。以下关系将从 Draft 移除：</p>${memberList([p.name,`Publisher URL: ${p.url || '(blank)'}`,`Default Service: ${serviceName(p.serviceId)}`])}<p class="s-help">Service 本身保留，可能成为空 Service。</p>`:'<p>移除本期刊；其他 Journals 和 Publisher 保留。</p>'}`,[cancel,{label:'Confirm removal',primary:true,run:()=>{
    list().journals=list().journals.filter(row=>row.id!==j.id);if(cascade)list().publishers=list().publishers.filter(row=>row.id!==p.id);
    ui.journals.delete(j.id);ui.journal=null;if(cascade){ui.publishers.delete(p.id);if(ui.publisher===p.id)ui.publisher=null;}
    changed();renderCollection();renderAccess();
  }}]);
}
$('s-add-journal').addEventListener('click',()=>{
  showDialog('Add Journal','<label>ISSN-L<input id="s-add-issn" placeholder="0000-0000" autocomplete="off"></label><p class="s-help">Save 时解析正式名称及 Publisher；保存前不能操作封面。</p><p id="s-add-error" role="alert"></p>',[cancel,{label:'Add to Draft',primary:true,run:()=>{
    const id=$('s-add-issn').value.trim().toUpperCase();
    if(!/^\d{4}-\d{3}[\dX]$/.test(id) || journal(id)){$('s-add-error').textContent=journal(id)?'此 ISSN-L 已存在。':'请输入 ISSN-L；完整身份校验在 Save 执行。';return false;}
    list().journals.push({id,name:'Pending resolution',publisherId:null,groupId:group(ui.group)?.id || null,pending:true});ui.journal=id;
    changed();renderCollection();message('Journal 已加入 Draft；Save 时解析真实身份。');
  }}]);$('s-add-issn').focus();
});

function renderServices() {
  const container=$('s-services'),scroll=container.scrollTop,horizontal=container.scrollLeft,focused=document.activeElement?.dataset.service;
  container.innerHTML=directoryRow('all','All Publishers',list().publishers.length,ui.service,'service')+directoryRow('none','Unassigned',list().publishers.filter(p=>!p.serviceId).length,ui.service,'service')+list().services.map(s=>directoryRow(s.id,s.name || '(unnamed Service)',list().publishers.filter(p=>p.serviceId===s.id).length,ui.service,'service')).join('');
  container.scrollTop=scroll;container.scrollLeft=horizontal;$('s-service-count').textContent=list().services.length;
  if(focused)container.querySelector(`[data-service="${focused}"]`)?.focus({preventScroll:true});
  const selected=service(ui.service),index=list().services.indexOf(selected);
  $('s-rename-service').disabled=$('s-delete-service').disabled=!selected;
  $('s-service-up').disabled=!selected || index===0;$('s-service-down').disabled=!selected || index===list().services.length-1;
  $('s-access-title').textContent=ui.search.access.trim()?'Global search':ui.service==='all'?'All Publishers':ui.service==='none'?'Unassigned':serviceName(ui.service);
}
function visiblePublishers() {
  const query=ui.search.access.trim().toLowerCase();
  return list().publishers.filter(p=>query?`${p.name} ${p.id}`.toLowerCase().includes(query):ui.service==='all' || (ui.service==='none'?!p.serviceId:p.serviceId===ui.service));
}
function syncPublisherSelection() {
  const visible=visiblePublishers(),count=visible.filter(p=>ui.publishers.has(p.id)).length,input=$('s-select-publishers');
  $('s-visible-publishers').textContent=`${visible.length} Publishers`;
  input.checked=visible.length>0 && count===visible.length;input.indeterminate=count>0 && count<visible.length;input.disabled=!visible.length;
}
function renderPublisherAssignment() {
  const batch=ui.publishers.size>0,rows=(batch?[...ui.publishers]:ui.accessDetail==='publisher'?[ui.publisher]:[]).map(publisher).filter(Boolean);
  const panel=$('s-publisher-assignment'),target=$('s-service-target'),pending=ui.publisherAssignment;
  panel.hidden=!rows.length;panel.classList.toggle('s-context-divider',!batch);
  const scope=JSON.stringify(rows.map(p=>[p.id,p.serviceId]));
  if(pending.scope!==scope){const values=new Set(rows.map(p=>p.serviceId || ''));pending.scope=scope;pending.target=values.size===1?[...values][0]:'__mixed__';}
  if(pending.target && pending.target!=='__mixed__' && !service(pending.target))pending.target='__mixed__';
  target.innerHTML='<option value="__mixed__" disabled>Mixed · choose Service</option><option value="">Unassigned</option>'+list().services.map(s=>`<option value="${esc(s.id)}">${esc(s.name || '(unnamed Service)')}</option>`).join('');
  target.value=pending.target;$('s-assign-publishers').disabled=!rows.length || pending.target==='__mixed__';
  $('s-publisher-selection').hidden=!batch;
  $('s-publisher-selection').innerHTML=batch?`<div class="s-context-title">SELECTED PUBLISHERS</div><h2>${rows.length} Publishers</h2>${memberList(rows.map(p=>p.name))}`:'';
  $('s-clear-publishers').hidden=!batch;
}
$('s-service-target').addEventListener('change',event=>{ui.publisherAssignment.target=event.target.value;$('s-assign-publishers').disabled=event.target.value==='__mixed__';});
function renderPublishers() {
  const container=$('s-publishers'),scroll=container.scrollTop,items=visiblePublishers(),focused=document.activeElement?.dataset.publisher;
  container.innerHTML=items.length?items.map(p=>`<article class="s-publisher-row" data-publisher="${esc(p.id)}" data-current="${ui.publisher===p.id && ui.accessDetail==='publisher'}" draggable="true" tabindex="0" role="group" aria-label="${esc(p.name)} · Enter for details"><label class="s-check"><input type="checkbox" data-select-publisher="${esc(p.id)}" ${ui.publishers.has(p.id)?'checked':''} aria-label="Select ${esc(p.name)}"></label><div><strong>${esc(p.name)}</strong>${ui.service==='all' || ui.search.access.trim()?`<small>Default Service: ${esc(serviceName(p.serviceId))}</small>`:''}</div></article>`).join(''):'<p class="s-empty">'+(list().publishers.length?'没有符合筛选的 Publishers。':'没有 Publishers。添加 Journal 后会出现其Publisher。')+'</p>';
  container.scrollTop=scroll;syncPublisherSelection();
  if(focused)container.querySelector(`[data-publisher="${focused}"]`)?.focus({preventScroll:true});
}
function renderServiceDetail() {
  const s=service(ui.service),detail=$('s-service-detail');
  detail.hidden=ui.accessDetail!=='service' || ui.publishers.size>0;
  if(!s){detail.innerHTML='<div class="s-context-title">SERVICE CONTEXT</div><p class="s-help">Select a Service to edit.</p>';return;}
  detail.innerHTML=`<div class="s-context-title">SERVICE / IDENTITY</div><h2 id="s-service-name-title">${esc(s.name || '(unnamed Service)')}</h2><dl><div><dt>STABLE SERVICE ID / READ ONLY</dt><dd>${esc(s.id)}</dd></div></dl><label class="s-context-divider">Service name<input data-service-field="name" value="${esc(s.name)}" autocomplete="off"></label><label>Access Method<select data-service-field="method"><option value="carsi">CARSI</option><option value="manual">Manual Login</option></select></label><label>Service URL<input data-service-field="url" value="${esc(s.url)}" type="url" placeholder="Optional public HTTP(S) URL" autocomplete="off"></label><p id="s-service-members" class="s-help">${list().publishers.filter(p=>p.serviceId===s.id).length} Publishers</p>`;
  detail.querySelector('select').value=s.method;
  detail.querySelectorAll('[data-service-field]').forEach(input=>input.addEventListener(input.tagName==='SELECT'?'change':'input',()=>{
    s[input.dataset.serviceField]=input.value;$('s-service-name-title').textContent=s.name || '(unnamed Service)';changed();renderServices();renderPublishers();renderPublisherDetail();renderJournalDetail();
  }));
}

function renderPublisherDetail() {
  renderPublisherAssignment();
  const p=publisher(ui.publisher),detail=$('s-publisher-detail');
  detail.hidden=ui.accessDetail!=='publisher' || ui.publishers.size>0;
  if(!p){detail.innerHTML='<div class="s-context-title">PUBLISHER CONTEXT</div><p class="s-empty">选择 Publisher 查看身份与手动 URL。</p>';return;}
  detail.innerHTML=`<div class="s-context-title">PUBLISHER / IDENTITY</div>${service(ui.service)?`<button id="s-back-service" class="s-context-back">← Edit ${esc(serviceName(ui.service))}</button>`:''}<h2>${esc(p.name)}</h2><dl><div><dt>DIRECT PUBLISHER ID / READ ONLY</dt><dd>${esc(p.id)}</dd></div></dl><label class="s-context-divider">Publisher URL<input id="s-publisher-url" type="url" value="${esc(p.url)}" placeholder="Optional public HTTP(S) URL" autocomplete="off"></label><p class="s-help">${list().journals.filter(j=>j.publisherId===p.id).length} Journals</p>`;
  $('s-back-service')?.addEventListener('click',()=>{showAccessDetail('service');$('s-service-detail').querySelector('input')?.focus({preventScroll:true});});
  $('s-publisher-url').addEventListener('input',event=>{p.url=event.target.value;changed();});
}
function renderAccessContext() {renderServiceDetail();renderPublisherDetail();}
function renderAccess() {renderServices();renderPublishers();renderAccessContext();}
function showAccessDetail(kind) {
  ui.accessDetail=kind;renderAccess();$('s-service-detail').parentElement.scrollTop=0;
}
$('s-services').addEventListener('click',event=>{const row=event.target.closest('[data-service]');if(!row)return;ui.service=row.dataset.service;showAccessDetail('service');});
$('s-publishers').addEventListener('click',event=>{
  const row=event.target.closest('[data-publisher]');if(!row || event.target.closest('label,input'))return;
  ui.publisher=row.dataset.publisher;showAccessDetail('publisher');
});
$('s-publishers').addEventListener('keydown',event=>{if(event.target.matches('[data-publisher]') && ['Enter',' '].includes(event.key)){event.preventDefault();ui.publisher=event.target.dataset.publisher;showAccessDetail('publisher');$('s-publishers').querySelector(`[data-publisher="${ui.publisher}"]`)?.focus({preventScroll:true});}});
$('s-publishers').addEventListener('change',event=>{const input=event.target.closest('[data-select-publisher]');if(!input)return;if(input.checked)ui.publishers.add(input.dataset.selectPublisher);else ui.publishers.delete(input.dataset.selectPublisher);syncPublisherSelection();renderAccessContext();});
function assignPublishers(ids,target) {
  ids.forEach(id=>{const p=publisher(id);if(p)p.serviceId=target || null;});
  changed();renderAccess();renderJournalDetail();message(`${ids.length} Publishers assigned · Draft。`);
}
$('s-assign-publishers').addEventListener('click',()=>{const ids=ui.publishers.size?[...ui.publishers]:[ui.publisher];if(ui.publisherAssignment.target!=='__mixed__')assignPublishers(ids,ui.publisherAssignment.target);});
$('s-select-publishers').addEventListener('change',event=>{visiblePublishers().forEach(p=>{if(event.target.checked)ui.publishers.add(p.id);else ui.publishers.delete(p.id);});renderPublishers();renderAccessContext();});
$('s-clear-publishers').addEventListener('click',()=>{ui.publishers.clear();renderPublishers();renderAccessContext();$('s-select-publishers').focus({preventScroll:true});});
$('s-rename-service').addEventListener('click',()=>{
  const s=service(ui.service);
  showDialog('Rename Service',`<label>Service name<input id="s-rename-service-name" value="${esc(s.name)}" autocomplete="off"></label><p class="s-help">仅修改名称；Service ID、URL、Access Method 和 Publisher 关系保留。</p><p id="s-service-error" role="alert"></p>`,[cancel,{label:'Rename',primary:true,run:()=>{
    const name=$('s-rename-service-name').value.trim();
    if(!name || list().services.some(row=>row.id!==s.id && row.name.trim().toLowerCase()===name.toLowerCase())){$('s-service-error').textContent='名称不能为空或与已有 Service 重复。';return false;}
    s.name=name;changed();renderAccess();renderJournalDetail();
  }}]);$('s-rename-service-name').focus();
});
function moveService(id,direction) {
  const index=list().services.findIndex(s=>s.id===id),to=index+direction;
  if(index<0 || to<0 || to>=list().services.length)return;
  const [s]=list().services.splice(index,1);list().services.splice(to,0,s);changed();renderServices();renderPublisherAssignment();
}
$('s-service-up').addEventListener('click',()=>moveService(ui.service,-1));$('s-service-down').addEventListener('click',()=>moveService(ui.service,1));
$('s-delete-service').addEventListener('click',()=>{
  const s=service(ui.service),members=list().publishers.filter(p=>p.serviceId===s.id);
  showDialog('Delete Service',`<p>${esc(s.name)} / ${esc(s.id)}</p><p>${members.length} Publishers 将成为 Unassigned，Publisher URL 保留。</p>${memberList(members.map(p=>p.name))}`,[cancel,{label:'Delete & unassign',primary:true,run:()=>{
    members.forEach(p=>p.serviceId=null);list().services=list().services.filter(row=>row.id!==s.id);ui.service='none';ui.accessDetail='service';changed();renderAccess();renderJournalDetail();
  }}]);
});
$('s-new-service').addEventListener('click',()=>{
  showDialog('New Access Service','<label>Service name<input id="s-new-service-name" autocomplete="off"></label><label>Service URL<input id="s-new-service-url" placeholder="Optional HTTP(S) URL"></label><label>Access Method<select id="s-new-service-method"><option value="carsi">CARSI</option><option value="manual">Manual Login</option></select></label><p id="s-service-error" role="alert"></p>',[cancel,{label:'Create Service',primary:true,run:()=>{
    const name=$('s-new-service-name').value.trim(),url=$('s-new-service-url').value.trim();
    if(!name || list().services.some(s=>s.name.trim().toLowerCase()===name.toLowerCase())){$('s-service-error').textContent='名称不能为空或重复；URL 会在 Save 时校验。';return false;}
    const s={id:'svc_'+crypto.randomUUID().replaceAll('-',''),name,url,method:$('s-new-service-method').value};list().services.push(s);ui.service=s.id;ui.accessDetail='service';
    changed();renderAccess();
  }}]);$('s-new-service-name').focus();
});

// One native drag payload; all assignments also have keyboard-friendly controls.
let drag=null;
root.addEventListener('dragstart',event=>{
  const node=event.target.closest('[data-journal],[data-publisher],[data-group]');if(!node)return;
  if(node.dataset.journal){const id=node.dataset.journal;drag={kind:'journal',ids:ui.journals.has(id)?[...ui.journals]:[id]};}
  else if(node.dataset.publisher){const id=node.dataset.publisher;drag={kind:'publisher',ids:ui.publishers.has(id)?[...ui.publishers]:[id]};}
  else drag={kind:'group',id:node.dataset.group};
  event.dataTransfer.setData('text/plain',JSON.stringify(drag));event.dataTransfer.effectAllowed='move';
});
function dropTarget(event) {
  const node=event.target.closest('[data-group],[data-service]');
  if(!drag || !node)return null;
  if(drag.kind==='publisher' && node.dataset.service && node.dataset.service!=='all')return node;
  if(drag.kind==='journal' && node.dataset.group && node.dataset.group!=='all')return node;
  if(drag.kind==='group' && group(node.dataset.group))return node;
  return null;
}
root.addEventListener('dragover',event=>{const target=dropTarget(event);if(target){event.preventDefault();event.dataTransfer.dropEffect='move';target.dataset.drop='true';}});
root.addEventListener('dragleave',event=>{event.target.closest('[data-drop]')?.removeAttribute('data-drop');});
root.addEventListener('drop',event=>{
  const target=dropTarget(event);if(!target)return;event.preventDefault();
  if(drag.kind==='journal')assignJournals(drag.ids,target.dataset.group==='none'?null:target.dataset.group);
  else if(drag.kind==='publisher')assignPublishers(drag.ids,target.dataset.service==='none'?null:target.dataset.service);
  else {
    const groups=list().groups,from=groups.findIndex(g=>g.id===drag.id),to=groups.findIndex(g=>g.id===target.dataset.group);
    const [g]=groups.splice(from,1);groups.splice(to,0,g);changed();renderGroups();
  }
  drag=null;root.querySelectorAll('[data-drop]').forEach(node=>node.removeAttribute('data-drop'));
});
root.addEventListener('dragend',()=>{drag=null;root.querySelectorAll('[data-drop]').forEach(node=>node.removeAttribute('data-drop'));});

function renderCoverJob() {
  $('s-cover-summary').textContent=coverJob.total?`${coverJob.phase} ${coverJob.completed}/${coverJob.total}`:'';
  $('s-cover-job-status').textContent=`${coverJob.phase} · ${coverJob.completed} / ${coverJob.total} processed · ${coverJob.failed} failed${coverJob.message?' · '+coverJob.message:''}`;
  $('s-cover-progress').value=coverJob.completed;$('s-cover-progress').max=coverJob.total || 1;
  $('s-cover-missing').disabled=loading || coverJob.busy || !coverJob.supported;
  $('s-cover-reset').disabled=loading || coverJob.busy;
  $('s-cover-continue').disabled=!coverJob.can_continue;
  $('s-cover-retry').disabled=!coverJob.can_retry;
  $('s-cover-cancel').disabled=coverJob.phase!=='Running';
}
$('s-covers-toggle').addEventListener('click',()=>{
  const open=$('s-cover-panel').hidden;$('s-cover-panel').hidden=!open;$('s-covers-toggle').setAttribute('aria-expanded',String(open));
});
async function refreshCovers() {
  clearTimeout(coverTimer);
  try {
    coverJob=await uiNext.request('/settings/covers/search');
    const data=await uiNext.request('/api/ui-next/settings');applyCoverDetails(data);
    renderCollection();
  } catch(error){message(error.message,'error');}
  if(coverJob.phase==='Running')coverTimer=setTimeout(refreshCovers,1000);
}
async function coverAction(path,extra={}) {
  try {await formRequest(path,extra,{draft:false});await refreshCovers();}
  catch(error){message(error.message,'error');}
}
function startCovers(ids) {void coverAction('/settings/covers/search',ids?.length?{issn_l:ids[0]}:{});}
$('s-cover-missing').addEventListener('click',()=>startCovers());
for(const action of ['continue','retry','cancel'])$('s-cover-'+action).addEventListener('click',()=>void coverAction('/settings/covers/search/'+action,{request_id:coverJob.request_id || ''}));
$('s-cover-reset').addEventListener('click',()=>confirm('Reset Cover resources','移除全部已保存封面，包括 Upload 结果；不会自动查找，配置 Draft 保留。',()=>void coverAction('/settings/covers/reset'),'Reset all covers'));
function uploadCover(j) {
  showDialog('Upload Cover','<label>Image file<input id="s-upload-file" type="file" accept="image/*"></label><p id="s-upload-error" role="alert"></p>',[cancel,{label:'Upload',primary:true,run:async()=>{
    const file=$('s-upload-file').files[0];if(!file){$('s-upload-error').textContent='请选择图片。';return false;}
    await coverAction('/settings/covers/'+encodeURIComponent(j.id)+'/upload',{file});
  }}]);
}
window.addEventListener('pagehide',()=>clearTimeout(coverTimer));

function closePreview() {
  preview=null;$('s-file-preview').hidden=true;$('s-'+view).hidden=false;
  updateDirty();restoreScroll();window.uiNextSettings.refreshNavigation();$('s-files').focus({preventScroll:true});
}
function beginPreview(kind,title,filename,summary,columns,choices) {
  rememberScroll();transitions.get(view).dispose();$('s-'+view).hidden=true;
  preview={kind,revision};$('s-file-preview').hidden=false;
  window.uiNextSettings.refreshNavigation();
  $('s-preview-title').textContent=title;$('s-preview-file').textContent=filename;$('s-preview-summary').textContent=summary;
  $('s-preview-columns').innerHTML=columns.map(({title,text})=>`<div><h3>${esc(title)}</h3><pre>${esc(text)}</pre></div>`).join('');
  $('s-preview-columns').style.gridTemplateColumns=columns.length===1?'minmax(0,1fr)':'';
  $('s-preview-actions').replaceChildren();
  for(const choice of choices){const button=document.createElement('button');button.className='s-button'+(choice.primary?' s-primary':'');button.textContent=choice.label;button.addEventListener('click',async()=>{button.disabled=true;try{await choice.run();}catch(error){message(error.message,'error');}finally{button.disabled=false;}});$('s-preview-actions').append(button);}
  updateDirty();$('s-preview-title').tabIndex=-1;$('s-preview-title').focus({preventScroll:true});
}
function selectImport() {
  showDialog('Import complete list.md','<label>Complete Markdown file<input id="s-import-file" type="file" accept=".md,text/markdown"></label><p class="s-help">Preview 比较整个已保存文件；Confirm 将完整替换 list.md。</p><p id="s-import-error" role="alert"></p>',[cancel,{label:'Preview',primary:true,run:async()=>{
    const file=$('s-import-file').files[0];if(!file){$('s-import-error').textContent='请选择完整 .md 文件。';return false;}
    await openImport(file);
  }}]);
}
async function openImport(file) {
  const data=await formRequest('/settings/import/preview',{markdown_file:file});
  showIssues(data);
  if(!data.import_plan)return false;
  const p=data.import_plan;
  beginPreview('import','Import Preview / complete list.md',data.import_filename,p.changes.join('\n'),[{title:'Saved baseline / entire file',text:p.original},{title:'Selected source / entire replacement',text:p.proposed}],[{label:'Cancel Import',run:closePreview},{label:'Confirm Import',primary:true,run:confirmImport}]);
  Object.assign(preview,{data,file});return true;
}
async function executeImport(discard=false) {
  const current=preview;if(!current || current.kind!=='import')return;
  const data=await formRequest('/settings/import/confirm',{
    markdown_import_payload:current.data.import_payload,markdown_import_filename:current.data.import_filename,
    markdown_import_proof:current.data.import_proof,confirm_full_import:'yes',...(discard?{import_discard:'yes'}:{})});
  await acceptOperation(data);
}
function confirmImport() {
  if(!dirty())return executeImport();
  showDialog('Resolve unsaved Settings','<p>Import 会替换整个 list.md。明确 Discard 会丢弃全部 Settings 草稿，包括 Monitor、Institution 和 Workflow；Import 只写 list.md。</p>',[
    cancel,{label:'Save & refresh Preview',run:async()=>{const file=preview.file;dialog.close();await requestSave(()=>openImport(file));}},
    {label:'Discard all Draft & Import',primary:true,run:()=>executeImport(true)},
  ]);
}
async function exportFile(kind='list',discard=false) {
  if(dirty() && !discard){
    showDialog('Resolve unsaved Settings','<p>导出只使用已保存配置。请选择 Save、丢弃全部 Settings 草稿或 Cancel。</p>',[cancel,
      {label:'Save & export',run:async()=>{dialog.close();await requestSave(()=>exportFile(kind));}},
      {label:'Discard all Draft & export',primary:true,run:()=>exportFile(kind,true)}]);return;
  }
  const result=await formRequest(kind==='pack'?'/settings/export-ai-mapping-pack':'/settings/export',discard?{export_discard:'yes'}:{},{download:true});
  if(!result.blob){showIssues(result);return;}
  if(discard){await loadSettings();await refreshSavedWorkspace();}
  const text=await result.blob.text();
  beginPreview(kind,kind==='pack'?'AI Mapping Pack / work file':'Export saved list.md',result.filename,kind==='pack'?'独立工作文件，不能作为 list.md 导入。':'已保存完整文件，不包含未保存草稿或 monitor.yaml。',[{title:'Saved file',text}],[
    {label:'Return to Settings',run:closePreview},{label:'Download',primary:true,run:()=>{const url=URL.createObjectURL(result.blob),link=document.createElement('a');link.href=url;link.download=result.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}}]);
}
function reloadSettings() {
  if(!draft){void loadSettings();return;}
  showDialog('Reload Settings from Disk','<p>丢弃全部未保存配置并重新读取文件。独立 Cover 资源保留。</p><label>Type RELOAD<input id="s-reload-word" autocomplete="off" spellcheck="false"></label><p id="s-reload-error" role="alert"></p>',[cancel,{label:'Reload / discard draft',primary:true,run:async()=>{
    if($('s-reload-word').value!=='RELOAD'){$('s-reload-error').textContent='请输入完整的 RELOAD。';return false;}
    await acceptOperation(await formRequest('/settings/reload',{reload_step:'confirm',reload_word:'RELOAD'}));
  }}]);$('s-reload-word').focus();
}
async function upgradeSchema() {
  const data=await formRequest('/settings/upgrade/preview');showIssues(data);if(!data.upgrade_plan)return;
  beginPreview('upgrade','Legacy Schema Upgrade Preview','list.md','确认升级已保存 Schema；未保存草稿必须先 Save 或明确 Discard。',[{title:'Saved file',text:serverState.list_text || ''},{title:'Proposed current schema',text:data.upgrade_plan.target_contents}],[{label:'Cancel Upgrade',run:closePreview},{label:'Confirm Upgrade',primary:true,run:()=>{
    const run=async(discard)=>acceptOperation(await formRequest('/settings/upgrade/confirm',{access_upgrade_proof:data.upgrade_proof,confirm_access_upgrade:'yes',...(discard?{upgrade_discard:'yes'}:{})}));
    if(dirty())showDialog('Resolve unsaved Settings','<p>升级后重新加载配置，丢弃全部未保存 Settings 草稿。</p>',[cancel,{label:'Save & refresh Preview',run:async()=>{dialog.close();await requestSave(upgradeSchema);}},{label:'Discard all Draft & upgrade',primary:true,run:()=>run(true)}]);else return run(false);
  }}]);
}
root.querySelectorAll('[data-file-action]').forEach(button=>button.addEventListener('click',()=>{
  closeMenu();if(preview || operationPending){message('请先完成或退出当前操作。');return;}
  const actions={import:selectImport,export:()=>exportFile(),pack:()=>exportFile('pack'),reload:reloadSettings,upgrade:upgradeSchema};
  Promise.resolve(actions[button.dataset.fileAction]()).catch(error=>message(error.message,'error'));
}));

function reconcileSelections() {
  if(ui.group!=='all' && ui.group!=='none' && !group(ui.group))ui.group='all';
  if(ui.service!=='all' && ui.service!=='none' && !service(ui.service))ui.service='all';
  if(!journal(ui.journal))ui.journal=null;if(!publisher(ui.publisher))ui.publisher=null;
  if(ui.accessDetail==='publisher' && !ui.publisher)ui.accessDetail='service';
  ui.journals=new Set([...ui.journals].filter(id=>journal(id)));ui.publishers=new Set([...ui.publishers].filter(id=>publisher(id)));
}

// Group keys are derived from names and are never a persisted identity.
function groupKey(name) {return 'group:'+name;}
function project(data) {
  const v=data.values;
  return {monitor:{name:v.name,expression:v.keyword_expression,from:v.from_month,to:v.to_month,
    workspace:v.output_dir,log:v.log_level,autoImport:v.auto_import_after_preparation,
    delay:v.transition_delay_seconds,displayName:v.display_name,institutionName:v.institution_name,
    idp:v.institution_idp_entity_id,consent:v.automatic_attribute_release_consent},
    revisions:{monitor_revision_exists:v.monitor_revision_exists,monitor_revision_digest:v.monitor_revision_digest,
      journal_revision_exists:v.journal_revision_exists,journal_revision_digest:v.journal_revision_digest},
    list:{journals:v.journals.filter(j=>j.issns).map(j=>({id:j.issns,name:j.name,publisherId:j.publisher_id || null,
        groupId:j.group?groupKey(j.group):null,pending:j.pending})),
      publishers:v.publishers.map(p=>({id:p.publisher_id,name:p.name,url:p.publisher_url,serviceId:p.access_service_id || null})),
      services:v.access_services.map(s=>({id:s.id,name:s.name,url:s.access_url,method:s.access_method})),
      groups:v.groups.map(name=>({id:groupKey(name),name})),legacy_journals:v.legacy_journals,
      migration_targets:v.migration_targets}};
}
function applyCoverDetails(data) {
  covers.clear();
  for(const [id,detail] of Object.entries(data.covers || {}))covers.set(id,{
    image:['Uploaded','Downloaded'].includes(detail.state)?'/settings/covers/'+encodeURIComponent(id)+'/image?revision='+Date.now():null,
    phase:detail.state+(detail.message?' · '+detail.message:'')});
}
function adoptState(data) {
  const resetChanged=serverState && serverState.values.monitor_revision_digest!==data.values.monitor_revision_digest;
  serverState=data;uiNext.csrf_token=data.csrf_token;saved=project(data);draft=copy(saved);revision++;
  loading=false;applyCoverDetails(data);reconcileSelections();syncMonitor();renderCollection();renderAccess();updateDirty();
  if(resetChanged){clearReset();if($('s-reset-section').open)void refreshResetPreview();}
}
function submission() {
  const form=new FormData(),m=draft.monitor;
  for(const [field,key] of Object.entries({name:'name',keyword_expression:'expression',from_month:'from',to_month:'to',
    output_dir:'workspace',log_level:'log',transition_delay_seconds:'delay',display_name:'displayName',
    institution_name:'institutionName',institution_idp_entity_id:'idp'}))form.set(field,String(m[key]??''));
  form.set('auto_import_after_preparation',m.autoImport?'1':'');form.set('automatic_attribute_release_consent',m.consent?'1':'');
  for(const [key,value] of Object.entries(draft.revisions))form.set(key,value);
  for(const g of list().groups)form.append('settings_group',g.name);
  // Group order is represented by Journal rows, with stable order inside each Group.
  const order=new Map(list().groups.map((g,i)=>[g.id,i]));
  const journals=[...list().journals];
  const occupied=(config)=>config.groups.filter(g=>config.journals.some(j=>j.groupId===g.id)).map(g=>g.id);
  if(!same(occupied(list()),occupied(saved.list)))journals.sort((a,b)=>(order.get(a.groupId)??order.size)-(order.get(b.groupId)??order.size));
  journals.forEach((j,i)=>{for(const [key,value] of Object.entries({journal_name:j.name,journal_issns:j.id,
    journal_group:group(j.groupId)?.name || '',journal_publisher_id:j.publisherId || '',journal_pending:j.pending?'1':'0',journal_order:String(i)}))form.append(key,value);});
  list().publishers.forEach((p,i)=>{for(const [key,value] of Object.entries({publisher_name:p.name,publisher_id:p.id,
    publisher_access_url:p.url,publisher_access_service_id:p.serviceId || '',publisher_order:String(i)}))form.append(key,value);});
  for(const s of list().services)for(const [key,value] of Object.entries({service_id:s.id,service_name:s.name,service_access_url:s.url,service_access_method:s.method}))form.append(key,value);
  form.set('legacy_journals',list().legacy_journals);
  for(const target of list().migration_targets)form.append('migration_issn_l',target);
  return form;
}
async function formRequest(path,extra={},options={}) {
  if(operationPending)throw new Error('当前操作尚未结束。');
  const form=options.draft===false?new FormData():submission();
  form.set('csrf_token',uiNext.csrf_token || '');for(const [key,value] of Object.entries(extra))form.set(key,value);
  operationPending=true;root.dataset.busy='true';root.querySelector('.s-content').inert=true;updateDirty();window.uiNextSettings.refreshNavigation();
  try {
    let response;
    try {response=await fetch(path,{method:'POST',headers:{Accept:'application/json'},body:form,cache:'no-store'});}
    catch {throw new Error('无法连接 Literature Monitor；草稿保留，请确认服务仍在运行。');}
    if(options.download && response.ok && response.headers.get('Content-Disposition')){
      const filename=response.headers.get('Content-Disposition').match(/filename="([^"]+)"/)?.[1] || 'list.md';
      return {blob:await response.blob(),filename};
    }
    let data;
    try{data=await response.json();}catch{throw new Error('服务响应无法读取；请 Reload 核对磁盘状态，当前草稿保留。');}
    if(!response.ok)throw new Error(data.message || data.reset_error || `请求失败 (${response.status})`);
    return data;
  } finally {operationPending=false;root.dataset.busy='false';root.querySelector('.s-content').inert=loading;updateDirty();window.uiNextSettings.refreshNavigation();}
}
function showIssues(data) {
  const issues=data.issues || [],validation=data.validation?.issues || [];
  const all=[...new Map([...issues,...validation].map(i=>[JSON.stringify([i.field,i.message]),i])).values()];
  const texts=[...new Set(all.map(i=>i.message))];
  message([data.message,...texts].filter(Boolean).join(' '),data.saved?'normal':'error');
  const fields={name:'name',keyword_expression:'expression',from_month:'from',to_month:'to',output_dir:'workspace',
    log_level:'log',transition_delay_seconds:'delay',display_name:'displayName',
    'institution.name':'institutionName','institution.idp_entity_id':'idp','institution':'idp',kept_workflow:'delay'};
  root.querySelectorAll('.s-field-error').forEach(node=>node.remove());
  root.querySelectorAll('[data-monitor]').forEach(input=>input.setAttribute('aria-invalid','false'));
  let first;
  for(const issue of all){const key=fields[issue.field];if(!key)continue;const input=root.querySelector(`[data-monitor="${key}"]`);input.setAttribute('aria-invalid','true');
    const hint=document.createElement('span');hint.className='s-field-error';hint.textContent=issue.message;input.parentElement.append(hint);first ||= input;}
  if(first){if(preview)closePreview();if(dialog.open)dialog.close();openSettings('settings',true);first.focus();}
}
async function refreshSavedWorkspace() {
  try {await window.uiNextSettings.refreshWorkspace();}
  catch(error){message('配置状态已更新；Workspace 读取失败：'+error.message,'error');}
}
async function acceptOperation(data) {
  if(data.saved){if(preview)closePreview();adoptState(data);showIssues(data);await refreshSavedWorkspace();return true;}
  showIssues(data);return false;
}
async function saveDraft(after=()=>{},approval={}) {
  const data=await formRequest('/settings/save',approval);showIssues(data);
  if(data.deletion_plan){
    const plan=data.deletion_plan,lines=[...plan.services.map(s=>'Service: '+s.name),...plan.publishers.map(p=>'Publisher: '+p.name)];
    for(const [id,members] of plan.service_members)for(const p of members)lines.push(`${p.name} → Unassigned (${id})`);
    showDialog('Confirm Settings deletions',`<p>以下影响由当前已保存配置计算。确认绑定本次草稿和文件版本。</p>${memberList(lines)}`,[cancel,{label:'Confirm Save',primary:true,run:()=>saveDraft(after,{confirm_settings_deletions:'yes',settings_deletion_proof:data.deletion_proof})}]);return false;
  }
  if(await acceptOperation(data)){await after();return true;}return false;
}
async function requestSave(after=()=>{}) {
  const empty=list().groups.filter(g=>!list().journals.some(j=>j.groupId===g.id));
  if(empty.length){showDialog('Empty Groups are draft only',`<p>成功 Save 后不会保留这些空 Group：</p>${memberList(empty.map(g=>g.name))}`,[cancel,{label:'Save without empty Groups',primary:true,run:()=>saveDraft(after)}]);return;}
  return saveDraft(after);
}
$('s-save').addEventListener('click',()=>void requestSave().catch(error=>message(error.message,'error')));

async function refreshDiagnostic(kind) {
  const button=root.querySelector(`[data-diagnostic="${kind}"]`);button.disabled=true;
  try {
    const data=await uiNext.request(kind==='run'?'/api/ui-next/run':'/api/ui-next/settings/diagnostics/'+kind);
    let summary;
    if(kind==='zotero')summary=`Connector: ${data.connector_readiness} · Desktop: ${data.zotero_instance?.message || (data.zotero_instance?.outcome || 'See details')}`;
    else if(kind==='health')summary=data.health_config_error || `${data.health_workspace?.issues?.length || 0} Workspace issues`;
    else summary=data.snapshot.status;
    $('s-'+kind+'-summary').textContent=summary;$('s-'+kind+'-result').textContent=JSON.stringify(data,null,2);
  } catch(error){$('s-'+kind+'-summary').textContent='Unavailable';$('s-'+kind+'-result').textContent=error.message;}
  finally {button.disabled=false;}
}
root.querySelectorAll('[data-diagnostic]').forEach(button=>button.addEventListener('click',()=>void refreshDiagnostic(button.dataset.diagnostic)));
let resetPlan=null;
async function refreshResetPreview() {
  try {resetPlan=await uiNext.request('/api/ui-next/settings/reset-preview');
    $('s-reset-result').textContent=resetPlan.reset_error || '确认后将删除此已保存 Workspace 的全部内容。';
    if(resetPlan.reset_plan)$('s-reset-path').textContent=resetPlan.reset_plan.workspace;
  }catch(error){resetPlan=null;$('s-reset-result').textContent=error.message;}
  $('s-reset-workspace').disabled=$('s-reset-word').value!=='RESET' || !resetPlan?.reset_token;
}
$('s-reset-section').addEventListener('toggle',()=>{if($('s-reset-section').open)void refreshResetPreview();});
$('s-reset-word').addEventListener('input',()=>{$('s-reset-workspace').disabled=$('s-reset-word').value!=='RESET' || !resetPlan?.reset_token;});
function clearReset() {$('s-reset-word').value='';$('s-reset-workspace').disabled=true;resetPlan=null;}
$('s-reset-cancel').addEventListener('click',()=>{clearReset();$('s-reset-result').textContent='Reset cancelled。';});
$('s-reset-workspace').addEventListener('click',()=>{
  if($('s-reset-word').value!=='RESET' || !resetPlan?.reset_token)return;
  const current=resetPlan;
  confirm('Confirm Workspace Reset',`删除已保存目标：${current.reset_plan.workspace}。该 Workspace 内的 Paper、Author、索引和其他文件均会移除；配置与 Zotero Library 不受影响。`,async()=>{
    try {
      const result=await formRequest('/settings/workspace-reset',{reset_token:current.reset_token,reset_word:'RESET'},{draft:false});
      $('s-reset-result').textContent=result.reset_error;
      if(result.reset_success){await refreshSavedWorkspace();await refreshDiagnostic('health');}
    }catch(error){$('s-reset-result').textContent=error.message;}finally{clearReset();}
  },'Confirm RESET');
});
async function loadSettings() {
  loading=true;root.querySelector('.s-content').inert=true;updateDirty();
  try {const data=await uiNext.request('/api/ui-next/settings');adoptState(data);coverJob=data.cover_search;renderCoverJob();
    if(data.issues.length)message(data.issues.map(i=>`${i.message}${i.path?' · '+i.path:''}`).join('\n'),'error');
    else message('');
    if(coverJob.phase==='Running')coverTimer=setTimeout(refreshCovers,1000);
  }catch(error){message(error.message,'error');}
  finally{root.querySelector('.s-content').inert=loading;updateDirty();}
}
void loadSettings();
