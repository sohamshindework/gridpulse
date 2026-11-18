"""Bounded kind integration: real Job suspend/resume, PVC checkpoint, inference protection."""
import json
import subprocess
import time
from datetime import datetime, timezone
NS = 'gridflex-demo'


def kubectl(*args, value=None):
    result = subprocess.run(['kubectl', '-n', NS, *args], input=json.dumps(value) if value is not None else None,
                            text=True, capture_output=True, check=True)
    return result.stdout


def job(name='flex-training'):
    return json.loads(kubectl('get', 'job', name, '-o', 'json'))


def until(predicate, description, seconds=90):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        if predicate():
            print('PASS:', description, flush=True); return
        time.sleep(2)
    raise AssertionError(description)


def signal(level, stale=False):
    payload = json.dumps({'level':level,'generatedAt':'2000-01-01T00:00:00Z' if stale else datetime.now(timezone.utc).isoformat()})
    kubectl('patch','configmap','gridflex-signal','--type=merge','--patch-file=/dev/stdin',value={'data':{'signal.json':payload}})


def annotation(name): return job()['metadata'].get('annotations',{}).get('gridpulse.dev/'+name)

try:
    until(lambda: len(json.loads(kubectl('get','pods','-l','job-name=flex-training','-o','json'))['items'])>0,'training pod created')
    kubectl('wait','--for=condition=Ready','pod','-l','job-name=flex-training','--timeout=120s')
    signal(1)
    until(lambda: annotation('power-fraction')=='0.85','power-cap request reaches labelled training Job')
    assert not job('protected-inference')['metadata'].get('annotations',{}).get('gridpulse.dev/policy-token')
    signal(3)
    until(lambda: job().get('spec',{}).get('suspend') is True,'Job suspended after cooperative checkpoint acknowledgement')
    assert annotation('checkpoint-ready')==annotation('policy-token')
    first_token=annotation('checkpoint-ready')
    signal(0)
    until(lambda: job().get('spec',{}).get('suspend') is False and annotation('resumed-from-checkpoint')=='true','Job restored and worker reloads persistent checkpoint')
    signal(3)
    until(lambda: job().get('spec',{}).get('suspend') is True and annotation('checkpoint-ready')!=first_token,'second pause requires a NEW checkpoint acknowledgement')
    signal(3,stale=True)
    until(lambda: job().get('spec',{}).get('suspend') is False,'stale signal releases controller restrictions')
    assert not job('protected-inference')['spec'].get('suspend',False)
    print('PASS: inference remains untouched; real GPU power NOT tested',flush=True)
except Exception:
    print(kubectl('get','pods','-o','wide'))
    print(kubectl('logs','deployment/gridflex','--tail=80'))
    raise
