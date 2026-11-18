import copy
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
from gridpulse.gridflex.signals import stress_level,build_signals
from gridpulse.gridflex.policies import policy
from gridpulse.gridflex.simulator import Fleet,simulate
from gridpulse.gridflex.operator import plan_job,reconcile,signal_level,published_level,PREFIX
from gridpulse.gridflex.kepler import energy_counter,KeplerSample
from gridpulse.gridflex.gpu_agent import bounded_cap
from gridpulse.ingest.eia_stress import parse
from gridpulse.ingest.common import iso,record
NOW=datetime(2026,11,18,12,tzinfo=timezone.utc)


def hours(level=0,n=48):
    return [{'timestamp':iso(NOW+timedelta(hours=i)),'level':level if isinstance(level,int) else level[i%len(level)],
             'carbon':300+i%12*5,'price':.1+i%6*.01} for i in range(n)]


def training(parallelism=1):
    return {'metadata':{'name':'train','labels':{PREFIX+'flex-enabled':'true',PREFIX+'workload':'training',PREFIX+'preemptible':'true'},'annotations':{},'resourceVersion':'1'},
            'spec':{'parallelism':parallelism,'suspend':False}}


def merge(job,patch):
    for section,values in patch.items():
        if section=='metadata':
            job['metadata'].setdefault('annotations',{}).update(values.get('annotations',{}))
        else:job.setdefault(section,{}).update(values)
    return job


def test_proxy_thresholds_and_no_invented_reserve():
    assert stress_level(price=-.1,renewable=80)['level']==0
    assert stress_level(price=.12)['level']==1
    assert stress_level(price=.20)['level']==2
    assert stress_level(price=.30)['level']==3
    assert stress_level(demand_mw=106,demand_forecast_mw=100)['level']==2
    assert stress_level(demand_mw=100,demand_forecast_mw=110)['level']==0
    assert stress_level(demand_mw=100,demand_forecast_mw=100)['reserveMarginFraction'] is None
    assert stress_level(demand_mw=100,available_capacity_mw=102)['level']==3
    assert stress_level()['level'] is None
    with pytest.raises(ValueError):stress_level(demand_mw=float('nan'))


def test_eia_demand_contract_and_hour_ending():
    payload={'response':{'data':[{'respondent':'CISO','type':'D','value':'20000','value-units':'megawatthours','period':'2026-11-18T12'}]}}
    result=parse(payload,NOW)
    assert result[0]['metric']=='demand'
    assert result[0]['timestamp_utc']=='2026-11-18T11:00:00Z'
    payload['response']['data'][0]['value']='-1'
    with pytest.raises(ValueError):parse(payload,NOW)


def test_future_stress_reference_excludes_future_actuals():
    history=[{'timestamp':iso(NOW-timedelta(days=1)),'carbon':200,'renewable':50,'price':None}]
    outlook=[{'timestamp':iso(NOW+timedelta(hours=h)),'carbon':200,'renewable':50,'price':None} for h in (0,24,48)]
    rows=[record(NOW-timedelta(days=1),'US-CAISO','demand','',100,'MW','eia',NOW),record(NOW-timedelta(days=1),'US-CAISO','demand_forecast','',100,'MW','eia',NOW)]
    expected=build_signals('US-CAISO',history,outlook,rows)
    poisoned=rows+[record(NOW+timedelta(days=1),'US-CAISO','demand','',500,'MW','eia',NOW),record(NOW+timedelta(days=1),'US-CAISO','demand_forecast','',100,'MW','eia',NOW)]
    assert build_signals('US-CAISO',history,outlook,poisoned)['forecast']==expected['forecast']
    assert all(s['level']==0 for s in expected['forecast'])


def test_inference_and_preemptibility_policies():
    for level in range(4):assert policy(level,workload='inference')['action']=='protected'
    assert policy(3,preemptible=False)['action']!='checkpoint-and-pause'
    assert policy(3,preemptible=True)['action']=='checkpoint-and-pause'
    p=policy(2,current_region='DE',allowed_regions=['DE','US-CAISO'],regional_levels={'US-CAISO':0,'US-PJM':0})
    assert p['migrationCandidate']=='US-CAISO' and p['migrationAutomatic'] is False
    assert policy(2,current_region='DE',allowed_regions=['DE'],regional_levels={'US-CAISO':0})['migrationCandidate'] is None
    with pytest.raises(ValueError):policy(True)


def test_no_stress_replay_is_identical():
    r=simulate(hours(),Fleet(gpus=1000))
    assert r['baseline']==r['controlled']
    assert r['metrics']['peakShedMW']==0
    assert r['metrics']['trainingThroughputLossPercent']==0
    assert r['annualExtrapolation'] is False


def test_pause_trades_work_for_power_and_keeps_inference():
    r=simulate(hours(3),Fleet(gpus=1000,preemptible_fraction=1))
    assert r['metrics']['inferenceProtected']
    assert r['metrics']['peakShedMW']>0
    assert r['controlled']['trainingGpuHours']==0
    assert r['controlled']['deadlineMisses']>r['baseline']['deadlineMisses']
    assert r['controlled']['pendingJobs']>0
    assert r['controlled']['energyKwh']>0


def test_fleet_scaling_and_measured_price_absence():
    small=simulate(hours([0,1,2,3]),Fleet(gpus=1000))
    large=simulate(hours([0,1,2,3]),Fleet(gpus=10000))
    assert large['metrics']['peakShedMW']==pytest.approx(small['metrics']['peakShedMW']*10)
    series=hours();series[0]['price']=None
    r=simulate(series)
    assert r['metrics']['costDifferenceEur'] is None
    assert r['baseline']['costEur'] is None


