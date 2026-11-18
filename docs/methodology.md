# Methodology and model card

## Sources and intervals

Energy-Charts `public_power?country=de` provides fuel-level public net generation. German prices use `price?bzn=DE-LU`; upstream reports EUR/MWh, converted to EUR/kWh. German quarter-hours are UTC timestamps and averaged only when all four intervals and all expected fuels in the fetched history are present. EIA-930 hourly fuel data for CISO, ERCO and PJM reports hour-ending UTC labels; we subtract one hour to create interval-start labels. An hourly MWh total is numerically the mean MW over that hour.

UTC is retained everywhere in storage, forecasts, SDK and API. Browser times use the viewer's local timezone. This avoids ambiguous daylight-saving local timestamps. A `now` record is the latest completed measured interval, not a real-time instantaneous meter. Each region displays its timestamp; delayed source data can be stale even if the snapshot build is recent.

The source schema is `(timestamp_utc, region, metric, fuel, value, unit, source, fetched_at)`. Contracts reject duplicate source intervals, unknown mapped fuel codes, non-finite values, excessive per-fuel gaps and out-of-bound generation/prices. Fuel coverage must be complete within each accepted hour. Upstream exports and optional weather use separate adapters; source licences and freshness must be checked before adding providers.

## Carbon accounting

`sum(generation_MW[f] * factor_gCO2e_per_kWh[f]) / sum(generation_MW[f])`.

Factors are versioned in `src/gridpulse/carbon/emission_factors.json`, based on IPCC AR5 lifecycle medians where corresponding fuel classes exist. Lignite, oil, coal-derived gas, waste and unknown categories use explicitly approximate assumptions. They are not instantaneous measured stack emissions or provider official carbon numbers. Renewable biomass lifecycle values are particularly context-sensitive.

We exclude load, residual load, cross-border exchange, published percentage series and pumped-storage output/consumption and EIA battery output from the fuel sum. This is a **domestic primary-generation proxy**, not a complete consumption mix. Net negative fuel values are preserved in raw storage and clipped to zero for the weighted-average engine. Each measured API row reports `clippedNegativeMW`; this can be material for net aggregate “other” categories and makes the estimate more uncertain. No import flow tracing or marginal intensity estimate is provided. Region comparisons are method-consistent but generation definitions, factors and source reporting can differ.

## Forecast baseline and scorecard

For each target UTC hour within 48 hours, use the most recent observed value for the same UTC hour available strictly before the forecast origin. This is a seasonal-naive baseline repeated over the horizon, without weather or ML features. Historical prices are never treated as future known prices. Only actual published future day-ahead EUR prices enter the optimizer; other slots have `null` prices.

Empirical 10th/90th residual quantiles are computed from historical same-hour baseline errors observed before the origin. The chart is a descriptive historical residual range, **not a calibrated 80% future prediction interval**. Both edges include the point estimate to make the visual range interpretable, so it is not a formal quantile model. We have no conformal coverage claim.

The scorecard uses rolling forecast origins every twelve hours after the initial 48 observations and evaluates 1, 6, 24 and 48 hour targets. Predictions only read records strictly before each origin. Published MAE compares this single baseline against measured production-average intensity; there is no challenger model and no claimed gain. Fourteen days is a short initial history, not a year-long backtest. Random train/test splits are not used.

## Scheduler and counterfactual

Each job has a whole-hour duration, fixed total kWh (or a consistent kW value), earliest start, deadline, explicitly allowed regions, interruptibility and objective. Equal power is assumed in each selected hour. A non-interruptible job enumerates contiguous hourly windows and picks the lowest score exactly. An interruptible job picks the best N independent hourly slots, which is exact under these assumptions and requires no integer solver. No restart overhead, dependencies, ramp limits, storage losses, transfer energy, latency or egress charges are modelled.

Carbon mode uses mean intensity, or the empirical upper estimate when requested. Cost mode uses published EUR/kWh prices only. Balanced mode uses a fixed, transparent `0.5 * carbon/500 + 0.5 * price/0.10` score; scales are not fitted. Negative wholesale prices are valid. Missing price hours are infeasible for cost/balanced mode. A deadline includes the **end** of every selected slot. Jobs cannot cross gaps in continuous mode. Ties prefer earlier starts, then lexical region order.

Counterfactual: earliest feasible **continuous** window in the first allowed region, subject to the chosen mode's data availability. It approximates running as soon as the hourly grid permits; it is not literally the current fractional hour. Missing counterfactual stays null. A cost-focused or risk-averse plan can increase mean carbon; negative estimated savings remain negative. All schedule outputs are forecasts, not executed work.

## Ledger

Plans are saved locally in the browser and never included in public verified totals. The user can attest execution only after every selected slot ends. Reconciliation requires complete measured hourly intensities for actual and counterfactual slots. `energy_kWh/duration_h * (sum(counterfactual intensity) - sum(actual intensity)) / 1000` yields a kg CO2e allocation difference. Cost reconciliation similarly requires measured prices for every slot.

The system does not meter a job's energy or prove it ran. Execution and kWh are user-attested; the measured part is the public grid intensity. This is accounting under average intensity, not proof of marginal avoided emissions. Local history can be cleared by the browser; export JSON for retention. Public ledger remains zero until real execution telemetry is connected. Negative outcomes are retained.

## Reliability and next research gates

Adapters have bounded retries and a 30-minute request cache. Source and contract failures quarantine the affected region: its last good archived measurements remain, while other validated regions can refresh. No gap is interpolated. The tracked `data/health.json`, API `dataHealth` and dashboard warning record the failure. Complete refresh failures leave the published JSON unchanged. SDK and browser refuse stale forecast snapshots after six hours; all three consumers also refuse regions with measured observations older than 24 hours. EIA-930 can lag, so a region may be visible but ineligible for scheduling. The shared demo key is a convenience, not a production credential.

Next gates: source-specific cadence/coverage controls; years of audited backfill; a licensed open dataset release; trained quantile challengers with rolling validation and separately calibrated uncertainty; imports and marginal intensity; independently measured execution telemetry; broader ISO prices. None is implied by this release.
