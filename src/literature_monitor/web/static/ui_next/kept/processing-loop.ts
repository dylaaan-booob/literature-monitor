// Complete the measured Blog Theme contour motion before looping it.
import {bootLogoTrack} from './vendor/boot-logo-tracks';

export const processingCycle={logoSeconds:2.6,dotsSeconds:1.2,duration:15.6};
const sourceStart=449,sourceEnd=486,knownSeconds=(sourceEnd-sourceStart)/25;
const first=bootLogoTrack(sourceStart).start,last=bootLogoTrack(sourceEnd).start;
const epsilon=.0001;
const firstSpeed=(bootLogoTrack(sourceStart+epsilon).start-first)*25/epsilon;
const lastSpeed=(last-bootLogoTrack(sourceEnd-epsilon).start)*25/epsilon;
const completionSeconds=processingCycle.logoSeconds-knownSeconds;

export function processingCut(time:number) {
  const phase=time%processingCycle.logoSeconds;
  if(phase<knownSeconds)return bootLogoTrack(sourceStart+phase*25).start;
  const p=(phase-knownSeconds)/completionSeconds,p2=p*p,p3=p2*p;
  // One complete contour later, both the gap and its speed match frame 449.
  return (2*p3-3*p2+1)*last+(p3-2*p2+p)*completionSeconds*lastSpeed
    +(-2*p3+3*p2)*(first+1)+(p3-p2)*completionSeconds*firstSpeed;
}
