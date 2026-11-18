from ..ingest.common import utc


def reconcile(plan, measured, executed=False):
    """Only report measured allocation differences after explicit execution attestation."""
    if not executed:
        return {'status':'awaiting-execution','verifiedSavingsKg':None}
    baseline=plan.get('baseline')
    if not baseline:
        return {'status':'missing-counterfactual','verifiedSavingsKg':None}
    def values(region,slots):
        lookup={r['timestamp']:r for r in measured.get(region,[])}
        if not all(t in lookup for t in slots):
            return None
        return [lookup[t] for t in slots]
    actual=values(plan['region'],plan['slots']);counter=values(baseline['region'],baseline['slots'])
    if actual is None or counter is None:
        return {'status':'awaiting-measurements','verifiedSavingsKg':None}
    power=plan['energy_kWh']/len(plan['slots'])
    emissions=sum(r['carbon'] for r in actual)*power/1000
    baseline_emissions=sum(r['carbon'] for r in counter)*power/1000
    costs_available=all(r.get('price') is not None for r in actual+counter)
    return {'status':'reconciled','actualKg':emissions,'baselineKg':baseline_emissions,
            'verifiedSavingsKg':baseline_emissions-emissions,
            'costDifferenceEur':power*(sum(r['price'] for r in counter)-sum(r['price'] for r in actual)) if costs_available else None,
            'basis':'Measured average-intensity allocation difference; execution and energy are user-attested; not causal marginal emissions'}
