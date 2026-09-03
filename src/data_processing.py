"""Derived indicator processing for Data for Decision releases."""

from __future__ import annotations

from pathlib import Path
from shutil import copy2

import numpy as np
import pandas as pd
from scipy import stats


def regression_summary(frame: pd.DataFrame, value_column: str, alpha: float = 0.05) -> dict:
    """Fit value ~ cycle_id and return slope, 95% CI, p-value, and n."""
    data = frame[["cycle_id", value_column]].copy()
    data["cycle_id"] = pd.to_numeric(data["cycle_id"], errors="coerce")
    data[value_column] = pd.to_numeric(data[value_column], errors="coerce")
    data = data.dropna().drop_duplicates(subset=["cycle_id"])
    if len(data) < 3 or data["cycle_id"].nunique() < 3:
        return {"trend_slope_per_cycle": np.nan, "trend_slope_ci_low": np.nan, "trend_slope_ci_high": np.nan, "trend_pvalue": np.nan, "trend_intercept": np.nan, "trend_n_cycles": len(data)}
    result = stats.linregress(data["cycle_id"].to_numpy(dtype=float), data[value_column].to_numpy(dtype=float))
    degrees_freedom = len(data) - 2
    critical = stats.t.ppf(1 - alpha / 2, degrees_freedom)
    return {
        "trend_slope_per_cycle": float(result.slope),
        "trend_slope_ci_low": float(result.slope - critical * result.stderr),
        "trend_slope_ci_high": float(result.slope + critical * result.stderr),
        "trend_pvalue": float(result.pvalue),
        "trend_intercept": float(result.intercept),
        "trend_n_cycles": int(len(data)),
    }


def derive_trend_label(row: pd.Series, dataset: str, significance_level: float = 0.10) -> str:
    slope = row.get("trend_slope_per_cycle")
    pvalue = row.get("trend_pvalue")
    if pd.isna(slope) or pd.isna(pvalue) or pvalue >= significance_level:
        return "Area to watch" if dataset == "presidential" else "Stable"
    if dataset == "presidential":
        return "Area of maintenance" if slope > 0 else "Area of great concern"
    return "Improving" if slope > 0 else "Declining"


def derive_zone(row: pd.Series, dataset: str) -> str:
    probability = row.get("probability")
    ci_low = row.get("ci_low")
    ci_high = row.get("ci_high")
    if pd.isna(probability) or pd.isna(ci_low) or pd.isna(ci_high):
        return "Unknown"
    if dataset == "presidential":
        if ci_high < 0.50:
            return "Cold spot"
        if ci_low > 0.50:
            return "Hot spot"
        return "Contested zone"
    if ci_high < 0.45:
        return "Safe Loss"
    if ci_high < 0.50:
        return "Lean Loss"
    if ci_low > 0.55:
        return "Safe Win"
    if ci_low > 0.50:
        return "Lean Win"
    return "Toss-Up"


