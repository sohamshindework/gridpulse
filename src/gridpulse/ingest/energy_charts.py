from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from .common import fetch, record

FUELS = {'Hydro Run-of-River':'hydro', 'Hydro water reservoir':'hydro_reservoir',
         'Biomass':'biomass', 'Fossil brown coal / lignite':'lignite', 'Fossil hard coal':'coal',
         'Fossil oil':'oil', 'Fossil coal-derived gas':'coal_gas', 'Fossil gas':'gas',
         'Geothermal':'geothermal', 'Waste':'waste', 'Wind offshore':'wind_offshore',
         'Wind onshore':'wind_onshore', 'Solar':'solar', 'Others':'other', 'Nuclear':'nuclear'}
# Pumped-storage output is excluded: its charging mix is unknown; not a primary fuel.


def parse_power(payload, fetched_at):
    times=payload['unix_seconds']; result=[]
    for series in payload['production_types']:
        name=series['name']
        if name not in FUELS:
            if name.startswith(('Fossil ', 'Wind ')):
                raise ValueError(f'Unmapped production fuel: {name}')
            continue
        if len(series['data']) != len(times):
            raise ValueError('Power series length mismatch')
        for timestamp, value in zip(times,series['data']):
            if value is not None:
                result.append(record(timestamp,'DE','generation',FUELS[name],value,'MW','energy-charts',fetched_at))
    if not result:
        raise ValueError('No mapped generation')
    return result


def parse_prices(payload, fetched_at):
    if payload.get('unit') not in ('EUR / MWh','EUR/MWh'):
        raise ValueError('Unexpected price unit')
    if len(payload['unix_seconds']) != len(payload['price']):
        raise ValueError('Price series length mismatch')
    return [record(t,'DE','price','',v/1000,'EUR/kWh','energy-charts',fetched_at)
            for t,v in zip(payload['unix_seconds'],payload['price']) if v is not None]


def ingest(start,end):
    now=datetime.now(timezone.utc)
    power=fetch('https://api.energy-charts.info/public_power?'+urlencode(dict(country='de',start=start,end=end)))
    prices=fetch('https://api.energy-charts.info/price?'+urlencode(dict(bzn='DE-LU',start=start,end=(now+timedelta(days=2)).strftime('%Y-%m-%d'))))
    return parse_power(power,now)+parse_prices(prices,now)
