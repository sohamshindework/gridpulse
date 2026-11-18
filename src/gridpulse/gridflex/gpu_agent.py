"""Explicit opt-in single exclusive GPU cap experiment, with automatic restoration.

Not run by the controller or mock workloads. Requires nvidia-smi permissions.
"""
import argparse
import math
import subprocess
import signal
import time


def bounded_cap(original, minimum, maximum, fraction):
    if not all(math.isfinite(x) for x in (original, minimum, maximum, fraction)) or not 0 < fraction <= 1:
        raise ValueError('Power fraction must be in (0, 1] and limits finite')
    if not 0 < minimum <= original <= maximum:
        raise ValueError('Inconsistent device power limits')
    return max(minimum, min(maximum, original * fraction))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gpu', required=True); parser.add_argument('--fraction', type=float, required=True)
    parser.add_argument('--seconds', type=float, default=30); parser.add_argument('--apply', action='store_true')
    parser.add_argument('--exclusive', action='store_true', help='Attest this GPU is exclusively assigned to this job')
    args = parser.parse_args()
    if not args.gpu.startswith('GPU-') or ',' in args.gpu: parser.error('Select one explicit GPU UUID')
    if not 1 <= args.seconds <= 3600: parser.error('--seconds must be 1–3600')
    if args.apply and not args.exclusive: parser.error('Applying a device-wide cap requires --exclusive')
    def command(*values):
        return subprocess.run(['nvidia-smi', '--id='+args.gpu, *values], check=True, text=True, capture_output=True).stdout.strip()
    fields = command('--query-gpu=power.limit,power.min_limit,power.max_limit', '--format=csv,noheader,nounits').split(',')
    if len(fields) != 3: raise ValueError('Expected one supported GPU')
    original, minimum, maximum = map(float, fields)
    cap = bounded_cap(original, minimum, maximum, args.fraction)
    print(f'{"APPLY" if args.apply else "DRY RUN"}: {args.gpu}: {original:g} W -> {cap:g} W for {args.seconds:g} s')
    if args.apply:
        def terminate(signum, frame): raise SystemExit(128+signum)
        signal.signal(signal.SIGTERM, terminate)
        try:
            command('--power-limit='+str(cap))
            time.sleep(args.seconds)
        finally:
            command('--power-limit='+str(original))


if __name__ == '__main__': main()