def build_latest_snapshot(history: pd.DataFrame, master: pd.DataFrame, dataset: str, hierarchy: list[str], probability: str) -> pd.DataFrame:
    """Create a latest row per geography and recompute all derived indicators."""
    history = history.copy()
    history["cycle_id"] = pd.to_numeric(history["cycle_id"], errors="coerce")
    history["cycle_date"] = pd.to_datetime(history["cycle_date"], errors="coerce")
    for column in [probability, "sample_size", "supporters", "ci_low", "ci_high"]:
        if column in history:
            history[column] = pd.to_numeric(history[column], errors="coerce")
    history = history.sort_values([*hierarchy, "cycle_id", "cycle_date"])
    latest = history.groupby(hierarchy, as_index=False, dropna=False).tail(1).copy()
    trend_rows = []
    for keys, group in history.groupby(hierarchy, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        result = {column: value for column, value in zip(hierarchy, keys)}
        result.update(regression_summary(group, probability))
        trend_rows.append(result)
    trends = pd.DataFrame(trend_rows)
    latest = latest.drop(columns=[column for column in ["zone", "trend_slope_per_cycle", "trend_slope_ci_low", "trend_slope_ci_high", "trend_pvalue", "trend_n_cycles", "trend_label"] if column in latest], errors="ignore")
    latest = latest.merge(trends, on=hierarchy, how="left")
    master_columns = [column for column in hierarchy + ["lat", "lon", "registered_voters"] if column in master.columns]
    if master_columns:
        latest = latest.drop(columns=[column for column in ["lat", "lon", "registered_voters"] if column in latest], errors="ignore")
        latest = latest.merge(master[master_columns].drop_duplicates(subset=hierarchy), on=hierarchy, how="left")
    latest["trend_label"] = latest.apply(lambda row: derive_trend_label(row, dataset), axis=1)
    latest["probability"] = latest[probability]
    latest["zone"] = latest.apply(lambda row: derive_zone(row, dataset), axis=1)
    latest = latest.drop(columns=["probability"])
    return latest.sort_values(hierarchy).reset_index(drop=True)


def compare_snapshots(source: pd.DataFrame, computed: pd.DataFrame, hierarchy: list[str], probability: str, numeric_tolerance: float = 1e-6) -> tuple[dict, pd.DataFrame]:
    """Compare uploaded and computed snapshots without treating expected refresh changes as failures."""
    keys = hierarchy
    fields = [probability, "ci_low", "ci_high", "zone", "trend_label", "trend_slope_per_cycle", "trend_slope_ci_low", "trend_slope_ci_high", "trend_pvalue", "trend_n_cycles"]
    left = source.copy().drop_duplicates(subset=keys)
    right = computed.copy().drop_duplicates(subset=keys)
    merged = left[keys + [field for field in fields if field in left]].merge(right[keys + [field for field in fields if field in right]], on=keys, how="outer", suffixes=("_source", "_computed"), indicator=True)
    detail = merged[keys + ["_merge"]].copy()
    detail["status"] = detail["_merge"].map({"both": "matched", "left_only": "source_only", "right_only": "computed_only"})
    for field in fields:
        source_field = f"{field}_source"
        computed_field = f"{field}_computed"
        if source_field not in merged or computed_field not in merged:
            continue
        if field in {"zone", "trend_label"}:
            changed = merged[source_field].fillna("<missing>").astype(str) != merged[computed_field].fillna("<missing>").astype(str)
        else:
            left_value = pd.to_numeric(merged[source_field], errors="coerce")
            right_value = pd.to_numeric(merged[computed_field], errors="coerce")
            changed = (left_value - right_value).abs().gt(numeric_tolerance) | left_value.isna().ne(right_value.isna())
        detail[f"{field}_changed"] = changed
        detail[f"{field}_source"] = merged[source_field]
        detail[f"{field}_computed"] = merged[computed_field]
    substantive_fields = [f"{field}_changed" for field in ["zone", "trend_label"] if f"{field}_changed" in detail]
    detail["substantive_change"] = detail[substantive_fields].any(axis=1) if substantive_fields else False
    summary = {
        "source_rows": len(source),
        "computed_rows": len(computed),
        "matched_geographies": int((detail["status"] == "matched").sum()),
        "source_only_geographies": int((detail["status"] == "source_only").sum()),
        "computed_only_geographies": int((detail["status"] == "computed_only").sum()),
        "substantive_changes": int(detail["substantive_change"].sum()),
        "changed_fields": {field: int(detail[f"{field}_changed"].sum()) for field in fields if f"{field}_changed" in detail},
        "numeric_tolerance": numeric_tolerance,
    }
    return summary, detail.drop(columns=["_merge"])


def process_workbook(source: Path, destination: Path, cfg: dict, dataset: str) -> dict:
    """Copy workbook and replace Latest_Snapshot with a computed snapshot."""
    copy2(source, destination)
    history = pd.read_excel(source, sheet_name=cfg["history_sheet"])
    master = pd.read_excel(source, sheet_name=cfg["master_sheet"])
    snapshot = build_latest_snapshot(history, master, dataset, cfg["hierarchy"], cfg["probability"])
    with pd.ExcelWriter(destination, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        snapshot.to_excel(writer, index=False, sheet_name=cfg["snapshot_sheet"])
    return {"snapshot_rows": len(snapshot), "snapshot_cycles": sorted(int(value) for value in snapshot["cycle_id"].dropna().unique())}
