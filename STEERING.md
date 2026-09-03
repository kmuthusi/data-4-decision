# Data for Decision Dashboard Steering Guide

## Purpose

Build an interactive dashboard for exploring the Data for Decision polling outputs by election cycle and geography, from Kenya national results to county, sub-county, and (for the Kitui governor dataset) ward detail.

The dashboard should help a user answer:

- How did the selected measure change across cycles?
- Which counties and sub-counties are strongest, weakest, improving, declining, or contested?
- What is the result for a selected county, sub-county, or ward?
- How certain is each estimate, and how large was the sample behind it?

This is a decision-support and polling-analysis product. It must not present modeled probabilities as certified election results or imply that a sampled geography represents unsampled populations without an explicit note.

## Confirmed product decisions

- Future-dated cycles are valid planned data and should be included as they are collected. The dashboard should display the source cycle/date and should not reject a cycle merely because it is later than the application runtime date.
- Historical-cycle views should prioritize `sample size`, `supporters`, and `true regime`.
- Current-cycle views should prioritize `support probability`, `zone`, and `trend label`.
- `zone`, `trend_label`, and `true_regime` are approved for leadership-facing display.
- The data model and interface should support additional elections and datasets in future releases.
- Derived latest indicators (`zone`, `trend_slope_per_cycle`, `trend_pvalue`, and `trend_label`) should be presented in a separate Latest assessment mode and never treated as cycle-specific observations.
- On every refresh, derive the latest snapshot from `Poll_History`: fit probability on cycle using OLS, calculate a 95% CI for the slope, use a two-sided significance threshold of 0.10 for the approved trend labels, and compute zones from the latest estimate and supplied 95% estimate interval. Preserve the source snapshot only as an audit comparison, not as the calculation authority.

## Current repository review

This repository currently contains data only; there is no application scaffold.

### `data/D4D_Kenya_Presidential.xlsx`

- `County_Master`: 300 county/sub-county rows, 47 counties, registered voters, and point coordinates.
- `Poll_History`: 5,700 rows covering 300 sub-counties across 19 cycles.
- `Latest_Snapshot`: one current/latest-style row per sub-county, but the cycle/date must be checked because the source currently reaches cycle 19 dated `2026-10-02`.
- `National_Tracking_Poll`: 19 national cycle summaries with support probability and confidence intervals.

### `data/D4D_Kitui_Governor.xlsx`

- `Ward_Master`: 39 Kitui wards across 8 sub-counties.
- `Poll_History`: 191 ward-level observations across 6 cycles.
- `Latest_Snapshot`: 39 ward-level rows, with the latest available cycle varying by row (cycles 4–6 in the current file).
- `county_tracking_poll`: 6 Kitui county cycle summaries.

### Reference geospatial assets

The sibling `cdc-kenya-footprint` repository provides:

- `data/kenya_country.geojson` — one Kenya country boundary.
- `data/kenya_counties.geojson` — 47 county polygons.
- `data/kenya_subcounties.geojson` — 290 sub-county polygons.
- Original county and sub-county shapefile directories.

The dashboard should use these boundary assets through a controlled, documented copy or a shared local asset path. The country boundary must be rendered as a separate, always-visible outline layer above fills and points.

## Recommended build

Use Python, Streamlit, pandas, Plotly, and GeoJSON/Plotly map layers. This is consistent with the reference dashboard and is sufficient for the required interactions without introducing a separate frontend build system. Build the data loader around a dataset configuration registry so new elections can be added without duplicating the application.

Suggested structure:

```text
app.py
data/
  D4D_Kenya_Presidential.xlsx
  D4D_Kitui_Governor.xlsx
  kenya_country.geojson
  kenya_counties.geojson
  kenya_subcounties.geojson
scripts/
  validate_data.py
  prepare_boundaries.py
requirements.txt
README.md
STEERING.md
```

Keep source workbooks immutable. Perform cleaning, validation, normalization, and derived summaries in code or in reproducible preparation scripts.

## Dashboard experience

### Global controls

- Dataset/election: `Presidential — Kenya` or `Governor — Kitui`.
- Cycle selector: all cycles for trend views; a specific cycle for map and ranking views.
- Geography level: national, county, sub-county, or ward where applicable.
- Measure: dynamically expose the metrics available for the selected dataset and cycle type. Historical cycles should default to `sample size`, `supporters`, and `true regime`; the current cycle should default to `support probability`, `zone`, and `trend label`.
- County and sub-county selectors, cascading from the selected dataset.
- Optional toggle for confidence interval display.

