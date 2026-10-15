// Playback only. Three.js is used exclusively by tools/record-run.mjs.
export class RunVisual {
  constructor(root, reduced) {
    this.reduced=reduced;
    this.intro=root.querySelector('.run-intro');
    this.loop=root.querySelector('.run-loop');
    this.poster=root.querySelector('.run-poster');
    this.stopped=false;
    this.introFinished=false;
    this.loopStarting=false;
    this.loopReady=false;
    this.loopFailed=false;
    this.handoff=false;
    for(const video of [this.intro,this.loop]) {
      video.pause();video.currentTime=0;video.hidden=true;
    }
    this.intro.style.opacity='1';
    this.poster.hidden=!reduced;
    this.onTime=()=>{
      if(this.intro.duration-this.intro.currentTime<=.4) void this.prepareLoop();
    };
    this.onIntroEnd=()=>{this.introFinished=true;void this.prepareLoop();void this.showLoop();};
    this.onIntroError=()=>{
      if(this.stopped) return;
      this.intro.pause();this.intro.hidden=true;this.poster.hidden=false;
      this.onIntroEnd();
    };
    this.onLoopError=()=>{
      this.loopFailed=true;this.loopReady=false;
      this.loop.pause();this.loop.hidden=true;this.poster.hidden=false;
      this.cancelFrame();
      if(this.introFinished) this.intro.hidden=true;
    };
    this.intro.addEventListener('timeupdate',this.onTime);
    this.intro.addEventListener('ended',this.onIntroEnd);
    this.intro.addEventListener('error',this.onIntroError);
    this.loop.addEventListener('error',this.onLoopError);
  }
  async start() {
    if(this.reduced) return;
    try {
      await this.intro.play();
      if(this.stopped) {this.intro.pause();return;}
      this.intro.hidden=false;
    } catch {this.onIntroError();}
  }
  async prepareLoop() {
    if(this.stopped || this.reduced || this.loopStarting || this.loopFailed) return;
    this.loopStarting=true;
    try {
      await this.loop.play();
      if(this.stopped) {this.loop.pause();return;}
      // Decode under the intro, then hold that first frame until the handoff.
      this.loop.hidden=false;
      if(this.loop.requestVideoFrameCallback) {
        await new Promise(resolve=>{
          this.resolveFrame=resolve;
          this.videoFrame=this.loop.requestVideoFrameCallback(resolve);
        });
      }
      if(this.stopped || this.loopFailed) return;
      this.loop.pause();
      this.loop.currentTime=0;
      this.loopReady=true;void this.showLoop();
    } catch {if(!this.stopped) this.onLoopError();}
  }
  async showLoop() {
    if(this.stopped || !this.introFinished || this.handoff) return;
    if(this.loopFailed) {this.intro.hidden=true;return;}
    if(!this.loopReady) return;
    this.handoff=true;
    try {
      await this.loop.play();
      if(this.stopped) {this.loop.pause();return;}
    } catch {if(!this.stopped) this.onLoopError();return;}
    this.intro.style.opacity='0';
    this.fade=setTimeout(()=>{
      this.intro.pause();this.intro.hidden=true;
    },180);
  }
  cancelFrame() {
    if(this.videoFrame!==undefined) this.loop.cancelVideoFrameCallback?.(this.videoFrame);
    this.resolveFrame?.();this.resolveFrame=null;
  }
  stop() {
    this.stopped=true;
    clearTimeout(this.fade);this.cancelFrame();
    this.intro.pause();this.loop.pause();
    this.poster.hidden=false;
    this.intro.removeEventListener('timeupdate',this.onTime);
    this.intro.removeEventListener('ended',this.onIntroEnd);
    this.intro.removeEventListener('error',this.onIntroError);
    this.loop.removeEventListener('error',this.onLoopError);
  }
}
