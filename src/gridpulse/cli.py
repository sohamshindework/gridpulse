import argparse
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .ingest import energy_charts,eia930,weather
from .ingest.common import iso,ATTRIBUTIONS
from .ingest.validate import validate
from .store.parquet_writer import write,read
from .carbon.intensity import hourly,FACTORS
from .forecast.baselines import forecast,backtest
from .sdk import best_window


def publish(store,output):
    rows=read(store);measured=hourly(rows);now=datetime.now(timezone.utc)
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
    data={'generatedAt':iso(now),'version':'0.1.0','basis':'Production-based average lifecycle gCO2e/kWh; excludes imports and pumped-storage output. Not marginal carbon.',
          'attribution':list(ATTRIBUTIONS.values()),'factorVersion':FACTORS['version'],'regions':regions,
          'ledger':{'executedJobs':0,'verifiedSavingsKg':0,'entries':[],'note':'No public execution telemetry connected. Browser plans are local and not counted as savings.'}}
    root=Path(output);root.mkdir(parents=True,exist_ok=True)
    staged=root/'status.tmp';staged.write_text(json.dumps(data,separators=(',',':'),allow_nan=False));staged.replace(root/'status.json')
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
    args=parser.parse_args()
    if args.command=='plan':
        print(json.dumps(best_window(region=args.region,duration_h=args.hours,energy_kWh=args.energy,deadline=args.deadline,objective=args.objective),indent=2));return
    if args.command=='update':
        now=datetime.now(timezone.utc);start=(now-timedelta(days=args.days)).strftime('%Y-%m-%d');end=now.strftime('%Y-%m-%d')
        rows=energy_charts.ingest(start,end)
        if args.with_us:rows+=eia930.ingest(start,end)
        if args.with_weather:rows+=weather.ingest()
        validate(rows);write(rows,args.store)
    publish(args.store,args.output)


if __name__=='__main__':main()
