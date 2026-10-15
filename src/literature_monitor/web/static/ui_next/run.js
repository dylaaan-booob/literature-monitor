import {RunVisual} from './run-visual.js';
const $=id=>document.getElementById(id),dialog=$('run-view');
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const transition=new SurfaceTransition($('run-wash'),undefined,600,600);
let active=null,pending=null;

function notice(text,details=[]) {
  uiNext.notice(text,details,{retryInbox:pending!==null});
}
function resultDetails(snapshot) {
  const result=snapshot.result;
  if(!result)return snapshot.unexpected_error?[snapshot.unexpected_error.message]:[];
  return [...(result.warnings||[]),...(result.errors||[]),...(result.diagnostics||[])].map(item=>item.message);
}
function showState(data) {
  const {snapshot,progress}=data;
  $('run-elapsed').textContent=progress.elapsed_text || '0s';
  $('run-stage').textContent=progress.stage_label || snapshot.status;
  $('run-description').textContent=progress.announcement_message || (snapshot.inactivity_warning?'暂未收到新的活动。':'');
}
async function finish(run,data) {
  if(active!==run || run.ended)return;
  run.ended=true;clearTimeout(run.timer);run.visual.stop();showState(data);
  const snapshot=data.snapshot,count=snapshot.result?.created_papers || 0;
  $('run-stage').textContent='RUN FINISHED';$('run-description').textContent=`${count} NEW PAPERS`;
  const details=resultDetails(snapshot);
  try {await window.inboxRun.prepare(null,run.selection);pending=null;$('retry-inbox').hidden=true;}
  catch(error){pending=run.selection;$('retry-inbox').hidden=false;details.push(`Inbox 刷新失败：${error.message}`);}
  notice(`Run · ${count} new papers${snapshot.result?.outcome?` · ${snapshot.result.outcome}`:''}`,details);
  if(!reduced.matches) {
    await new Promise(resolve=>{transition.show(false);const animation=transition.animations[0];if(animation)void animation.finished.then(resolve,resolve);else resolve();});
    $('run-stage-layer').hidden=true;dialog.classList.add('returning');dialog.querySelector('.run-background').hidden=true;
    await new Promise(resolve=>transition.hide(false,resolve));
  }
  dialog.close();run.visual.stop();active=null;$('run-start').disabled=false;$('run-start').focus({preventScroll:true});
}
async function poll(run) {
  if(active!==run || run.ended)return;
  try {
    const data=await uiNext.request('/api/ui-next/run');showState(data);
    if(data.snapshot.status!=='RUNNING'){await finish(run,data);return;}
  }catch(error){$('run-description').textContent=`连接中断：${error.message}；正在等待状态恢复。`;}
  if(active===run && !run.ended)run.timer=setTimeout(()=>void poll(run),750);
}
function open(data) {
  if(active)return;
  const run=active={selection:window.inboxRun.selection(),ended:false};
  uiNext.notice('');$('run-wash').hidden=true;$('run-stage-layer').hidden=false;
  dialog.classList.remove('returning');dialog.querySelector('.run-background').hidden=false;
  run.visual=new RunVisual(dialog,reduced.matches);
  dialog.showModal();showState(data);void run.visual.start();
  if(data.snapshot.status==='FINISHED')void finish(run,data);else void poll(run);
}
async function start() {
  if(active || !window.inboxRun?.available())return;
  $('run-start').disabled=true;
  try {open(await uiNext.request('/api/ui-next/run',{}));}
  catch(error){notice('Run 启动失败',resultDetails(error.state?.snapshot || {}).concat(error.message));$('run-start').disabled=false;}
}
$('run-start').addEventListener('click',()=>void start());
dialog.addEventListener('cancel',event=>event.preventDefault());
reduced.addEventListener('change',()=>{if(reduced.matches)active?.visual.stop();});
$('retry-inbox').addEventListener('click',async()=>{
  $('retry-inbox').disabled=true;
  try{await window.inboxRun.prepare(null,pending);pending=null;uiNext.notice('Inbox 已刷新。');}
  catch(error){notice('Inbox 尚未准备完成',[error.message]);}
  finally{$('retry-inbox').disabled=false;}
});
async function restore() {
  try {
    const data=await uiNext.request('/api/ui-next/run');
    if(data.snapshot.status==='RUNNING'){
      $('run-start').disabled=true;
      if(!$('kept-flow').open)open(data);
    }
    else if(data.snapshot.status==='FINISHED')notice(`Run · ${data.snapshot.result?.created_papers || 0} new papers`,resultDetails(data.snapshot));
  }catch(error){uiNext.notice(error.message);}
}
if(window.uiWorkspace.ready())void restore();else window.addEventListener('inbox-ready',()=>void restore(),{once:true});
window.addEventListener('pagehide',()=>{if(active){clearTimeout(active.timer);active.visual.stop();}});
$('kept-flow').addEventListener('close',()=>{if(!active)void restore();});
