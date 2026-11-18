from datetime import timedelta
from math import isfinite
from ..ingest.common import utc, iso


def optimize(regions,job):
    duration=job.get('duration_h',1)
    if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not float(duration).is_integer() or not 1<=duration<=48:
        raise ValueError('duration_h must be an integer from 1 to 48')
    duration=int(duration)
    energy=job.get('energy_kWh')
    if energy is None and job.get('power_kW') is not None:
        energy=job['power_kW']*duration
    if not isinstance(energy,(int,float)) or not isfinite(energy) or energy<=0:
        raise ValueError('Provide positive finite energy_kWh or power_kW')
    if job.get('power_kW') is not None and abs(job['power_kW']*duration-energy)>1e-6:
        raise ValueError('energy_kWh and power_kW disagree')
    start=utc(job['earliest_start']);deadline=utc(job['deadline'])
    objective=job.get('objective','co2');risk=job.get('risk_averse',False);interruptible=job.get('interruptible',False)
    if objective not in ('co2','cost','balanced'):
        raise ValueError('objective must be co2, cost or balanced')
    allowed=job.get('allowed_regions')
    if not isinstance(allowed,list) or not allowed:
        raise ValueError('Provide allowed_regions; residency and transfer constraints are caller responsibilities')
    choices=[];baseline=None
    def score(row):
        carbon=row['high'] if risk else row['carbon']
        if objective=='co2':return carbon
        if row.get('price') is None:return None
        if objective=='cost':return row['price']
        # Explicit reference scales: 500 g/kWh and EUR .10/kWh, 50/50.
        return .5*carbon/500+.5*row['price']/.1
    def result(region,slots):
        price=None if any(r.get('price') is None for r in slots) else sum(r['price'] for r in slots)*energy/duration
        return {'region':region,'start':slots[0]['timestamp'],'end':iso(utc(slots[-1]['timestamp'])+timedelta(hours=1)),
                'slots':[r['timestamp'] for r in slots],'energy_kWh':energy,'expected_kg':sum(r['carbon'] for r in slots)*energy/duration/1000,
                'expected_cost_eur':price,'average_carbon':sum(r['carbon'] for r in slots)/duration,
                'score':sum(score(r) for r in slots)/duration,'basis':'forecast average lifecycle intensity; not causal avoided emissions'}
    for region in allowed:
        series=regions.get(region,[])
        eligible=sorted([r for r in series if start<=utc(r['timestamp']) and utc(r['timestamp'])+timedelta(hours=1)<=deadline and score(r) is not None],key=lambda r:r['timestamp'])
        for i in range(len(eligible)-duration+1):
            slots=eligible[i:i+duration]
            if any(utc(b['timestamp'])-utc(a['timestamp'])!=timedelta(hours=1) for a,b in zip(slots,slots[1:])):
                continue
            plan=result(region,slots)
            if region==allowed[0] and baseline is None:
                baseline=plan
            if not interruptible:
                choices.append(plan)
        if interruptible and len(eligible)>=duration:
            slots=sorted(sorted(eligible,key=lambda r:(score(r),r['timestamp']))[:duration],key=lambda r:r['timestamp'])
            choices.append(result(region,slots))
    if not choices:
        raise ValueError('No complete feasible window with required data before the deadline')
    best=min(choices,key=lambda r:(r['score'],r['start'],r['region']))
    best['baseline']=baseline
    best['estimated_savings_kg']=None if baseline is None else baseline['expected_kg']-best['expected_kg']
    best['estimated_savings_eur']=None if baseline is None or baseline['expected_cost_eur'] is None or best['expected_cost_eur'] is None else baseline['expected_cost_eur']-best['expected_cost_eur']
    best['interruptible']=bool(interruptible);best['objective']=objective;best['risk_averse']=bool(risk)
    return best
