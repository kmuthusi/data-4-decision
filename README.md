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

## Authentication and geographic access

The app now includes a provider-agnostic authentication and authorization foundation. On first run, create the initial administrator account. Administrators can register users, assign a role, activate/deactivate accounts, and assign geography scopes from the Administration panel.

Roles restrict the maximum geography level as follows:

- `MCA`: ward only
- `MP`: sub-county/constituency and ward
- `Governor`, `Senator`, `Women Representative`: county, sub-county, and ward
- `President`: all supported data and geography levels
- `Administrator`: system and user management

Administrators assign scopes through cascading dropdowns: dataset, access depth, county, sub-county/constituency, and ward(s) where applicable. The underlying representation is `dataset|county|sub_county|constituency|ward`, with blank fields as wildcards. For example, a county-level Kitui assignment is stored as `|Kitui|||`; a sub-county assignment as `|Kitui|Kitui Central||`; and a ward assignment as `|Kitui|Kitui Central||Ward Name`. The current implementation filters all frames server-side before maps, trends, tables, and downloads are created.

For local development without a database secret, the app stores salted PBKDF2 password hashes in the ignored local file `data/auth.sqlite3`. When a PostgreSQL URL is configured, users, scopes, and audit events are stored in PostgreSQL instead. The application initializes the required schema on startup.

To migrate an existing local registry once:

```powershell
python scripts/migrate_auth_to_postgres.py --database-url "postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require"
```

For production deployment, use an organization-managed OIDC provider such as Google, Microsoft Entra ID, or Keycloak, enable MFA, and retain the same role/scope authorization policy.

### Microsoft Entra pilot deployment

The app uses native Streamlit OIDC when `[auth]` and a named provider section such as `[auth.google]` or `[auth.microsoft]` are configured. The pilot template is configured for Google OIDC. Register the exact deployed callback URL ending in `/oauth2callback`, then copy `.streamlit/secrets.example.toml` into the deployment secret manager and replace its placeholders; never commit the real secret values.

For Streamlit Community Cloud, deploy the private app from the GitHub repository, configure the OIDC and PostgreSQL values in the app's Secrets panel, and use separate Google OAuth clients for local development and the pilot URL. The administrator must register each pilot user's Google email in the Administration panel before that user can access the dashboard. PostgreSQL is the authoritative user/scope store, so redeployments do not require re-registration.

## Included assets

- `D4D_Kenya_Presidential.xlsx`
- `D4D_Kitui_Governor.xlsx`
- Kenya country, county, and sub-county GeoJSON boundaries adapted from the `cdc-kenya-footprint` reference repository.

See [STEERING.md](STEERING.md) for product decisions, data safeguards, boundary handling, refresh process, and extension guidance for future elections.
