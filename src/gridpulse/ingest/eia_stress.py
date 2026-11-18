"""EIA-930 reported demand and demand forecast, not available capacity."""
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from .common import fetch, record, utc
from .eia930 import RESPONDENTS


def parse(payload, fetched_at):
    result = []
    for row in payload['response']['data']:
        if row['respondent'] not in RESPONDENTS or row['type'] not in ('D', 'DF'):
            raise ValueError('Unexpected EIA demand respondent or type')
        if row.get('value-units') != 'megawatthours':
            raise ValueError('Unexpected EIA demand unit')
        if row.get('value') is None: continue
        value = float(row['value'])
        if value <= 0 or value > 1e6: raise ValueError('Demand outside physical contract')
        timestamp = utc(row['period']+':00:00Z')-timedelta(hours=1)
        result.append(record(timestamp, RESPONDENTS[row['respondent']],
                             'demand' if row['type'] == 'D' else 'demand_forecast', '', value, 'MW', 'eia', fetched_at))
    return result


def ingest(start, end, api_key=None):
    result = []; now = datetime.now(timezone.utc)
    key = api_key or os.getenv('EIA_API_KEY') or 'DEMO_KEY'
    for respondent in RESPONDENTS:
        offset = 0
        while True:
            params = {'api_key': key, 'frequency': 'hourly', 'data[0]': 'value',
                      'facets[respondent][]': respondent, 'facets[type][0]': 'D', 'facets[type][1]': 'DF',
                      'start': start+'T00', 'end': end+'T23', 'sort[0][column]': 'period',
                      'sort[0][direction]': 'asc', 'offset': offset, 'length': 5000}
            payload = fetch('https://api.eia.gov/v2/electricity/rto/region-data/data/?'+urlencode(params))
            page = payload['response']['data']; result.extend(parse(payload, now)); offset += len(page)
            if offset >= int(payload['response']['total']): break
            if not page: raise ValueError('EIA demand pagination stalled')
    return result
