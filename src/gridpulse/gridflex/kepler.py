"""Read a configured Kepler Prometheus energy counter; never invent GPU watts."""
import math
import re


def energy_counter(text, namespace, metric='kepler_container_joules_total'):
    total = 0.; found = False
    pattern = re.compile(r'^' + re.escape(metric) + r'\{([^}]*)\}\s+([^\s]+)(?:\s+\d+)?$')
    for line in text.splitlines():
        match = pattern.match(line)
        if not match: continue
        labels = dict(re.findall(r'(\w+)="([^"\\]*)"', match[1]))
        if labels.get('namespace', labels.get('container_namespace')) != namespace: continue
        value = float(match[2])
        if not math.isfinite(value) or value < 0: raise ValueError('Invalid Kepler energy counter')
        total += value; found = True
    return total if found else None


class KeplerSample:
    def __init__(self): self.previous = None
    def observe(self, value, timestamp):
        if value is None:
            self.previous = None
            return {'status': 'metric-unavailable', 'powerWatts': None}
        watts = None; status = 'first-sample'
        if self.previous:
            before, then = self.previous
            if timestamp > then and value >= before:
                watts = (value - before) / (timestamp - then); status = 'observed'
            else: status = 'counter-reset-or-invalid-interval'
        self.previous = value, timestamp
        return {'status': status, 'energyJoules': value, 'powerWatts': watts,
                'basis': 'Selected Kepler counter in the configured namespace; counter may cover CPU only'}
