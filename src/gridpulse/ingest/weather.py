from datetime import datetime, timezone
from urllib.parse import urlencode
from .common import fetch, record


def parse(payload,region,fetched_at):
    hourly=payload['hourly']; times=hourly['time']; result=[]
    for metric,unit in [('temperature_2m','°C'),('wind_speed_100m','km/h'),('shortwave_radiation','W/m²')]:
        if len(times)!=len(hourly[metric]):
            raise ValueError('Weather series length mismatch')
        for time,value in zip(times,hourly[metric]):
            if value is not None:
                result.append(record(time+'Z',region,metric,'',value,unit,'open-meteo',fetched_at))
    return result


def ingest(region='DE',latitude=51.16,longitude=10.45):
    params=dict(latitude=latitude,longitude=longitude,hourly='temperature_2m,wind_speed_100m,shortwave_radiation',timezone='UTC',forecast_days=3)
    return parse(fetch('https://api.open-meteo.com/v1/forecast?'+urlencode(params)),region,datetime.now(timezone.utc))
