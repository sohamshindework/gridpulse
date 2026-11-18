import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

ATTRIBUTIONS = {
    'energy-charts': 'Energy-Charts / Fraunhofer ISE',
    'smard': 'Bundesnetzagentur | SMARD.de',
    'eia': 'U.S. EIA',
    'open-meteo': 'Open-Meteo.com (CC BY 4.0)',
}


def utc(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp must include a UTC offset')
    return result.astimezone(timezone.utc)


def iso(value):
    return utc(value).isoformat().replace('+00:00', 'Z')


def fetch(url, cache_dir='.cache', max_age_seconds=1800):
    """Bounded curl retries; cache only successfully decoded responses.

    URLs may contain a secret key: never log them, use curl config over stdin.
    """
    import hashlib
    import time
    path = Path(cache_dir) / (hashlib.sha256(url.encode()).hexdigest()+'.json')
    if path.exists() and time.time()-path.stat().st_mtime < max_age_seconds:
        return json.loads(path.read_text())
    escaped = url.replace('\\', '\\\\').replace('"', '\\"')
    response = subprocess.run(['curl', '--config', '-', '--fail', '--silent', '--show-error', '--location',
                               '--retry', '2', '--retry-delay', '3', '--max-time', '90'],
                              input=f'url = "{escaped}"\n', capture_output=True, text=True)
    if response.returncode:
        raise RuntimeError('Source request failed; cached published data is retained')
    payload = json.loads(response.stdout)
    if isinstance(payload, dict) and ('error' in payload or 'errors' in payload):
        raise ValueError('Source returned an API error')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return payload


def record(time, region, metric, fuel, value, unit, source, fetched_at):
    return dict(timestamp_utc=iso(time), region=region, metric=metric, fuel=fuel,
                value=float(value), unit=unit, source=source, fetched_at=iso(fetched_at))
