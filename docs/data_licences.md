# Data sources and attribution

The software is MIT licensed. This does not relicense upstream data.

| Source | Use and attribution | Terms reference |
|---|---|---|
| Energy-Charts / Fraunhofer ISE | Germany fuel generation, published DE-LU day-ahead prices. Attribution shown on charts and every region API response. | https://api.energy-charts.info/ and https://energy-charts.info/ |
| Bundesnetzagentur \| SMARD.de | Underlying German market data; prices explicitly include CC BY 4.0 metadata from the source API. Credit retained alongside Energy-Charts. Direct exported-series parser is available; direct live SMARD orchestration is not enabled. | https://www.smard.de/ and https://creativecommons.org/licenses/by/4.0/ |
| U.S. EIA | Hourly EIA-930 demand, demand forecasts and net generation by fuel for CISO, ERCO and PJM. Cite U.S. EIA; public DEMO_KEY or an owner's registered key required for API access. | https://www.eia.gov/opendata/ and https://www.eia.gov/about/copyrights_reuse.php |
| Open-Meteo.com | Optional forecast weather adapter, not a deployed forecast feature. CC BY 4.0 attribution and free/non-commercial API restrictions must be respected. | https://open-meteo.com/en/terms |
| IPCC AR5 WGIII Annex III | Baseline lifecycle-factor reference with explicit approximations documented in the factors file. | https://www.ipcc.ch/site/assets/uploads/2018/02/ipcc_wg3_ar5_annex-iii.pdf |

Fetched data is transformed: timestamps standardized to UTC; German 15-minute intervals averaged to hours; EIA hour-ending labels converted to hour-start; fuel types mapped and intensity estimated. Values should not be presented as official provider carbon figures. Retain source name, unit, period and methodology when reusing generated API data.

Recheck provider terms and dataset-specific licence metadata before commercial redistribution, adding datasets, or publishing an external dataset mirror. No Hugging Face account or dataset has been created by this repository.
