import json
from urllib.request import urlopen
from datetime import datetime, timezone
from .scheduler.optimizer import optimize
DEFAULT_URL='https://sohamshindework.github.io/gridpulse/api/v1/status.json'


def best_window(region='DE',duration_h=3,deadline=None,energy_kWh=1,objective='co2',url=DEFAULT_URL,**kwargs):
    if deadline is None:
        raise ValueError('An explicit timezone-aware deadline is required')
    with urlopen(url,timeout=30) as response:
        data=json.load(response)
    now=datetime.now(timezone.utc)
    generated=datetime.fromisoformat(data['generatedAt'].replace('Z','+00:00'))
    if (now-generated).total_seconds()>6*3600:
        raise ValueError('Forecast snapshot is stale; refusing to schedule')
    available={r['id']:r['forecast'] for r in data['regions'] if r['status']=='available' and (now-datetime.fromisoformat(r['now']['timestamp'].replace('Z','+00:00'))).total_seconds()<=24*3600}
    return optimize(available,dict(duration_h=duration_h,energy_kWh=energy_kWh,deadline=deadline,
                                  earliest_start=now.isoformat(),allowed_regions=[region],objective=objective,**kwargs))
