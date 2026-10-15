// Blog Theme 6f1e5b0 source geometry with visible-symmetry cycles.
import {bootMotion} from './vendor/boot-motion';
const tau=2*Math.PI,sourceFPS=25,sourceStart=479,blendStart=548,sourceEnd=568;
const at=(frame:number)=>bootMotion(frame/sourceFPS-5,'');
const end=at(sourceEnd),leadSeconds=(blendStart-sourceStart)/sourceFPS;
const introFrames=Math.ceil((leadSeconds+1.8)*60-1e-8),introSeconds=introFrames/60;
const bridgeSeconds=introSeconds-leadSeconds,sourceTailSeconds=(sourceEnd-blendStart)/sourceFPS;
export const scanCycle={duration:8,introFrames,introSeconds,bridgeSeconds,
  periods:{orange:2,doubleGap:8/3,side:1.6,satellites:1},satelliteRadius:4.25,
  satelliteMotion:{degreesPerSecond:-60,bridgeSeconds:.4}};
const smooth=(p:number)=>p*p*p*(10+p*(-15+6*p));
// Match source clock speed 1 -> 0 and acceleration 0 -> 0. The monotone
// clock preserves the measured trajectories and lands on the reference pose.
function sourceIntroState(time:number) {
  if(time<leadSeconds)return at(sourceStart+time*sourceFPS);
  const p=Math.max(0,Math.min(1,(time-leadSeconds)/bridgeSeconds)),d=bridgeSeconds,s=sourceTailSeconds;
  const clock=d*p+(10*s-6*d)*p**3+(8*d-15*s)*p**4+(6*s-3*d)*p**5;
  const state=at(Math.min(sourceEnd,blendStart+clock*sourceFPS)),weight=smooth(p);
  state.scanOrbit.satellites=state.scanOrbit.satellites.map(dot=>({...dot,radius:dot.radius+(scanCycle.satelliteRadius-dot.radius)*weight}));
  return state;
}
const satelliteSpeed=scanCycle.satelliteMotion.degreesPerSecond*Math.PI/180;
const satelliteBridge=scanCycle.satelliteMotion.bridgeSeconds,satelliteStartTime=introSeconds-satelliteBridge;
const angleOf=(state:ReturnType<typeof bootMotion>)=>{
  const point=state.scanOrbit.satellites[0];return Math.atan2(point.y-539.5,point.x-959.5);
};
const satelliteStartAngle=angleOf(sourceIntroState(satelliteStartTime));
const epsilon=.0001;
const satelliteStartSpeed=(satelliteStartAngle-angleOf(sourceIntroState(satelliteStartTime-epsilon)))/epsilon;
// Integral of the quintic smoothstep velocity blend. Its endpoints preserve
// position and velocity without introducing a recurring stop in the loop.
const satelliteAngle=(p:number)=>satelliteStartAngle+satelliteBridge*(satelliteStartSpeed*p+
  (satelliteSpeed-satelliteStartSpeed)*(p**6-3*p**5+2.5*p**4));
const satelliteLoopAngle=satelliteAngle(1);
function satelliteOrbit(points:ReturnType<typeof bootMotion>['scanOrbit']['satellites'],angle:number) {
  const distance=Math.hypot(points[0].x-959.5,points[0].y-539.5);
  return points.map((point,i)=>({...point,x:959.5+Math.cos(angle+i*tau/6)*distance,y:539.5+Math.sin(angle+i*tau/6)*distance}));
}
export function scanIntroState(time:number) {
  const state=sourceIntroState(time);
  if(time>=satelliteStartTime) {
    const p=Math.min(1,(time-satelliteStartTime)/satelliteBridge);
    state.scanOrbit.satellites=satelliteOrbit(state.scanOrbit.satellites,satelliteAngle(p));
  }
  return state;
}
// Each zero-speed phase is the reference pose modulo the shape's symmetry.
const advance=(time:number,period:number,degrees:number)=>{
  const p=time/period;return degrees*Math.PI/180*(p-Math.sin(tau*p)/tau);
};
export function scanLoopState(time:number) {
  const scan={...end.scan,orbit:end.scan.orbit-advance(time,2,180),innerStart:end.scan.innerStart+advance(time,8/3,180)};
  const sides=end.scanOrbit.sides.map((side,i)=>({...side,start:side.start+(i?-1:1)*advance(time,1.6,360)}));
  const satellites=satelliteOrbit(end.scanOrbit.satellites,satelliteLoopAngle+satelliteSpeed*time)
    .map(point=>({...point,radius:scanCycle.satelliteRadius}));
  return {...end,scan,scanOrbit:{...end.scanOrbit,sides,satellites}};
}
