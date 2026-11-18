"""Deterministic five-minute fleet replay against measured grid history.

    Fixed inference allocation is never throttled. Synthetic training arrivals,
    GPU power curves and checkpoint costs are explicit, configurable assumptions.
"""
from dataclasses import dataclass, asdict
from math import isfinite
from ..ingest.common import utc
from .policies import policy


@dataclass
class Fleet:
    gpus: int = 1000
    inference_fraction: float = .20
    gpu_max_w: float = 700.
    gpu_idle_w: float = 100.
    inference_gpu_w: float = 500.
    host_w_per_gpu: float = 70.
    pue: float = 1.2
    checkpoint_minutes: float = 5.
    resume_minutes: float = 2.
    preemptible_fraction: float = .8
    training_utilization: float = .85
    training_tokens_per_gpu_second: float = 1000.
    inference_tokens_per_gpu_second: float = 1500.
    mode: str = 'hybrid'

    def validate(self):
        if isinstance(self.gpus, bool) or not isinstance(self.gpus, int) or not 1 <= self.gpus <= 10000:
            raise ValueError('gpus must be an integer from 1 to 10000')
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and (not isfinite(value) or value < 0):
                raise ValueError(f'Invalid fleet parameter: {key}')
        if not 0 <= self.inference_fraction < 1 or not 0 <= self.preemptible_fraction <= 1 or not 0 < self.training_utilization <= 1:
            raise ValueError('Invalid fleet fractions')
        if self.gpu_max_w < self.gpu_idle_w or self.pue < 1 or self.inference_gpu_w > self.gpu_max_w:
            raise ValueError('Invalid power / PUE profile')
        if self.mode not in ('hybrid', 'power-cap', 'checkpoint'):
            raise ValueError('Unknown policy mode')
        return self


