const stamp=t=>new Date(t).getTime();
export function optimize(regions,job){
 const duration=Number(job.duration_h),energy=Number(job.energy_kWh??Number(job.power_kW)*duration);
 if(!Number.isInteger(duration)||duration<1||duration>48)throw Error('Run time must be a whole number of hours from 1 to 48.');
 if(!Number.isFinite(energy)||energy<=0)throw Error('Enter a positive energy requirement.');
 if(job.power_kW!==undefined&&Math.abs(job.power_kW*duration-energy)>1e-6)throw Error('Power and energy disagree.');
 if(!Array.isArray(job.allowed_regions)||!job.allowed_regions.length)throw Error('Choose at least one available region.');
 const start=stamp(job.earliest_start),deadline=stamp(job.deadline),objective=job.objective??'co2';
 if(!Number.isFinite(start)||!Number.isFinite(deadline)||deadline<=start)throw Error('Choose a future deadline after the earliest start.');
 if(!['co2','cost','balanced'].includes(objective))throw Error('Unknown scheduling objective.');
 const score=row=>{const ci=job.risk_averse?row.high:row.carbon;if(objective==='co2')return ci;if(row.price===null||row.price===undefined)return null;if(objective==='cost')return row.price;return .5*ci/500+.5*row.price/.1;};
 const result=(region,slots)=>({region,start:slots[0].timestamp,end:new Date(stamp(slots.at(-1).timestamp)+3600000).toISOString().replace('.000Z','Z'),slots:slots.map(r=>r.timestamp),energy_kWh:energy,expected_kg:slots.reduce((s,r)=>s+r.carbon,0)*energy/duration/1000,expected_cost_eur:slots.some(r=>r.price==null)?null:slots.reduce((s,r)=>s+r.price,0)*energy/duration,average_carbon:slots.reduce((s,r)=>s+r.carbon,0)/duration,score:slots.reduce((s,r)=>s+score(r),0)/duration,basis:'Forecast average lifecycle intensity; not causal avoided emissions'});
 const choices=[];let baseline=null;
 for(const region of job.allowed_regions){const eligible=(regions[region]??[]).filter(r=>stamp(r.timestamp)>=start&&stamp(r.timestamp)+3600000<=deadline&&score(r)!==null).sort((a,b)=>stamp(a.timestamp)-stamp(b.timestamp));
  for(let i=0;i<=eligible.length-duration;i++){const slots=eligible.slice(i,i+duration);if(slots.some((r,j)=>j&&stamp(r.timestamp)-stamp(slots[j-1].timestamp)!==3600000))continue;const p=result(region,slots);if(region===job.allowed_regions[0]&&!baseline)baseline=p;if(!job.interruptible)choices.push(p);}
  if(job.interruptible&&eligible.length>=duration){const slots=[...eligible].sort((a,b)=>score(a)-score(b)||stamp(a.timestamp)-stamp(b.timestamp)).slice(0,duration).sort((a,b)=>stamp(a.timestamp)-stamp(b.timestamp));choices.push(result(region,slots));}
 }
 if(!choices.length)throw Error('No complete window before this deadline. Price mode needs published prices for every selected hour; try carbon or extend your deadline.');
 choices.sort((a,b)=>a.score-b.score||stamp(a.start)-stamp(b.start)||a.region.localeCompare(b.region));
 const best=choices[0];return {...best,baseline,estimated_savings_kg:baseline?baseline.expected_kg-best.expected_kg:null,estimated_savings_eur:baseline?.expected_cost_eur!=null&&best.expected_cost_eur!=null?baseline.expected_cost_eur-best.expected_cost_eur:null,interruptible:!!job.interruptible,objective,risk_averse:!!job.risk_averse};
}
export function reconcile(plan,measured,executed=false){if(!executed)return {status:'awaiting-execution',verifiedSavingsKg:null};if(!plan.baseline)return {status:'missing-counterfactual',verifiedSavingsKg:null};const values=(region,slots)=>{const map=new Map((measured[region]??[]).map(r=>[r.timestamp,r]));return slots.every(t=>map.has(t))?slots.map(t=>map.get(t)):null;};const actual=values(plan.region,plan.slots),counter=values(plan.baseline.region,plan.baseline.slots);if(!actual||!counter)return {status:'awaiting-measurements',verifiedSavingsKg:null};const power=plan.energy_kWh/plan.slots.length,actualKg=actual.reduce((s,r)=>s+r.carbon,0)*power/1000,baselineKg=counter.reduce((s,r)=>s+r.carbon,0)*power/1000;return {status:'reconciled',actualKg,baselineKg,verifiedSavingsKg:baselineKg-actualKg,costDifferenceEur:actual.concat(counter).every(r=>r.price!=null)?power*(counter.reduce((s,r)=>s+r.price,0)-actual.reduce((s,r)=>s+r.price,0)):null};}
