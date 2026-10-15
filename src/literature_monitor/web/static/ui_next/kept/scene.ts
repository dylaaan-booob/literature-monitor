// Shared Blog Theme 6f1e5b0 2D scene, used live and by the loop recorder.
import {BootSequence} from './vendor/boot';
import {logo,brandHeading} from './vendor/brand';
import {scanCycle,scanIntroState,scanLoopState} from './scan-loop';
import {processingCycle,processingCut} from './processing-loop';
import './scene.css';
export {scanCycle,processingCycle};
export const shots={access:{duration:2.04},identity:{duration:8.92},scan:{duration:scanCycle.introSeconds},welcome:{duration:4.16,holdAt:(650-569)/25}};
export const loops={processing:{duration:processingCycle.duration},'scan-loop':{duration:scanCycle.duration}};
export type ScenePart=keyof typeof shots | keyof typeof loops;
export const sceneReady=()=>Promise.all([400,600,700].map(weight=>document.fonts.load(`${weight} 22px "Kept Film"`))).then(()=>document.fonts.ready);
export class KeptScene {
  root:HTMLElement;
  private sequence:BootSequence;
  constructor(host:HTMLElement,scale=1) {
    this.root=document.createElement('div');this.root.className='kept-reference';host.replaceChildren(this.root);this.scale(scale);
    this.root.innerHTML=`
      <div id="boot-background" class="boot-background"><svg viewBox="0 0 1920 1080" preserveAspectRatio="none"><g fill="none" stroke="#fff" stroke-width="3"><path d="M-210 705C-45 705 182 704 247 567C337 377 99 306 4 435S27 680 169 631C309 584 227 314 279 111S568-113 568-113"/><path d="M1560-80C1374 114 1671 168 1601 323S1371 367 1431 480S1692 666 1559 787S1329 886 1498 1130"/><circle cx="1450" cy="648" r="346"/><circle cx="1450" cy="648" r="348"/></g></svg></div>
      <header class="brand">${brandHeading}</header>
      <div class="access-text"></div><div class="boot-logo">${logo}</div>
      <div class="auth-status"><span>▪</span><span id="auth-message"></span></div>
      <div class="scan"><svg viewBox="0 0 1920 1080"><g fill="none" stroke="#080a08" stroke-width="2" stroke-linecap="round"><path/><path stroke="#fff"/><path/><path/><path/><path/><circle class="orbit-dot" r="8" fill="#ed821b" stroke="none"/><circle class="orbit-dot" r="8" fill="#ed821b" stroke="none"/><circle class="scan-core" cx="960" cy="540" r="5" fill="#080a08" stroke="none"/></g></svg><span hidden></span></div>
      <div class="powered">POWERED BY <b>RHINE LAB</b><i></i></div>
      <div class="welcome"><div class="welcome-panel"></div><div class="welcome-heading">WELCOME TO</div><div class="welcome-company"><strong>RHINE LAB.LLC.</strong><strong class="welcome-highlight" aria-hidden="true">RHINE LAB.LLC.</strong></div><div class="welcome-database">INTERNAL DATABASE</div><div class="welcome-logo">${logo}</div></div>
      <div class="boot-white"></div>`;
    this.sequence=new BootSequence(this.root);
  }
  scale(value:number) {this.root.style.setProperty('--kept-scene-scale',String(value));}
  render(part:ScenePart,time:number,name='',processingExitTime:number|null=null) {
    if(part==='scan' && time<=0 && processingExitTime!==null){this.render('processing',processingExitTime,name);return;}
    const scan=part==='scan'?scanIntroState(time):part==='scan-loop'?scanLoopState(time):null;
    const frame=scan?scan.t*25:part==='processing'?449:part==='access'?Math.min(225,175+time*25):part==='identity'?Math.min(449,226+time*25):Math.min(673,569+time*25);
    this.sequence.update(frame/25-5,'');
    if(frame<363) {
      const count=Math.max(0,Math.min(13,1+Math.floor((frame-281)*12/14)));
      const nameCount=frame>=321?Math.min(name.length,1+Math.floor((frame-321)*(name.length-1)/18)):0;
      this.sequence.setAuthText('LOCAL PROFILE'.slice(0,count)+(frame>=320?' : ':'')+name.slice(0,nameCount));
    } else if(part==='processing') {
      this.sequence.setAuthText('START PROCESSING'+'.'.repeat(1+Math.floor((time+1e-8)/.4)%3));
      this.sequence.setLogoCut(processingCut(time));
    }
    if(part==='scan' && frame<487 && processingExitTime!==null)this.sequence.setLogoCut(processingCut(processingExitTime+time));
    if(scan)this.sequence.renderScan(scan);
    const backgroundFrame=part==='welcome'?frame:part==='scan-loop'?568:part==='scan'?449+(568-449)*Math.min(1,time/scanCycle.introSeconds):Math.min(frame,449);
    const t=backgroundFrame/25;
    this.root.querySelector<HTMLElement>('.boot-background svg')!.style.transform=`translate(${Math.sin(t*.16)*18}px,${-(t-6)*5}px) scale(1.08)`;
  }
  destroy(){this.root.remove();}
}
