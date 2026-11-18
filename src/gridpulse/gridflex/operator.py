"""Namespaced, opt-in cooperative Kubernetes Job controller. Dry-run by default."""
import argparse
import json
import os
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from .policies import policy
from .kepler import energy_counter, KeplerSample
PREFIX = 'gridpulse.dev/'


def plan_job(job, level, mode='hybrid'):
    metadata = job.get('metadata', {}); labels = metadata.get('labels', {}); annotations = metadata.get('annotations', {})
    name = metadata.get('name', '')
    if labels.get(PREFIX+'flex-enabled') != 'true': return {'name': name, 'action': 'ignored', 'patch': None}
    workload = labels.get(PREFIX+'workload')
    if workload == 'inference': return {'name': name, 'action': 'protected', 'patch': None}
    if workload not in ('training', 'batch'): return {'name': name, 'action': 'unknown-workload', 'patch': None}
    if job.get('status', {}).get('completionTime') or job.get('status', {}).get('conditions', []) and any(
            c.get('type') in ('Complete', 'Failed') and c.get('status') == 'True' for c in job['status']['conditions']):
        return {'name': name, 'action': 'finished', 'patch': None}
    original_text = annotations.get(PREFIX+'original-spec')
    original = json.loads(original_text) if original_text else {
        'parallelism': job.get('spec', {}).get('parallelism', 1), 'suspend': job.get('spec', {}).get('suspend', False)}
    if original.get('suspend'):
        return {'name': name, 'action': 'owner-suspended', 'patch': None}
    if level is None:
        if not original_text: return {'name': name, 'action': 'unknown-signal', 'patch': None}
        level = 0  # Stale/unknown signal releases only previously managed restrictions.
    decision = policy(level, workload=workload, preemptible=labels.get(PREFIX+'preemptible') == 'true', mode=mode)
    base = f'v1:{level}:{mode}'
    previous = annotations.get(PREFIX+'policy-token', '')
    try: generation = max(0, int(annotations.get(PREFIX+'policy-generation', '0')))
    except ValueError: generation = 0
    if previous.split('|')[0] == base and '|' in previous:
        token = previous
    else:
        generation += 1
        token = base+'|'+str(generation)
    desired = {PREFIX+'original-spec': json.dumps(original, separators=(',', ':')),
               PREFIX+'policy-token': token, PREFIX+'policy-generation': str(generation), PREFIX+'power-fraction': str(decision['powerFraction']),
               PREFIX+'checkpoint-request': token if decision['checkpointRequired'] else '',
               PREFIX+'migration': 'advisory-only'}
    spec = {}
    coordinated = original['parallelism'] <= 1 or labels.get(PREFIX+'checkpoint-coordinator') == 'true'
    ack = coordinated and annotations.get(PREFIX+'checkpoint-ready') == token
    if decision['checkpointRequired'] and not ack:
        action = 'awaiting-checkpoint' if coordinated else 'checkpoint-coordinator-required'
        # Never reduce parallelism or suspend an unacknowledged running Job.
    else:
        spec = {'parallelism': max(1, round(original['parallelism'] * decision['parallelismFraction'])),
                'suspend': decision['action'] == 'checkpoint-and-pause'}
        action = decision['action']
    desired[PREFIX+'policy-applied'] = token if spec else annotations.get(PREFIX+'policy-applied', '')
    patch = {'metadata': {'annotations': desired}}
    if spec: patch['spec'] = spec
    unchanged = all(annotations.get(k) == v for k, v in desired.items()) and all(job.get('spec', {}).get(k, False if k == 'suspend' else 1) == v for k, v in spec.items())
    return {'name': name, 'action': action, 'patch': None if unchanged else patch,
            'checkpointAcknowledged': ack, 'decision': decision, 'realGpuCapApplied': False}


class Kubernetes:
    def __init__(self, namespace):
        self.namespace = namespace
        host = os.environ.get('KUBERNETES_SERVICE_HOST')
        if not host: raise RuntimeError('Live control requires an in-cluster service account; use --jobs for offline planning')
        self.base = f'https://{host}:{os.getenv("KUBERNETES_SERVICE_PORT", "443")}'
        self.token_path = Path('/var/run/secrets/kubernetes.io/serviceaccount/token')
        self.context = ssl.create_default_context(cafile='/var/run/secrets/kubernetes.io/serviceaccount/ca.crt')
    def request(self, path, method='GET', value=None):
        request = Request(self.base + path, method=method, data=json.dumps(value).encode() if value is not None else None,
                          headers={'Authorization': 'Bearer '+self.token_path.read_text().strip(),
                                   'Content-Type': 'application/merge-patch+json' if method == 'PATCH' else 'application/json'})
        with urlopen(request, context=self.context, timeout=15) as response: return json.load(response)
    def jobs(self):
        selector = quote(PREFIX+'flex-enabled=true', safe='')
        return self.request(f'/apis/batch/v1/namespaces/{quote(self.namespace)}/jobs?labelSelector={selector}')['items']
    def patch(self, name, patch):
        return self.request(f'/apis/batch/v1/namespaces/{quote(self.namespace)}/jobs/{quote(name)}', 'PATCH', patch)
    def configmap(self, name): return self.request(f'/api/v1/namespaces/{quote(self.namespace)}/configmaps/{quote(name)}')


