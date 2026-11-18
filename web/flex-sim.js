// Browser counterpart of gridpulse.gridflex.simulator; tested against Python replay fixtures.
export function policy(level,preemptible=true,mode='hybrid'){
 if(![0,1,2,3].includes(level)||!['hybrid','power-cap','checkpoint'].includes(mode))throw Error('Invalid flex policy.');
 let [power,throughput,parallel]=[[1,1,1],[.85,.95,1],[.65,.80,.75],[.60,.72,.50]][level];
 const pause=level===3&&preemptible&&['hybrid','checkpoint'].includes(mode);
 if(mode==='power-cap')parallel=1;if(mode==='checkpoint'&&level<3)[power,throughput,parallel]=[1,1,1];
 return {action:pause?'checkpoint-and-pause':level===0?'normal':'power-cap-and-scale',powerFraction:pause?0:power,throughputFraction:pause?0:throughput,parallelismFraction:pause?0:parallel,checkpointRequired:pause||parallel<1};
}
export function simulate(hours,options={}){
 const cfg={gpus:1000,inference_fraction:.20,gpu_max_w:700,gpu_idle_w:100,inference_gpu_w:500,host_w_per_gpu:70,pue:1.2,checkpoint_minutes:5,resume_minutes:2,preemptible_fraction:.8,training_utilization:.85,training_tokens_per_gpu_second:1000,inference_tokens_per_gpu_second:1500,mode:'hybrid',...options};
 if(!Number.isInteger(cfg.gpus)||cfg.gpus<1||cfg.gpus>10000)throw Error('Choose 1–10,000 GPUs.');
 for(const [key,value]of Object.entries(cfg))if(typeof value==='number'&&(!Number.isFinite(value)||value<0))throw Error(`Invalid ${key}.`);
 if(cfg.inference_fraction>=1||cfg.preemptible_fraction>1||cfg.training_utilization<=0||cfg.training_utilization>1||cfg.gpu_max_w<cfg.gpu_idle_w||cfg.pue<1||cfg.inference_gpu_w>cfg.gpu_max_w)throw Error('Invalid fleet profile.');
 if(!hours.length||hours.length>8760)throw Error('Supply 1–8,760 measured grid hours.');
 hours.forEach((h,i)=>{if(![0,1,2,3].includes(h.level)||!Number.isFinite(h.carbon)||h.carbon<0||h.price!=null&&!Number.isFinite(h.price))throw Error('Unknown signal or invalid measured grid data.');if(i&&Date.parse(h.timestamp)-Date.parse(hours[i-1].timestamp)!==3600000)throw Error('Replay requires contiguous measured hours.');});
 // Python uses bankers rounding; inference populations from the UI are whole percentages of thousands.
 const rounded=cfg.gpus*cfg.inference_fraction;const inferenceGpus=Math.floor(rounded)+((rounded%1>.5||rounded%1===.5&&Math.floor(rounded)%2===1)?1:0),trainingGpus=cfg.gpus-inferenceGpus;if(trainingGpus<1)throw Error('Profile needs at least one training GPU.');
 const replay=controlled=>{let checkpointH=0,wasPaused=false,previousPolicy=null,completedWork=0,checkpointTotal=0,inferenceTokens=0;const jobs=[],trace=[];
 for(let h=0;h<hours.length;h++){const hour=hours[h];if(h%6===0){const index=jobs.length;jobs.push({id:index,remaining:trainingGpus*6*cfg.training_utilization,deadline:h+12,preemptible:((index*37)%100)/100<cfg.preemptible_fraction,completedAtHour:null});}
 let usedKwh=0,useful=0,infTokens=0;
 for(let sub=0;sub<12;sub++){const now=h+sub/12,pending=jobs.filter(j=>j.remaining>1e-8).sort((a,b)=>a.deadline-b.deadline||a.id-b.id),current=pending[0],decision=policy(controlled?hour.level:0,current?current.preemptible:true,cfg.mode),paused=!!current&&decision.action==='checkpoint-and-pause',key=decision.action+':'+decision.parallelismFraction;
 if(current&&key!==previousPolicy){const overhead=decision.checkpointRequired?cfg.checkpoint_minutes:wasPaused?cfg.resume_minutes:0;checkpointH+=overhead/60;checkpointTotal+=overhead/60;}previousPolicy=key;wasPaused=paused;
 const dt=1/12;let progress=0,busyFraction=0,cap=decision.powerFraction,activeDt=dt;
 if(checkpointH>0){const overhead=Math.min(dt,checkpointH);checkpointH-=overhead;busyFraction=overhead/dt;cap=1;activeDt=dt-overhead;}
 if(current&&!paused&&activeDt>0){const speed=decision.throughputFraction*decision.parallelismFraction;progress=Math.min(current.remaining,trainingGpus*speed*activeDt);const runningFraction=trainingGpus&&speed?progress/(trainingGpus*speed*dt):0;busyFraction+=runningFraction*decision.parallelismFraction;current.remaining-=progress;if(current.remaining<1e-8)current.completedAtHour=now+dt;}
 const trainingW=trainingGpus*(cfg.gpu_idle_w+(cfg.gpu_max_w-cfg.gpu_idle_w)*cap*busyFraction),facilityW=(trainingW+inferenceGpus*cfg.inference_gpu_w+cfg.gpus*cfg.host_w_per_gpu)*cfg.pue;usedKwh+=facilityW/1000*dt;useful+=progress;const tokens=inferenceGpus*cfg.inference_tokens_per_gpu_second*dt*3600;infTokens+=tokens;inferenceTokens+=tokens;
 }
 completedWork+=useful;trace.push({timestamp:hour.timestamp,level:hour.level,energyKwh:usedKwh,powerMW:usedKwh/1000,trainingGpuHours:useful,inferenceTokens:infTokens,carbonKg:usedKwh*hour.carbon/1000,costEur:hour.price==null?null:usedKwh*hour.price});
 }
 const energy=trace.reduce((s,r)=>s+r.energyKwh,0),deadlines=jobs.filter(j=>j.deadline<=hours.length);return {energyKwh:energy,carbonKg:trace.reduce((s,r)=>s+r.carbonKg,0),costEur:trace.every(r=>r.costEur!=null)?trace.reduce((s,r)=>s+r.costEur,0):null,trainingGpuHours:completedWork,inferenceTokens,usefulTokensPerKwh:energy?(completedWork*3600*cfg.training_tokens_per_gpu_second+inferenceTokens)/energy:null,deadlineMisses:deadlines.filter(j=>j.completedAtHour==null||j.completedAtHour>j.deadline+1e-6).length,deadlinesObserved:deadlines.length,pendingJobs:jobs.filter(j=>j.remaining>1e-8).length,checkpointOverheadHours:checkpointTotal,trace};};
 const baseline=replay(false),controlled=replay(true),deltas=baseline.trace.map((a,i)=>a.powerMW-controlled.trace[i].powerMW),requests=deltas.filter((_,i)=>hours[i].level>0),energyDiff=baseline.energyKwh-controlled.energyKwh,lost=baseline.trainingGpuHours-controlled.trainingGpuHours;
 return {kind:'simulation',version:'flexsim-v1',config:cfg,hours:hours.length,start:hours[0].timestamp,lastIntervalStart:hours.at(-1).timestamp,gridInputs:hours.map(({timestamp,level,carbon,price})=>({timestamp,level,carbon,price})),baseline,controlled,metrics:{peakShedMW:Math.max(0,...requests),meanShedDuringRequestsMW:requests.length?requests.reduce((a,b)=>a+b,0)/requests.length:0,requestHours:requests.length,netEnergyDifferenceKwh:energyDiff,carbonDifferenceKg:baseline.carbonKg-controlled.carbonKg,costDifferenceEur:baseline.costEur!=null&&controlled.costEur!=null?baseline.costEur-controlled.costEur:null,trainingThroughputLossPercent:baseline.trainingGpuHours?100*lost/baseline.trainingGpuHours:0,lostGpuHoursPerMwh:energyDiff>0?lost/(energyDiff/1000):null,inferenceProtected:Math.abs(baseline.inferenceTokens-controlled.inferenceTokens)<1e-5},annualExtrapolation:false};
}
