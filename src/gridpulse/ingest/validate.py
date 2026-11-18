import math
from collections import defaultdict
from .common import utc


def validate(rows, max_gap_h=3):
    if not rows:
        raise ValueError('Empty source data')
    keys=set(); timelines=defaultdict(set)
    for row in rows:
        time=utc(row['timestamp_utc']);utc(row['fetched_at'])
        key=(row['timestamp_utc'],row['region'],row['metric'],row['fuel'],row['source'])
        if key in keys:
            raise ValueError('Duplicate source interval')
        keys.add(key)
        value=row['value']
        if not math.isfinite(value):
            raise ValueError('Nonfinite value')
        if row['metric']=='generation' and not -100000<=value<=1e6:
            raise ValueError('Generation outside physical contract')
        if row['metric']=='price' and not -2<=value<=10:
            raise ValueError('Price outside EUR/kWh contract')
        timelines[(row['region'],row['metric'],row['fuel'],row['source'])].add(time)
    for times in timelines.values():
        ordered=sorted(times)
        if any((b-a).total_seconds()>max_gap_h*3600 for a,b in zip(ordered,ordered[1:])):
            raise ValueError('Source interval gap over configured limit')
    return rows
