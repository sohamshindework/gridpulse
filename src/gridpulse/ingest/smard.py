"""SMARD recorded/exported series adapter; direct API orchestration is optional."""
from datetime import datetime, timezone
from .common import record


def parse_series(payload, fuel, region='DE', source='smard'):
    now=datetime.now(timezone.utc)
    return [record(timestamp/1000,region,'generation',fuel,value,'MW',source,now)
            for timestamp,value in payload['series'] if value is not None]
