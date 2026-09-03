# Boundary sources

The `subcounties/` directory contains the original sub-county shapefile components copied from the `cdc-kenya-footprint` repository:

`ken_admbnda_adm2_iebc_20180607`

The app-ready `data/kenya_subcounties.geojson` is the normalized GeoJSON conversion of this source. The reference layer contains 290 polygons, while the polling workbooks may contain 300 source sub-county names because of administrative-version differences. Unresolved names are displayed as coordinate fallback markers using the workbook master coordinates; they are not silently assigned an incorrect polygon.
