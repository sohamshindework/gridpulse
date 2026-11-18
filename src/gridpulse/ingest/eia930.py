import os
from datetime import datetime, timezone
from urllib.parse import urlencode
from .common import fetch, record

FUELS={'GEO':'geothermal','BIO':'biomass','COL':'coal','NG':'gas','NUC':'nuclear','OIL':'oil','WAT':'hydro','SUN':'solar','WND':'wind','OTH':'other'}
RESPONDENTS={'CISO':'US-CAISO','ERCO':'US-ERCOT','PJM':'US-PJM'}


def parse(payload, fetched_at):
    rows=payload['response']['data']; result=[]
    for row in rows:
        fuel=row['fueltype']; region=RESPONDENTS.get(row['respondent'])
        if fuel in {'BAT', 'PS'}:
            continue  # Storage output has unknown charging emissions; excluded from primary mix.
        if not region or fuel not in FUELS:
            raise ValueError('Unknown EIA respondent or fuel')
        if row.get('value-units') not in ('megawatthours','megawatts'):
            raise ValueError('Unknown EIA unit')
        # EIA-930 hourly periods are UTC hour-ending interval labels.
        from datetime import timedelta
        from .common import utc
        time=utc(row['period']+':00:00Z')-timedelta(hours=1)
        value=row.get('value')
        if value is not None:
            result.append(record(time,region,'generation',FUELS[fuel],value,'MW','eia',fetched_at))
    return result


def ingest(start,end,api_key=None):
    key=api_key or os.getenv('EIA_API_KEY') or 'DEMO_KEY'
    result=[]; now=datetime.now(timezone.utc)
    for respondent in RESPONDENTS:
        offset=0
        while True:
            params={'api_key':key,'frequency':'hourly','data[0]':'value','facets[respondent][]':respondent,
                    'start':start+'T00','end':end+'T23','sort[0][column]':'period','sort[0][direction]':'asc',
                    'offset':offset,'length':5000}
            payload=fetch('https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/?'+urlencode(params))
            result.extend(parse(payload,now))
            offset+=len(payload['response']['data'])
            if offset>=int(payload['response']['total']):
                break
            if not payload['response']['data']:
                raise ValueError('EIA pagination stalled')
    return result