def test_checkpoint_costs_and_uncertain_inputs():
    trace=hours([0,3,0,2],24)
    a=simulate(trace,Fleet(checkpoint_minutes=0,resume_minutes=0))
    b=simulate(trace,Fleet(checkpoint_minutes=15,resume_minutes=5))
    assert b['controlled']['checkpointOverheadHours']>0
    assert b['controlled']['trainingGpuHours']<a['controlled']['trainingGpuHours']
    with pytest.raises(ValueError):simulate([dict(trace[0],level=None)])
    with pytest.raises(ValueError):simulate([trace[0],trace[2]])
    with pytest.raises(ValueError):simulate(trace,Fleet(gpus=10001))
    with pytest.raises(ValueError):simulate(trace,Fleet(pue=.9))


def test_operator_ignores_unlabelled_and_protects_inference():
    job=training();job['metadata']['labels']={}
    assert plan_job(job,3)['patch'] is None
    job=training();job['metadata']['labels'][PREFIX+'workload']='inference'
    assert plan_job(job,3)['action']=='protected'
    assert plan_job(job,3)['patch'] is None
    job=training();job['spec']['suspend']=True
    assert plan_job(job,0)['action']=='owner-suspended'


def test_cooperative_pause_and_new_ack_every_cycle():
    job=training();first=plan_job(job,3)
    assert first['action']=='awaiting-checkpoint' and 'spec' not in first['patch']
    merge(job,first['patch']);token=job['metadata']['annotations'][PREFIX+'policy-token']
    job['metadata']['annotations'][PREFIX+'checkpoint-ready']=token
    merge(job,plan_job(job,3)['patch']);assert job['spec']['suspend'] is True
    assert plan_job(job,3)['patch'] is None
    merge(job,plan_job(job,0)['patch']);assert job['spec']=={'parallelism':1,'suspend':False}
    second=plan_job(job,3)
    assert second['action']=='awaiting-checkpoint' and 'spec' not in second['patch']
    assert second['patch']['metadata']['annotations'][PREFIX+'policy-token']!=token


def test_multi_rank_needs_coordinator_and_stale_restores_only_managed():
    job=training(4)
    assert plan_job(job,3)['action']=='checkpoint-coordinator-required'
    assert plan_job(job,None)['patch'] is None
    merge(job,plan_job(job,1)['patch'])
    result=plan_job(job,None)
    assert result['patch']['spec']['parallelism']==4
    assert result['patch']['spec']['suspend'] is False


def test_dry_run_does_not_patch_and_apply_is_idempotent():
    class Client:
        def __init__(self):self.value=training();self.calls=[]
        def jobs(self):return [copy.deepcopy(self.value)]
        def patch(self,name,patch):self.calls.append(patch);merge(self.value,patch)
    client=Client();reconcile(client,1);assert client.calls==[]
    reconcile(client,1,apply=True);assert len(client.calls)==1
    assert client.calls[0]['metadata']['resourceVersion']=='1'
    reconcile(client,1,apply=True);assert len(client.calls)==1


def test_signal_freshness_unknown_and_published_selection():
    assert signal_level({'level':3,'generatedAt':iso(NOW)},NOW)==3
    assert signal_level({'level':True,'generatedAt':iso(NOW)},NOW) is None
    assert signal_level({'level':3,'generatedAt':iso(NOW-timedelta(days=1))},NOW) is None
    payload={'generatedAt':iso(NOW),'regions':[{'id':'DE','now':{'timestamp':iso(NOW-timedelta(hours=2))}}],
             'gridflex':{'regions':{'DE':{'forecast':[{'timestamp':iso(NOW),'level':2}]}}}}
    assert published_level(payload,'DE',NOW)==2
    payload['dataHealth']={'errors':{'DE':'missing readings'}}
    assert published_level(payload,'DE',NOW) is None
    payload.pop('dataHealth')
    payload['regions'][0]['now']['timestamp']=iso(NOW-timedelta(days=2))
    assert published_level(payload,'DE',NOW) is None


def test_kepler_namespace_counter_delta_and_reset():
    metrics='''# TYPE kepler_container_joules_total counter
kepler_container_joules_total{namespace="gridflex-demo",pod_name="train-a"} 120
kepler_container_joules_total{namespace="other",pod_name="train-b"} 999
kepler_container_joules_total{namespace="gridflex-demo",pod_name="train-c"} 30
'''
    assert energy_counter(metrics,'gridflex-demo')==150
    assert energy_counter(metrics,'missing') is None
    sampler=KeplerSample();assert sampler.observe(150,10)['powerWatts'] is None
    assert sampler.observe(250,12)['powerWatts']==50
    assert sampler.observe(1,13)['powerWatts'] is None


def test_real_gpu_adapter_bounds_without_hardware_execution():
    assert bounded_cap(700,200,700,.8)==560
    assert bounded_cap(700,200,700,.1)==200
    with pytest.raises(ValueError):bounded_cap(700,200,700,1.1)


def test_invalid_region_quarantined_without_weakening_gap_contract():
    from gridpulse.ingest.validate import validate_regions,validate
    rows=[record(NOW,'DE','generation','other',10,'MW','energy-charts',NOW),
          record(NOW+timedelta(hours=4),'DE','generation','other',10,'MW','energy-charts',NOW),
          record(NOW,'US-CAISO','generation','gas',100,'MW','eia',NOW),
          record(NOW+timedelta(hours=1),'US-CAISO','generation','gas',110,'MW','eia',NOW)]
    with pytest.raises(ValueError,match='gap'):validate(rows)
    accepted,errors=validate_regions(rows)
    assert set(errors)=={'DE'}
    assert {r['region'] for r in accepted}=={'US-CAISO'}
    assert len(accepted)==2
