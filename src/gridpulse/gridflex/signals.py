"""Heuristic flexibility requests, never official grid-emergency declarations."""
from collections import defaultdict
from datetime import timedelta
from statistics import mean
from ..ingest.common import utc, iso

LABELS = ['Normal', 'Conserve', 'Reduce', 'Pause flexible work']


def stress_level(*, renewable=None, price=None, demand_mw=None, demand_forecast_mw=None,
                 available_capacity_mw=None):
    """Explicit thresholds; available capacity must be independently supplied.

    Net generation is NOT available capacity and is never used as a reserve proxy.
    """
    import math
    values = [renewable, price, demand_mw, demand_forecast_mw, available_capacity_mw]
    if any(v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v)) for v in values):
        raise ValueError('Signal inputs must be finite numbers or null')
    if renewable is not None and not 0 <= renewable <= 100:
        raise ValueError('Renewable share must be between 0 and 100')
    if any(v is not None and v <= 0 for v in (demand_mw, demand_forecast_mw, available_capacity_mw)):
        raise ValueError('Demand and available capacity must be positive')
    reasons = []; levels = []; error = None; reserve = None
    if renewable is not None:
        level = 2 if renewable < 15 else 1 if renewable < 30 else 0
        levels.append(level)
        if level: reasons.append('Low renewable generation share')
    if price is not None:
        level = 3 if price >= .30 else 2 if price >= .20 else 1 if price >= .12 else 0
        levels.append(level)
        if level: reasons.append('Elevated German wholesale EUR price')
    if demand_mw is not None and demand_forecast_mw is not None:
        error = (demand_mw - demand_forecast_mw) / demand_forecast_mw
        level = 3 if error >= .10 else 2 if error >= .05 else 1 if error >= .02 else 0
        levels.append(level)
        if level: reasons.append('Demand above published demand forecast')
    if demand_mw is not None and available_capacity_mw is not None:
        reserve = (available_capacity_mw - demand_mw) / demand_mw
        level = 3 if reserve < .03 else 2 if reserve < .07 else 1 if reserve < .15 else 0
        levels.append(level)
        if level: reasons.append('Low supplied available-capacity margin')
    level = max(levels) if levels else None
    return {'level': level, 'label': LABELS[level] if level is not None else 'Unknown',
            'reasons': reasons or (['Inputs below configured thresholds'] if levels else ['No usable inputs']),
            'demandErrorFraction': error, 'reserveMarginFraction': reserve,
            'inputs': {'renewablePercent': renewable, 'priceEurKwh': price, 'demandMW': demand_mw,
                       'demandForecastMW': demand_forecast_mw, 'availableCapacityMW': available_capacity_mw},
            'kind': 'heuristic-flex-request', 'officialEmergency': False}


def build_signals(region, history, outlook, rows):
    """Join hourly source records. Future US demand error repeats a past UTC hour.

    EIA-930 does not supply dependable available generation capacity here;
    reserve margins stay null. This is a demand-deviation proxy, not reliability.
    """
    bins = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row['region'] != region or row['metric'] not in ('demand', 'demand_forecast', 'available_capacity'):
            continue
        time = iso(utc(row['timestamp_utc']).replace(minute=0, second=0, microsecond=0))
        bins[time][row['metric']].append(row['value'])
    demand = {time: {key: mean(values) for key, values in metrics.items()} for time, metrics in bins.items()}
    observed = []
    for hour in history:
        values = demand.get(hour['timestamp'], {})
        kwargs = {'renewable': hour['renewable'], 'price': hour['price']} if region == 'DE' else {
            'demand_mw': values.get('demand'), 'demand_forecast_mw': values.get('demand_forecast'),
            'available_capacity_mw': values.get('available_capacity')}
        observed.append({'timestamp': hour['timestamp'], **stress_level(**kwargs),
                         'carbon': hour['carbon'], 'price': hour['price'], 'renewable': hour['renewable'],
                         'basis': 'measured-generation / price proxy' if region == 'DE' else 'reported demand-deviation proxy'})
    future = []
    origin = outlook[0]['timestamp'] if outlook else None
    for hour in outlook:
        kwargs = {'renewable': hour['renewable'], 'price': hour['price']} if region == 'DE' else {}
        reference = None
        if region != 'DE':
            candidates = [(time, values) for time, values in demand.items()
                          if origin and time < origin and utc(time).hour == utc(hour['timestamp']).hour
                          and values.get('demand') and values.get('demand_forecast')]
            if candidates:
                reference, previous = max(candidates)
                if utc(hour['timestamp']) - utc(reference) <= timedelta(hours=72):
                    kwargs = {'demand_mw': previous['demand'], 'demand_forecast_mw': previous['demand_forecast']}
        future.append({'timestamp': hour['timestamp'], **stress_level(**kwargs),
                       'referenceTimestamp': reference, 'basis': 'renewable baseline + published prices' if region == 'DE'
                       else 'same-UTC-hour historical demand deviation; not a calibrated stress forecast'})
    return {'region': region, 'now': observed[-1] if observed else None, 'history': observed, 'forecast': future,
            'thresholdVersion': 'heuristic-v1', 'reserveMarginAvailable': any(s['reserveMarginFraction'] is not None for s in observed),
            'limits': 'No official alert or calibrated reliability forecast. Reserve margin requires independent available-capacity data.'}