def simulate(hours, config=None):
    cfg = (config or Fleet()).validate()
    if not hours or len(hours) > 8760:
        raise ValueError('Supply 1–8760 chronological hourly grid records')
    for i, hour in enumerate(hours):
        if type(hour.get('level')) is not int or hour['level'] not in (0, 1, 2, 3):
            raise ValueError('Cannot simulate unknown stress hours')
        if not isinstance(hour.get('carbon'), (int, float)) or not isfinite(hour['carbon']) or hour['carbon'] < 0:
            raise ValueError('Missing or invalid measured carbon intensity')
        if hour.get('price') is not None and (not isfinite(hour['price'])):
            raise ValueError('Invalid price')
        if i and (utc(hour['timestamp']) - utc(hours[i-1]['timestamp'])).total_seconds() != 3600:
            raise ValueError('Replay requires contiguous hourly data; do not interpolate gaps silently')
    inference_gpus = round(cfg.gpus * cfg.inference_fraction)
    training_gpus = cfg.gpus - inference_gpus
    if training_gpus < 1: raise ValueError('Profile needs at least one training GPU')
    def replay(controlled):
        jobs = []; trace = []; checkpoint_h = 0.; was_paused = False; previous_policy = None
        completed_work = 0.; checkpoint_total = 0.; inference_tokens = 0.
        for h, hour in enumerate(hours):
            if h % 6 == 0:
                index = len(jobs)
                jobs.append({'id': index, 'remaining': training_gpus * 6 * cfg.training_utilization,
                             'deadline': h + 12, 'preemptible': ((index * 37) % 100) / 100 < cfg.preemptible_fraction,
                             'completedAtHour': None})
            used_kwh = 0.; useful = 0.; inf_tokens = 0.
            for sub in range(12):
                now = h + sub / 12
                pending = [j for j in jobs if j['remaining'] > 1e-8]
                pending.sort(key=lambda j: (j['deadline'], j['id']))
                current = pending[0] if pending else None
                decision = policy(hour['level'] if controlled else 0, mode=cfg.mode,
                                  preemptible=current['preemptible'] if current else True)
                paused = bool(current and decision['action'] == 'checkpoint-and-pause')
                key = (decision['action'], decision['parallelismFraction'])
                if current and key != previous_policy:
                    overhead = cfg.checkpoint_minutes if decision['checkpointRequired'] else cfg.resume_minutes if was_paused else 0.
                    checkpoint_h += overhead / 60
                    checkpoint_total += overhead / 60
                previous_policy = key
                was_paused = paused
                dt = 1 / 12
                progress = 0.; busy_fraction = 0.; cap = decision['powerFraction']
                if checkpoint_h > 0:
                    overhead = min(dt, checkpoint_h); checkpoint_h -= overhead
                    busy_fraction = overhead / dt
                    cap = 1.
                    active_dt = dt - overhead
                else:
                    active_dt = dt
                if current and not paused and active_dt > 0:
                    speed = decision['throughputFraction'] * decision['parallelismFraction']
                    progress = min(current['remaining'], training_gpus * speed * active_dt)
                    running_fraction = progress / (training_gpus * speed * dt) if training_gpus and speed else 0
                    busy_fraction += running_fraction * decision['parallelismFraction']
                    current['remaining'] -= progress
                    if current['remaining'] < 1e-8: current['completedAtHour'] = now + dt
                training_w = training_gpus * (cfg.gpu_idle_w + (cfg.gpu_max_w - cfg.gpu_idle_w) * cap * busy_fraction)
                facility_w = (training_w + inference_gpus * cfg.inference_gpu_w + cfg.gpus * cfg.host_w_per_gpu) * cfg.pue
                used_kwh += facility_w / 1000 * dt; useful += progress
                tokens = inference_gpus * cfg.inference_tokens_per_gpu_second * dt * 3600
                inf_tokens += tokens; inference_tokens += tokens
            completed_work += useful
            trace.append({'timestamp': hour['timestamp'], 'level': hour['level'], 'energyKwh': used_kwh,
                          'powerMW': used_kwh / 1000, 'trainingGpuHours': useful, 'inferenceTokens': inf_tokens,
                          'carbonKg': used_kwh * hour['carbon'] / 1000,
                          'costEur': used_kwh * hour['price'] if hour['price'] is not None else None})
        energy = sum(r['energyKwh'] for r in trace)
        deadlines = [j for j in jobs if j['deadline'] <= len(hours)]
        return {'energyKwh': energy, 'carbonKg': sum(r['carbonKg'] for r in trace),
                'costEur': sum(r['costEur'] for r in trace) if all(r['costEur'] is not None for r in trace) else None,
                'trainingGpuHours': completed_work, 'inferenceTokens': inference_tokens,
                'usefulTokensPerKwh': (completed_work * 3600 * cfg.training_tokens_per_gpu_second + inference_tokens) / energy if energy else None,
                'deadlineMisses': sum(j['completedAtHour'] is None or j['completedAtHour'] > j['deadline'] + 1e-6 for j in deadlines),
                'deadlinesObserved': len(deadlines), 'pendingJobs': sum(j['remaining'] > 1e-8 for j in jobs),
                'checkpointOverheadHours': checkpoint_total, 'trace': trace}
    baseline = replay(False); flex = replay(True)
    deltas = [a['powerMW'] - b['powerMW'] for a, b in zip(baseline['trace'], flex['trace'])]
    request_deltas = [d for d, hour in zip(deltas, hours) if hour['level'] > 0]
    energy_difference = baseline['energyKwh'] - flex['energyKwh']
    lost_work = baseline['trainingGpuHours'] - flex['trainingGpuHours']
    return {'kind': 'simulation', 'version': 'flexsim-v1', 'config': asdict(cfg),
            'hours': len(hours), 'start': hours[0]['timestamp'], 'lastIntervalStart': hours[-1]['timestamp'],
            'gridInputs': [{**{key: hour[key] for key in ('timestamp', 'level', 'carbon')}, 'price': hour.get('price')} for hour in hours],
            'baseline': baseline, 'controlled': flex,
            'metrics': {'peakShedMW': max([0.] + request_deltas),
                        'meanShedDuringRequestsMW': sum(request_deltas) / len(request_deltas) if request_deltas else 0.,
                        'requestHours': len(request_deltas), 'netEnergyDifferenceKwh': energy_difference,
                        'carbonDifferenceKg': baseline['carbonKg'] - flex['carbonKg'],
                        'costDifferenceEur': baseline['costEur'] - flex['costEur'] if baseline['costEur'] is not None and flex['costEur'] is not None else None,
                        'trainingThroughputLossPercent': 100 * lost_work / baseline['trainingGpuHours'] if baseline['trainingGpuHours'] else 0.,
                        'lostGpuHoursPerMwh': lost_work / (energy_difference / 1000) if energy_difference > 0 else None,
                        'inferenceProtected': abs(baseline['inferenceTokens'] - flex['inferenceTokens']) < 1e-5},
            'assumptions': ['Synthetic six-hour training arrivals with twelve-hour deadlines; equal replay workload.',
                            'Illustrative GPU power / throughput curve, not hardware-calibrated.',
                            'Fixed inference GPUs protected; GPU idle, host overhead, PUE, checkpoint and resume costs included.',
                            'No migration or network-energy model; migration suggestions require separate capacity and residency approval.',
                            'Carbon difference uses measured average intensity, not causal marginal emissions.',
                            'Short-horizon work deferred beyond the replay is not counted as completed; energy differences are not equal-work savings.'],
            'annualExtrapolation': False}