def signal_level(payload, now=None, max_age_seconds=21600):
    now = now or datetime.now(timezone.utc)
    try:
        generated = datetime.fromisoformat(payload['generatedAt'].replace('Z', '+00:00'))
        if not -300 <= (now-generated).total_seconds() <= max_age_seconds: return None
        value = payload['level']
        return value if type(value) is int and value in (0, 1, 2, 3) else None
    except (KeyError, ValueError, TypeError): return None


def published_level(payload, region, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        selected = payload['gridflex']['regions'][region]
        measurements = next(r for r in payload['regions'] if r['id'] == region)
        observed = datetime.fromisoformat(measurements['now']['timestamp'].replace('Z', '+00:00'))
        if not -300 <= (now-observed).total_seconds() <= 86400: return None
        hour = now.replace(minute=0,second=0,microsecond=0)
        candidate = next(row for row in selected['forecast'] if datetime.fromisoformat(row['timestamp'].replace('Z','+00:00')) == hour)
        return signal_level({'generatedAt':payload['generatedAt'],'level':candidate['level']},now)
    except (KeyError,ValueError,TypeError,StopIteration): return None


def reconcile(client, level, apply=False, mode='hybrid'):
    result = []
    for job in client.jobs():
        planned = plan_job(job, level, mode)
        if apply and planned['patch']:
            # Optimistic concurrency: do not overwrite a concurrently edited Job.
            if job.get('metadata', {}).get('resourceVersion'):
                planned['patch']['metadata']['resourceVersion'] = job['metadata']['resourceVersion']
            client.patch(planned['name'], planned['patch'])
        result.append({**planned, 'applied': bool(apply and planned['patch'])})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='gridflex-demo'); parser.add_argument('--apply', action='store_true')
    parser.add_argument('--once', action='store_true'); parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--mode', choices=['hybrid', 'power-cap', 'checkpoint'], default='hybrid')
    parser.add_argument('--signal-url', help='Read published GridPulse status JSON instead of the demo ConfigMap')
    parser.add_argument('--region', default='DE')
    parser.add_argument('--configmap', default='gridflex-signal'); parser.add_argument('--jobs', type=Path)
    parser.add_argument('--level', type=int, choices=[0, 1, 2, 3], default=0)
    args = parser.parse_args()
    if args.jobs:
        if args.apply: parser.error('--jobs is offline dry-run only')
        jobs = json.loads(args.jobs.read_text())
        items = jobs.get('items', []) if isinstance(jobs, dict) else jobs
        print(json.dumps([plan_job(j, args.level, args.mode) for j in items if isinstance(j, dict)], indent=2)); return
    if args.interval < 1: parser.error('--interval must be at least one second')
    client = Kubernetes(args.namespace); sampler = KeplerSample()
    while True:
        try:
            try:
                if args.signal_url:
                    if not args.signal_url.startswith('https://'): raise ValueError('Published signals require HTTPS')
                    with urlopen(args.signal_url,timeout=15) as response: payload=json.load(response)
                    level=published_level(payload,args.region)
                else: level = signal_level(json.loads(client.configmap(args.configmap)['data']['signal.json']))
            except Exception: level = None
            metrics = {'status': 'not-configured', 'powerWatts': None}
            if os.getenv('KEPLER_METRICS_URL'):
                try:
                    with urlopen(os.environ['KEPLER_METRICS_URL'], timeout=10) as response: text = response.read().decode()
                    metrics = sampler.observe(energy_counter(text, args.namespace, os.getenv('KEPLER_METRIC', 'kepler_container_joules_total')), time.time())
                except Exception: metrics = {'status': 'unavailable', 'powerWatts': None}
            print(json.dumps({'level': level, 'dryRun': not args.apply, 'jobs': reconcile(client, level, args.apply, args.mode), 'kepler': metrics}), flush=True)
        except Exception as exc:
            print(json.dumps({'error': str(exc), 'retrying': not args.once}), flush=True)
            if args.once: raise
        if args.once: break
        time.sleep(args.interval)


if __name__ == '__main__': main()
