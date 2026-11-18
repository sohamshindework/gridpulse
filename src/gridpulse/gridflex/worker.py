"""Cooperative CPU mock for the kind smoke test, not a GPU benchmark."""
import json
import os
import time
from pathlib import Path
from .operator import Kubernetes, PREFIX
from urllib.parse import quote


def main():
    namespace = os.environ.get('POD_NAMESPACE', 'gridflex-demo')
    name = os.environ['JOB_NAME']; workload = os.getenv('WORKLOAD', 'training')
    client = Kubernetes(namespace)
    path = f'/apis/batch/v1/namespaces/{quote(namespace)}/jobs/{quote(name)}'
    root = Path(os.getenv('CHECKPOINT_DIR', '/checkpoints')); root.mkdir(parents=True, exist_ok=True)
    checkpoint = root/(name+'.json')
    progress = json.loads(checkpoint.read_text())['progress'] if checkpoint.exists() else 0.
    if checkpoint.exists() and workload != 'inference':
        client.patch(name, {'metadata': {'annotations': {PREFIX+'resumed-from-checkpoint': 'true'}}})
    print(json.dumps({'mock': True, 'workload': workload, 'resumedProgress': progress}), flush=True)
    while progress < float(os.getenv('TARGET_PROGRESS', '3600')):
        job = client.request(path); annotations = job['metadata'].get('annotations', {})
        if workload != 'inference':
            token = annotations.get(PREFIX+'checkpoint-request')
            if token:
                if annotations.get(PREFIX+'checkpoint-ready') != token:
                    temp = checkpoint.with_suffix('.tmp')
                    with temp.open('w') as file:
                        json.dump({'progress': progress, 'token': token}, file); file.flush(); os.fsync(file.fileno())
                    temp.replace(checkpoint)
                    client.patch(name, {'metadata': {'annotations': {PREFIX+'checkpoint-ready': token}}})
                    print(json.dumps({'checkpoint': str(checkpoint), 'progress': progress, 'token': token}), flush=True)
                    time.sleep(.5); continue
                if annotations.get(PREFIX+'policy-applied') != token or job.get('spec', {}).get('suspend'):
                    time.sleep(.5); continue
            fraction = max(0., min(1., float(annotations.get(PREFIX+'power-fraction', '1'))))
        else:
            fraction = 1.
        # Simulated useful progress; no claim of measured GPU energy or throughput.
        progress += fraction * .5
        time.sleep(.5)
    print(json.dumps({'mockCompleted': True, 'progress': progress}), flush=True)


if __name__ == '__main__': main()
