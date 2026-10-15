import {KeptScene,shots,loops} from './kept/scene';

// One-shot scenes and recorded loops share the same geometry and source clock.
export class KeptPlayer {
  constructor(root,metadata,name,reduced,onEnd) {
    Object.assign(this,{root,metadata,name,reduced,onEnd});
    this.sceneHost=root.querySelector('#kept-scene');this.scene=new KeptScene(this.sceneHost);
    this.videos=[...root.querySelectorAll('video')];
    this.active=0;this.version=0;this.clip=null;this.stopped=false;this.completed=false;this.presentedTime=0;
    for(const video of this.videos){video.pause();video.style.opacity='0';}
    this.resize();
  }
  resize(){this.scene.scale(Math.min(innerWidth/1920,innerHeight/1080));}
  cancelFrames() {
    cancelAnimationFrame(this.animationFrame);
    if(this.videoFrame!==undefined){this.frameVideo.cancelVideoFrameCallback(this.videoFrame);this.videoFrame=undefined;}
  }
  finish(version) {
    if(this.stopped || version!==this.version || this.completed)return;
    this.completed=true;this.onEnd(this.clip);
  }
  cancelPreparation() {
    const pending=this.preparation;if(!pending)return;
    this.preparation=null;pending.cancelled=true;pending.release?.();
    pending.video.pause();pending.video.style.opacity='0';
  }
  prepare(clip) {
    this.cancelPreparation();
    const video=this.videos[1-this.active];
    video.onerror=video.ontimeupdate=null;video.loop=true;
    video.src=this.metadata.clips[clip].src;video.load();
    // Decode beneath the opaque live scene, matching Run's handoff order.
    video.style.zIndex='0';video.style.opacity='1';
    const pending=this.preparation={clip,video,cancelled:false,failed:false,release:null};
    pending.task=this.decode(pending);return pending;
  }
  async decode(pending) {
    const video=pending.video;
    pending.onError=()=>{pending.failed=true;pending.release?.();};video.onerror=pending.onError;
    try {
      const firstFrame=new Promise(resolve=>{
        let id;
        const finish=()=>{if(id!==undefined)video.cancelVideoFrameCallback(id);video.removeEventListener('loadeddata',finish);pending.release=null;resolve();};
        pending.release=finish;
        if(video.requestVideoFrameCallback)id=video.requestVideoFrameCallback(finish);
        else if(video.readyState>=2)finish();else video.addEventListener('loadeddata',finish,{once:true});
      });
      await video.play();await firstFrame;
      if(pending.cancelled || pending.failed || this.stopped)return false;
      video.pause();
      if(video.currentTime!==0)await new Promise(resolve=>{
        const finish=()=>{video.removeEventListener('seeked',finish);pending.release=null;resolve();};
        pending.release=finish;video.addEventListener('seeked',finish,{once:true});video.currentTime=0;
      });
      return !pending.cancelled && !pending.failed && !this.stopped;
    } catch {return false;}
    finally {if(video.onerror===pending.onError)video.onerror=null;pending.release?.();}
  }
  draw(time) {this.scene.render(this.clip,time,this.name,this.processingExitTime);}
  staticLoop() {
    this.cancelFrames();this.failed=true;
    for(const video of this.videos){video.pause();video.style.opacity='0';video.ontimeupdate=null;}
    this.draw(this.presentedTime);this.sceneHost.style.opacity='1';
  }
  async play(clip,offset=0,stopAt=shots[clip]?.duration) {
    if(this.stopped || (this.clip===clip && this.stopAt===stopAt))return;
    const processingExitTime=this.clip==='processing'?this.presentedTime:null;
    this.cancelFrames();
    if(this.preparation?.clip!==clip)this.cancelPreparation();
    for(const video of this.videos)if(video!==this.preparation?.video){video.pause();video.onerror=video.ontimeupdate=null;video.style.opacity='0';}
    this.clip=clip;this.stopAt=stopAt;this.completed=false;this.failed=false;this.processingExitTime=processingExitTime;
    const version=++this.version;
    if(shots[clip]) {
      this.liveTime=this.reduced?stopAt:offset;this.draw(this.liveTime);this.sceneHost.style.opacity='1';
      if(this.reduced){queueMicrotask(()=>this.finish(version));return;}
      const started=performance.now()-offset*1000;
      const frame=now=>{
        if(this.stopped || version!==this.version)return;
        this.liveTime=Math.min(stopAt,(now-started)/1000);this.draw(this.liveTime);
        if(this.liveTime>=stopAt)this.finish(version);
        else this.animationFrame=requestAnimationFrame(frame);
      };
      this.animationFrame=requestAnimationFrame(frame);
      const next={identity:'processing',scan:'scan-loop'}[clip];if(next)this.prepare(next);
      return;
    }
    this.presentedTime=offset;this.draw(offset);this.sceneHost.style.opacity='1';
    if(this.reduced)return;
    const pending=this.preparation || this.prepare(clip),ready=await pending.task;
    if(this.stopped || version!==this.version)return;
    if(this.preparation===pending)this.preparation=null;
    if(!ready){this.staticLoop();return;}
    const video=pending.video;
    this.active=this.videos.indexOf(video);video.style.zIndex='1';video.style.opacity='1';
    video.onerror=()=>{if(version===this.version)this.staticLoop();};
    if(offset)video.currentTime=offset;
    const presented=(_now,info)=>{
      if(this.stopped || version!==this.version || this.failed)return;
      this.presentedTime=info.mediaTime;
      this.videoFrame=video.requestVideoFrameCallback(presented);
    };
    if(video.requestVideoFrameCallback){this.frameVideo=video;this.videoFrame=video.requestVideoFrameCallback(presented);}
    else video.ontimeupdate=()=>{this.presentedTime=video.currentTime;};
    this.sceneHost.style.opacity='0';
    try {await video.play();}catch {if(version===this.version)this.staticLoop();}
  }
  setReduced(value) {
    this.reduced=value;if(!this.clip)return;
    if(value) {
      this.cancelPreparation();this.cancelFrames();
      for(const video of this.videos){video.pause();video.style.opacity='0';}
      if(shots[this.clip])this.liveTime=this.stopAt;
      this.draw(shots[this.clip]?this.liveTime:this.presentedTime);this.sceneHost.style.opacity='1';
      if(shots[this.clip])this.finish(this.version);
    } else if(loops[this.clip]) {
      const clip=this.clip,time=this.presentedTime;this.clip=null;void this.play(clip,time);
    }
  }
  resume() {
    if(this.stopped || this.reduced || this.failed || !loops[this.clip])return;
    const video=this.videos[this.active],version=this.version;
    if(this.preparation || !video.paused)return;
    void video.play().catch(()=>{if(version===this.version)this.staticLoop();});
  }
  stop() {
    this.stopped=true;this.version++;this.cancelFrames();this.cancelPreparation();
    for(const video of this.videos){video.pause();video.onerror=video.ontimeupdate=null;video.style.opacity='0';}
    this.scene.destroy();
  }
}
