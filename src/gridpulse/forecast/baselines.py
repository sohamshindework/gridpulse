from datetime import timedelta
from statistics import mean
from ..ingest.common import utc, iso


def reference(history, target, origin, metric):
    candidates=[r for r in history if utc(r['timestamp'])<origin and utc(r['timestamp']).hour==target.hour and r.get(metric) is not None]
    return max(candidates,key=lambda r:r['timestamp'])[metric] if candidates else None


def forecast(history, now, hours=48, known_prices=None):
    """UTC-hour seasonal naive. Empirical residual bands, no calibration claim."""
    if len(history)<48:
        raise ValueError('At least 48 measured hourly rows required')
    origin=now.replace(minute=0,second=0,microsecond=0)
    known_prices=known_prices or {}
    prior=[r for r in history if utc(r['timestamp'])<origin]
    errors=[]
    for row in prior:
        target=utc(row['timestamp']); prediction=reference(prior,target,target,'carbon')
        if prediction is not None:
            errors.append(row['carbon']-prediction)
    ordered=sorted(errors)
    q10=ordered[int(.1*(len(ordered)-1))] if ordered else -100
    q90=ordered[int(.9*(len(ordered)-1))] if ordered else 100
    result=[]
    for h in range(hours):
        target=origin+timedelta(hours=h);ci=reference(prior,target,origin,'carbon');renewable=reference(prior,target,origin,'renewable')
        if ci is None:
            raise ValueError('Missing baseline reference hour')
        price=known_prices.get(iso(target))
        result.append(dict(timestamp=iso(target),carbon=round(ci,3),low=round(max(0,min(ci,ci+q10)),3),high=round(max(ci,ci+q90),3),
                           renewable=renewable,price=price,priceKind='published day-ahead' if price is not None else 'unavailable',
                           kind='forecast',model='UTC-hour seasonal naive'))
    return result


def backtest(history):
    """Rolling origins; each forecast reads only rows strictly before its origin."""
    results={h:[] for h in (1,6,24,48)}
    by_time={utc(r['timestamp']):r for r in history}
    times=sorted(by_time)
    if len(times)<96:
        return {'status':'insufficient-history','model':'seasonal-naive','annualBacktest':False}
    for origin in times[48::12]:
        for h in results:
            target=origin+timedelta(hours=h)
            if target not in by_time:
                continue
            estimate=reference(history,target,origin,'carbon')
            if estimate is not None:
                results[h].append(abs(estimate-by_time[target]['carbon']))
    return {'status':'baseline-only','model':'UTC-hour seasonal naive','annualBacktest':False,
            'observedHours':len(history),'horizons':[{'hours':h,'mae':round(mean(errors),2),'samples':len(errors)} for h,errors in results.items() if errors],
            'uncertainty':'Empirical historical residual range, not a calibrated 80% prediction interval',
            'challenger':None,'improvementClaim':False}
