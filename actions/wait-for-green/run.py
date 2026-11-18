"""Plan-only by default. Waiting consumes a runner; never claim execution/savings."""
import os
import sys
import time
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from gridpulse.sdk import best_window

p=best_window(region=os.environ['GP_REGION'],duration_h=int(os.environ['GP_HOURS']),
              energy_kWh=float(os.environ['GP_ENERGY']),deadline=os.environ['GP_DEADLINE'])
with open(os.environ['GITHUB_OUTPUT'],'a') as file:
    file.write(f'start={p["start"]}\nexpected-kg={p["expected_kg"]}\n')
with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as file:
    file.write(f'### GridPulse planned window\n\nStart: {p["start"]} in {p["region"]}. Forecast allocation: {p["expected_kg"]:.3f} kg CO2e. This is a forecast, not measured savings.\n')
if os.environ.get('GP_WAIT')=='true':
    seconds=max(0,(datetime.fromisoformat(p['start'].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds())
    limit=float(os.environ['GP_MAX_WAIT'])*60
    if seconds>limit:
        raise SystemExit('Selected window exceeds max-wait-minutes; plan exported, runner will not idle.')
    while seconds>0:
        time.sleep(min(seconds,30));seconds=max(0,(datetime.fromisoformat(p['start'].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds())
