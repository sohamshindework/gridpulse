# GridPulse developer guide

Setup, integrations, automation and technical limits. For a visual introduction, [start with the README](../README.md).

## Run it

Python 3.11+, Node 22+, and curl (available on GitHub Ubuntu runners).

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
npm ci
npm run dev
# http://127.0.0.1:5174
```

A committed snapshot powers the dashboard immediately. Refresh and backfill a bounded source history:

```sh
# Germany generation + published day-ahead EUR prices
gridpulse update --days 14
# Also California, Texas and PJM hourly fuel generation
gridpulse update --with-us
# Optional weather collection, not yet a model feature
gridpulse update --with-us --with-weather
# Rebuild static API from the partitioned Parquet store
gridpulse publish
```

EIA falls back to its **public DEMO_KEY**, which returned real measurements for the initial release. It has tight shared rate limits. Set `EIA_API_KEY` locally or as a repository Actions secret for reliable ongoing US ingestion. Never commit a personal key. Requests do not log key-bearing URLs. The browser has no API credentials.

## What is implemented

| Layer | Working implementation |
|---|---|
| Ingest | Energy-Charts generation and prices, EIA-930 pagination, optional Open-Meteo, SMARD exported-series parser |
| Contracts | UTC timestamps, source-key duplicates, finite values, physical bounds, per-fuel gaps; incomplete fuel/hour exclusion |
| Store | DuckDB merge/upsert into Zstandard Parquet partitioned by region/year/month |
| Carbon | Documented production-weighted average lifecycle factors; renewable share |
| Forecast | Same-UTC-hour seasonal-naive baseline, 48 hours, empirical residual range and rolling-origin scorecards |
| Scheduler | Exact hourly sliding windows or cheapest independent interruptible slots; region allowlists, deadlines, upper-carbon mode, CO₂/cost/fixed-scale balanced objectives |
| Ledger | Explicit execution attestation and measured actual-vs-counterfactual allocation reconciliation; never count plans as savings |
| Serve | Static JSON API, browser dashboard, Python SDK/CLI, stdio MCP, reusable GitHub Action |

**This is an implemented baseline system, not the completed research programme.** Calibrated conformal uncertainty, LightGBM/neural challengers, import flow tracing, marginal emissions, years of backfill, a hosted public execution ledger and Hugging Face dataset publication remain milestones. No improvement over the baseline, measured public savings, or year-long backtest is claimed.

## Developer tools

### Python SDK and CLI

Install from this repository (`pip install .`), not an unrelated package name on PyPI.

```python
from gridpulse import best_window

window = best_window(
    region="DE",
    duration_h=3,
    energy_kWh=12,
    deadline="2026-11-20T08:00:00Z",  # replace with a future deadline
    objective="co2",
)
print(window["start"], window["expected_kg"])
```

```sh
gridpulse plan --region DE --hours 3 --energy 12 --deadline '2026-11-20T08:00:00Z'
```

SDK reads the public static API and refuses snapshots older than six hours. Full job definitions and cross-region scheduling are available through `gridpulse.scheduler.optimizer.optimize(regions, job)`; see the tests. Whole-hour durations only; equal power in each selected hour. Regions must be explicitly allowed. Transfers, latency and residency are the caller's constraints.

### GitHub Action

```yaml
- uses: sohamshindework/gridpulse/actions/wait-for-green@main
  id: green
  with:
    region: DE
    duration-hours: '3'
    energy-kwh: '12'
    deadline: '2026-11-20T08:00:00Z' # replace with a future UTC deadline
    wait: 'false'
```

Plan-only by default. Outputs: `start`, `expected-kg`. `wait: 'true'` waits only up to `max-wait-minutes` (30 by default) and then lets subsequent steps execute. A GitHub runner idling for hours uses resources; prefer exporting the plan and using an external job dispatcher for long delays. This action does not execute your workload or claim measured savings. Pin a commit SHA for production use.

### MCP server

```json
{"mcpServers":{"gridpulse":{"command":"/absolute/path/to/.venv/bin/gridpulse-mcp"}}}
```

Tools: `get_grid_now(region)`, `forecast(region, hours)`, `schedule_job(job)`. Implements JSON-RPC MCP stdio initialization, tool listing/calls, notifications and ping. Tool calls use the same published snapshot and scheduler; no job execution or secret required. Compatible protocol version: `2024-11-05`.

### Static API

- [`/api/v1/status.json`](https://sohamshindework.github.io/gridpulse/api/v1/status.json): all regions, source timestamps, measured history, forecasts and scorecards.
- `/api/v1/DE/now.json`
- `/api/v1/DE/forecast.json`
- `/api/v1/US-CAISO/forecast.json`
- `/api/v1/US-ERCOT/now.json`
- `/api/v1/US-PJM/scorecard.json`

Each file includes data status, generation time, attribution and accounting basis. A source region can be unavailable or its latest observation stale; consumers must inspect both timestamps. Future German prices are used only where published; missing US/EUR prices are never invented or currency-converted implicitly.

## Hourly automation

`ingest_hourly.yml` runs at minute **17 every hour (UTC)**, or on manual dispatch. It fetches a 14-day rolling history, validates before publication, merges data, computes the baseline and publishes JSON. It commits only after successful source/contract checks. Failure opens or updates a GitHub issue and retains the last published snapshot. A failed optional US source currently fails the whole refresh to avoid silently publishing partial updates. The UI pauses scheduling on stale data.

`deploy_pages.yml` builds on `main` pushes and successful hourly runs. Enable GitHub Pages with **Source: GitHub Actions**. GitHub schedules can be delayed or disabled after repository inactivity. EIA shared demo-key quotas and provider downtime can interrupt updates; no uptime guarantee is implied. Growing Parquet history belongs in an external dataset store before repository history gets large.

## Verification

```sh
pytest -q
node --test tests/scheduler.test.mjs
npm run build
```

Recorded DE/EIA source fixtures, UTC/DST handling, carbon arithmetic, partitioned upsert, partial-hour rejection, negative prices, missing data, deadlines, contiguous vs interruptible plans, forecast leakage, MCP protocol, and honest positive/negative measured ledger differences are covered. Python and browser optimizers use the same reference scenarios.

## Attribution and limits

**Energy-Charts / Fraunhofer ISE · Bundesnetzagentur | SMARD.de · U.S. EIA**. Optional weather: **Open-Meteo.com**. See [data licences](data_licences.md).

Production-average lifecycle intensity excludes imports and pumped-storage output. It is not consumption-based or marginal intensity. Forecast uncertainty is descriptive, not calibrated. Wholesale costs exclude retail fees and taxes. Savings are measured accounting differences with user-attested execution and energy, not an independently metered causal reduction. MIT software licence; source data terms remain separate.


## GridFlex

For live proxy signals, fleet replay, the Kubernetes controller and Kepler/device hooks, see the [GridFlex visual guide and model card](gridflex.md).

```sh
gridpulse flex-sim --region DE --gpus 10000 --hours 168 --output replay.json
python -m gridpulse.gridflex.operator --jobs k8s/fixtures/jobs.json --level 3
```

MCP additionally exposes `get_flex_signal` and `simulate_fleet`. Hourly US ingestion now archives EIA demand and demand-forecast records as well as generation. Reserve margins remain unknown without independent available-capacity data.