Display active-filter chips and the source cycle/date prominently so exported screenshots remain interpretable.

### National overview

Show the selected national/county summary, a cycle trend line with confidence interval ribbon where available, and a Kenya map colored by the selected measure. Include cards for the applicable current-cycle metrics, total respondents/sample size, and geographies represented.

### County and sub-county drill-down

Selecting a county should update the map, trend, summary cards, and ranked sub-county table. Selecting a sub-county should show its cycle history and its latest available estimate. For the Kitui dataset, selecting a sub-county should expose ward rankings and ward trends.

### Map behavior

- Default to a clear county-level choropleth for Kenya.
- Add sub-county polygons only when the selected level and matching boundary are reliable.
- Use point markers only as a fallback for geographies without a matched polygon.
- Draw the Kenya country boundary as a separate thick dark outline with no fill, rendered last so it remains legible.
- Draw county boundaries above county fills; draw sub-county boundaries above county fills but below the country outline.
- Fit the map to Kenya for national view and to the selected county for county/sub-county view.
- Provide hover text containing geography, cycle/date, estimate, confidence interval, sample size, and any relevant status label.
- Use a color scale whose direction is explicitly labeled; do not assume that “higher” always means better.

The map must not hide missing geometry. If a geography has data but no matched polygon, show it in the table and report it in a data-quality note.

## Data model and calculations

Normalize names only for joins; preserve source labels for display. Recommended keys are:

- `county_key`
- `subcounty_key`
- `ward_key`
- `cycle_id`
- `cycle_date`

Validate numeric ranges before visualization:

- probabilities between 0 and 1;
- supporters between 0 and sample size;
- non-negative sample sizes and registered voters;
- `ci_low <= estimate <= ci_high` where the source represents a confidence interval;
- valid coordinates within the approximate Kenya range when coordinates are used.

Derived values should be labeled clearly:

- percentage = probability × 100;
- margin = probability − 0.5, shown as percentage points;
- trend = source `trend_slope_per_cycle` and `trend_label` where present, otherwise a documented estimate from the history table;
- latest = the maximum valid cycle/date available for the selected geography, not necessarily one global cycle for every row.

Do not aggregate sub-county or ward probabilities by simple averaging unless that is explicitly approved. If an aggregate is needed, use the source national/county tracking table or a documented respondent-weighted calculation.

## Boundary and join requirements

Use the shapefiles/GeoJSON from `cdc-kenya-footprint` as the reference geography. Before release:

1. Confirm CRS and convert to WGS84 / EPSG:4326 for web mapping.
2. Confirm 47 county polygons and one country polygon.
3. Build a named crosswalk for spelling and administrative-version differences.
4. Report exact, fuzzy, and unmatched joins separately.
5. Treat the current 290 sub-county polygons versus 300 presidential sub-counties as a known coverage gap. Do not silently assign a different polygon or rename an administrative unit.
6. Preserve the country outline even when the selected map is filtered to one county.

The validation report should include boundary counts, matched geography counts, unmatched source geographies, and records shown without polygons.

## Transparency and interpretation safeguards

Show a compact source note on every main view:

> Estimates are based on the selected polling cycles and source sample sizes. They are not certified election results. Confidence intervals and coverage depend on the source data.

Also show:

- source workbook and sheet;
- selected cycle/date;
- whether the displayed value is a source value or a derived calculation;
- the number of observations/respondents behind the estimate;
- warnings for future-dated, missing, duplicated, or inconsistent cycle records;
- a data-quality panel with invalid ranges, missing coordinates, unmatched boundaries, and incomplete latest snapshots.

## Acceptance criteria

- A user can select either workbook and move from national/county to sub-county; the Kitui view additionally supports ward detail.
- A user can select a cycle and see the corresponding estimate, interval, sample size, and map state.
- A user can inspect trends across cycles for national, county, sub-county, and ward entities where data exists.
- Kenya’s boundary is clear at all zoom levels and remains visible above the map contents.
- County and sub-county joins are auditable, with the 290-versus-300 coverage issue visible.
- Filters update all relevant cards, maps, charts, and tables together.
- The app handles missing data without crashing and explains why a geography is absent from a map.
- A filtered data download is available, including source and filter metadata.
- Automated validation and a local smoke test pass before deployment.

## Development sequence

1. Inventory and validate both workbooks.
2. Copy/prepare and validate the reference boundaries.
3. Implement normalized tidy data loaders and derived metrics.
4. Build national, county, sub-county, and Kitui ward views.
5. Add linked filters, map layers, country outline, tooltips, and downloads.
6. Add interpretation notes and data-quality reporting.
7. Test edge cases, including missing polygons, mixed latest cycles, empty filters, and the future-dated presidential cycle.
8. Run visual checks at desktop and narrow/mobile widths.

