import argparse
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .ingest import energy_charts,eia930,eia_stress,weather
from .ingest.common import iso,ATTRIBUTIONS
from .ingest.validate import validate,validate_regions
from .store.parquet_writer import write,read
from .carbon.intensity import hourly,FACTORS
from .forecast.baselines import forecast,backtest
from .sdk import best_window


def publish(store,output,health=None):
    rows=read(store);measured=hourly(rows);now=datetime.now(timezone.utc)
    health_path=Path(store).parent/'health.json'
    if health is None: health=json.loads(health_path.read_text()) if health_path.exists() else {'status':'unknown','errors':{}}
    regions=[]
    definitions=[('DE','Germany','Frankfurt','DE-LU'),('US-CAISO','California','US West','CISO'),('US-ERCOT','Texas','US Central','ERCO'),('US-PJM','Mid-Atlantic','US East','PJM')]
    for key,name,location,zone in definitions:
        history=[r for r in measured if r['region']==key and r['timestamp']<iso(now.replace(minute=0,second=0,microsecond=0))]
        base=dict(id=key,name=name,location=location,zone=zone,attribution=[ATTRIBUTIONS['energy-charts'],ATTRIBUTIONS['smard']] if key=='DE' else [ATTRIBUTIONS['eia']])
        if len(history)<48 or (now-datetime.fromisoformat(history[-1]['timestamp'].replace('Z','+00:00'))).total_seconds()>3*86400:
            regions.append(dict(**base,status='unavailable',reason='EIA_API_KEY required or sufficient recent data unavailable',forecast=[],history=[]))
            continue
        # Prices are allowed ahead of generation only when actually published by source.
        from collections import defaultdict
        prices=defaultdict(list)
        for row in rows:
            if row['region']==key and row['metric']=='price':
                t=datetime.fromisoformat(row['timestamp_utc'].replace('Z','+00:00')).replace(minute=0,second=0,microsecond=0)
                prices[iso(t)].append(row['value'])
        known={t:sum(v)/len(v) for t,v in prices.items() if len(v) in (1,4)}
        outlook=forecast(history,now,known_prices=known)
        regions.append(dict(**base,status='available',now=history[-1],history=history,forecast=outlook,scorecard=backtest(history)))
    if not any(r['status']=='available' for r in regions):
        raise ValueError('No recent usable regions; preserving last published snapshot')
    from .gridflex.signals import build_signals
    from .gridflex.simulator import simulate, Fleet
    flex_regions = {}
    for region in regions:
        if region['status'] == 'available':
            flex_regions[region['id']] = build_signals(region['id'], region['history'], region['forecast'], rows)
            if region['id'] in health.get('errors',{}):
                flex_regions[region['id']]['quarantined']=True
                for signal in flex_regions[region['id']]['forecast']:
                    signal['level']=None;signal['label']='Unknown'
                    signal['reasons']=['Regional source quarantined; live flex request suppressed']
    examples = {}
    for key, signals in flex_regions.items():
        replay = signals['history'][-168:]
        if replay and all(hour['level'] is not None for hour in replay):
            try:
                examples[key] = simulate(replay, Fleet(gpus=1000))
                examples[key]['region'] = key
                examples[key]['attribution'] = next(r['attribution'] for r in regions if r['id'] == key)
                examples[key]['factorVersion'] = FACTORS['version']
            except ValueError: pass
    data={'generatedAt':iso(now),'version':'0.2.0','basis':'Production-based average lifecycle gCO2e/kWh; excludes imports and pumped-storage output. Not marginal carbon.',
          'dataHealth':health,'attribution':list(ATTRIBUTIONS.values()),'factorVersion':FACTORS['version'],'regions':regions,
          'gridflex': {'regions': flex_regions, 'examples': examples, 'kind': 'heuristic signals + simulated fleets'},
          'ledger':{'executedJobs':0,'verifiedSavingsKg':0,'entries':[],'note':'No public execution telemetry connected. Browser plans are local and not counted as savings.'}}
    root=Path(output);root.mkdir(parents=True,exist_ok=True)
    staged=root/'status.tmp';staged.write_text(json.dumps(data,separators=(',',':'),allow_nan=False));staged.replace(root/'status.json')
    flex_path = root/'gridflex'; flex_path.mkdir(exist_ok=True)
    (flex_path/'signals.json').write_text(json.dumps({'generatedAt': data['generatedAt'], 'attribution':data['attribution'], 'regions':flex_regions},separators=(',',':'),allow_nan=False))
    (flex_path/'simulations.json').write_text(json.dumps({'generatedAt':data['generatedAt'], 'attribution':data['attribution'], 'kind':'simulation', 'examples':examples},separators=(',',':'),allow_nan=False))
    for region in regions:
        path=root/region['id'];path.mkdir(exist_ok=True)
        for file,value in [('now',region.get('now')),('forecast',region['forecast']),('scorecard',region.get('scorecard'))]:
            (path/f'{file}.json').write_text(json.dumps({'region':region['id'],'status':region['status'],'generatedAt':data['generatedAt'],'attribution':region['attribution'],'basis':data['basis'],'data':value},separators=(',',':')))
    print(f'Published {sum(r["status"]=="available" for r in regions)} regions, {len(measured)} measured hourly records.')
    return data


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    update=sub.add_parser('update');update.add_argument('--days',type=int,default=14);update.add_argument('--store',default='data/observations');update.add_argument('--output',default='web/public/api/v1');update.add_argument('--with-us',action='store_true');update.add_argument('--with-weather',action='store_true')
    local=sub.add_parser('publish');local.add_argument('--store',default='data/observations');local.add_argument('--output',default='web/public/api/v1')
    plan=sub.add_parser('plan');plan.add_argument('--region',default='DE');plan.add_argument('--hours',type=int,default=3);plan.add_argument('--energy',type=float,default=1);plan.add_argument('--deadline',required=True);plan.add_argument('--objective',choices=['co2','cost','balanced'],default='co2')
    sim=sub.add_parser('flex-sim', help='Replay measured grid history through a simulated GPU fleet')
    sim.add_argument('--input',type=Path,default=Path('web/public/api/v1/status.json'));sim.add_argument('--output',type=Path)
    sim.add_argument('--region',default='DE');sim.add_argument('--gpus',type=int,default=1000);sim.add_argument('--hours',type=int,default=168)
    sim.add_argument('--mode',choices=['hybrid','power-cap','checkpoint'],default='hybrid')
    args=parser.parse_args()
    if args.command=='flex-sim':
        from .gridflex.simulator import simulate,Fleet
        if not 1<=args.hours<=8760: parser.error('--hours must be between 1 and 8760')
        data=json.loads(args.input.read_text());history=data['gridflex']['regions'][args.region]['history']
        if args.hours>len(history): parser.error('Requested replay exceeds available history')
        result=simulate(history[-args.hours:],Fleet(gpus=args.gpus,mode=args.mode))
        result['region']=args.region;result['attribution']=data['attribution'];result['factorVersion']=data['factorVersion']
        output=json.dumps(result,indent=2,allow_nan=False)
        if args.output: args.output.write_text(output+'\n')
        else: print(output)
        return
    if args.command=='plan':
        print(json.dumps(best_window(region=args.region,duration_h=args.hours,energy_kWh=args.energy,deadline=args.deadline,objective=args.objective),indent=2));return
    if args.command=='update':
        now=datetime.now(timezone.utc);start=(now-timedelta(days=args.days)).strftime('%Y-%m-%d');end=now.strftime('%Y-%m-%d')
        rows=[];source_errors={}
        try:rows+=energy_charts.ingest(start,end)
        except Exception as exc:source_errors['DE']=str(exc)
        if args.with_us:
            try:rows+=eia930.ingest(start,end)+eia_stress.ingest(start,end)
            except Exception as exc:
                for region in ('US-CAISO','US-ERCOT','US-PJM'):source_errors[region]=str(exc)
        if args.with_weather:
            try:rows+=weather.ingest()
            except Exception as exc:source_errors['weather']=str(exc)
        accepted,errors=validate_regions(rows);errors.update(source_errors)
        # Any failed region retains its last archived observations.
        accepted=[row for row in accepted if row['region'] not in errors]
        if not accepted:raise ValueError('No valid regions in refresh; preserving last published snapshot: '+str(errors))
        write(accepted,args.store)
        health={'status':'partial' if errors else 'healthy','checkedAt':iso(now),'errors':errors,
                'updatedRegions':sorted({row['region'] for row in accepted}),
                'policy':'Invalid regions quarantined; their last good archived measurements retained. No missing values filled.'}
        data=publish(args.store,args.output,health)
        health_path=Path(args.store).parent/'health.json';health_path.parent.mkdir(parents=True,exist_ok=True)
        health_path.write_text(json.dumps(health,indent=2)+'\n')
        print('Refresh health:',health['status'],json.dumps(errors))
        return
    publish(args.store,args.output)


if __name__=='__main__':main()
