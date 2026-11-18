import json
from pathlib import Path
from collections import defaultdict
from statistics import mean
from ..ingest.common import utc, iso
FACTORS=json.loads(Path(__file__).with_name('emission_factors.json').read_text())


def hourly(rows):
    """Require complete 15-minute or hourly generation samples within each hour.

    Keep fuel-level net negatives visible upstream; clip their carbon-engine
    contribution to zero, explicitly documenting this accounting convention.
    """
    bins=defaultdict(lambda:defaultdict(list)); prices=defaultdict(list)
    subhourly=set()
    for row in rows:
        time=utc(row['timestamp_utc']);hour=iso(time.replace(minute=0,second=0,microsecond=0));key=(row['region'],hour)
        if row['metric']=='generation':
            if time.minute != 0: subhourly.add(row['region'])
            bins[key][row['fuel']].append((time,row['value']))
        elif row['metric']=='price':
            prices[key].append(row['value'])
    # A known fuel cannot silently disappear in an hour and lower its intensity.
    expected=defaultdict(set)
    for (region,_),fuels in bins.items():
        expected[region].update(fuels)
    output=[]
    for (region,time),fuels in sorted(bins.items()):
        if fuels.keys()!=expected[region]:
            continue
        counts={len(samples) for samples in fuels.values()}
        if len(counts)!=1 or next(iter(counts)) != (4 if region in subhourly else 1):
            continue
        if next(iter(counts))==1 and any(samples[0][0].minute!=0 for samples in fuels.values()):
            continue
        mix={fuel:max(0,mean(v for _,v in samples)) for fuel,samples in fuels.items()}
        if not mix or sum(mix.values())<=0:
            continue
        if not mix.keys()<=FACTORS['factors'].keys():
            raise ValueError('Unmapped emission factor')
        total=sum(mix.values());ci=sum(value*FACTORS['factors'][fuel] for fuel,value in mix.items())/total
        output.append(dict(timestamp=time,region=region,carbon=round(ci,3),
                           renewable=round(100*sum(v for f,v in mix.items() if f in FACTORS['renewable'])/total,3),
                           price=round(mean(prices[region,time]),6) if prices[region,time] else None,
                           generationMW=round(total,2),clippedNegativeMW=round(sum(max(0,-mean(v for _,v in samples)) for samples in fuels.values()),2),fuelMix=mix))
    return output