## Information still required

Before implementation, confirm the following:

1. What confidence-interval terminology and statistical method should be shown (for example, confidence interval versus credible interval)?
2. Is respondent-level weighting or any approved aggregation method available, or should the dashboard use only the supplied national/county tracking summaries?
3. May the reference boundary GeoJSON/shapefile assets be copied into this repository, and which source/version should be cited?
4. What are the final approved logos, fonts, colors, and publication naming conventions?
5. Which additional election/dataset is most likely to be added first, so its configuration can be used as the extension test case?

## Proposed branding, deployment, authentication, and downloads

These are recommendations for confirmation during implementation.

### Branding

- Product name: `Data for Decision` with a dataset-specific subtitle such as `Kenya Presidential Tracking` or `Kitui Governor Tracking`.
- Visual language: restrained public-sector style using deep navy, blue, white, and accessible status colors; use a high-contrast palette for zone and trend categories.
- Use approved organizational logos only after receiving the image files and permission for digital use. Until then, use text wordmarks and do not recreate official seals.
- Display a small source/version footer on every page and export: dataset name, workbook date/version, latest cycle, boundary source/version, and refresh timestamp.

### Deployment

- Primary proposal: deploy the Streamlit application to an organizational Posit Connect or equivalent managed internal application platform.
- Development: local `python -m streamlit run app.py` with environment-specific configuration.
- Production: pinned Python dependencies, a health/smoke check, application logs, and a documented rollback to the previous workbook/configuration bundle.
- Store workbooks and boundary assets as versioned application data or in an approved internal object/file store; do not rely on a developer workstation path.
- Maintain separate development, review, and production deployment targets where the organization supports them.

### Authentication and access

- Require organizational single sign-on through the deployment platform for production.
- Default role: authenticated read-only viewer.
- Optional roles: data steward, who can upload/approve a refresh; and administrator, who can manage deployment settings.
- Do not embed workbook data, credentials, or tokens in URLs or client-side code.
- Record refresh and publication events, but avoid collecting unnecessary personally identifiable information; the current dashboard should remain aggregate and geography-level.

### Downloads

- First release: filtered CSV download and filtered Excel download, including active filters, dataset, cycle/date, source sheet, and refresh metadata.
- First release: print-ready HTML briefing view for national, county, and sub-county selections.
- Later release: PDF export after the HTML layout is approved and tested on the production platform.
- Every download should include a data note stating that estimates are polling outputs, not certified election results, plus the applicable source and coverage caveats.

## Proposed cycle refresh process

The refresh process should be repeatable, reviewable, and safe for future cycles.

1. Place the new workbook in a versioned intake location using a predictable filename, for example `incoming/D4D_Kenya_Presidential_YYYYMMDD.xlsx`.
2. Record a manifest containing dataset name, workbook filename, received date, source owner, expected cycle/date, and boundary version.
3. Run automated validation before publication:
   - required sheets and columns;
   - duplicate geography/cycle keys;
   - probability, count, interval, and date ranges;
   - cycle continuity and newly added geographies;
   - current-cycle metric completeness;
   - historical metric completeness;
   - boundary join coverage;
   - comparison with the previous approved release.
4. Write a machine-readable validation report and a human-readable release summary. Fail the refresh when required columns are missing, keys are duplicated unexpectedly, or values fall outside approved ranges.
5. Load the workbook into a staging/review deployment. A data steward reviews changed counts, changed labels, new cycle values, and warnings.
6. Approve and publish the workbook/configuration as an immutable release. Keep the previous release available for rollback and comparison.
7. Update the visible source footer and refresh timestamp automatically from the release manifest.
8. Re-run the application smoke test and a small set of visual checks after publication.

The repository implementation of this workflow is `scripts/refresh_release.py`. It stages immutable release folders containing the workbook, `manifest.json`, `validation_report.json`, and `release_summary.md`. Promotion is explicit and backs up the previous canonical workbook before replacing it.

The application should calculate the current cycle per dataset from the maximum valid `cycle_id`/`cycle_date` in the approved release. It should not compare cycle dates with the server clock or suppress future-dated observations. If a release contains a cycle/date conflict, show a validation warning and retain the source values for steward review.

For future elections, add a dataset configuration rather than branching the UI. Each configuration should declare workbook path, sheet names, geography hierarchy, metric names, current-cycle metrics, historical metrics, label dictionaries, and applicable boundary level.
