# Data for Decision Dashboard

Interactive Streamlit dashboard for exploring polling results by cycle, county, sub-county, and ward where available.

The dashboard separates longitudinal `Cycle view` metrics from the derived `Latest assessment` layer. Cycle view uses `Poll_History`; Latest assessment uses `Latest_Snapshot` for zone, trend slope, p-value, and trend label. The cycle timeline is disabled while viewing the latest assessment because those indicators summarize the full historical period.

During refresh, `src/data_processing.py` rebuilds `Latest_Snapshot` from `Poll_History`. It fits an OLS regression of probability on cycle, writes the slope, slope 95% CI, p-value, and cycle count, then classifies trend and zone using the dataset-specific rules documented in the processing module. Uploaded snapshot values are therefore treated as replaceable derived outputs, not as the authoritative calculation.

Each refresh also compares the uploaded snapshot with the computed snapshot. Release folders contain `derived_indicator_audit.csv` and the release summary reports source-only/computed-only geographies and zone/trend-label changes.

## Run locally

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Validate source data

```powershell
python scripts/validate_data.py
```

## Refresh a workbook

Stage and validate a new release without changing the live dashboard data:

```powershell
python scripts/refresh_release.py --dataset presidential --workbook data/incoming/D4D_Kenya_Presidential_YYYYMMDD.xlsx
```

Review the generated `data/releases/<dataset>/<release-id>/release_summary.md` and `validation_report.json`. After approval, promote the release:

```powershell
python scripts/refresh_release.py --dataset presidential --workbook data/incoming/D4D_Kenya_Presidential_YYYYMMDD.xlsx --promote
```

Promotion backs up the previous canonical workbook in the release directory. Restart Streamlit after promotion so cached data reloads. The workflow accepts future-dated cycles and validates both `Poll_History` and `Latest_Snapshot`.

For the full release gate, which also runs application smoke tests and blocks promotion on failure, use:

```powershell
python scripts/release_gate.py --dataset presidential --workbook data/incoming/D4D_Kenya_Presidential_YYYYMMDD.xlsx
```

Add `--promote` only after the staged release report is reviewed. If post-promotion smoke tests fail, the previous canonical workbook is restored automatically.

To automate incoming-folder processing, run the one-shot watcher from Windows Task Scheduler or another scheduler:

```powershell
python scripts/auto_refresh.py --promote
```

The watcher infers `presidential` or `kitui_governor` from the filename, skips hashes already processed successfully, prevents overlapping runs with a lock file, and records outcomes in `data/refresh_log.jsonl` and `data/refresh_state.json`. Use `--watch --interval 60` for a continuously running process. Promotion is optional; omit `--promote` for staging-only automation.

The dashboard includes the current/future-dated cycle supplied by each workbook. It determines the current cycle from the maximum valid `cycle_id`; it does not compare source dates with the server clock.

## Included assets

- `D4D_Kenya_Presidential.xlsx`
- `D4D_Kitui_Governor.xlsx`
- Kenya country, county, and sub-county GeoJSON boundaries adapted from the `cdc-kenya-footprint` reference repository.

See [STEERING.md](STEERING.md) for product decisions, data safeguards, boundary handling, refresh process, and extension guidance for future elections.
