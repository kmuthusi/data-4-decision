# Incoming workbook refreshes

Place a newly received workbook here, then stage it with:

```powershell
python scripts/refresh_release.py --dataset presidential --workbook data/incoming/D4D_Kenya_Presidential_YYYYMMDD.xlsx
```

Review the generated release under `data/releases/<dataset>/<release-id>/`. Promote only after review:

```powershell
python scripts/refresh_release.py --dataset presidential --workbook data/incoming/D4D_Kenya_Presidential_YYYYMMDD.xlsx --promote
```

Promotion backs up the previous canonical workbook inside the release directory. Restart the Streamlit app after promotion so its cached data is reloaded.

To run validation plus dashboard smoke tests as one release gate:

```powershell
python scripts/release_gate.py --dataset presidential --workbook data/incoming/D4D_Kenya_Presidential_YYYYMMDD.xlsx
```

Use `--promote` only after reviewing the staged report. The gate backs up the previous workbook and rolls back automatically if the post-promotion app smoke test fails.

For scheduled processing of all recognizable workbooks in this folder:

```powershell
python scripts/auto_refresh.py --promote
```

The script is safe to run repeatedly: successful workbook hashes are skipped, overlapping executions are blocked, and all outcomes are logged. For a continuously running watcher, add `--watch --interval 60`.
