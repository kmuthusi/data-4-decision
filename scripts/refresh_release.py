"""Stage, validate, and optionally promote a Data for Decision workbook.

Default behavior is safe and non-destructive: the supplied workbook is copied to
an immutable release directory and validated, but the dashboard's canonical
workbook is not changed. Use --promote only after reviewing the report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_processing import compare_snapshots, process_workbook

DATA_DIR = ROOT / "data"
RELEASES_DIR = DATA_DIR / "releases"
INCOMING_DIR = DATA_DIR / "incoming"

DATASETS = {
    "presidential": {
        "canonical": "D4D_Kenya_Presidential.xlsx",
        "history_sheet": "Poll_History",
        "snapshot_sheet": "Latest_Snapshot",
        "master_sheet": "County_Master",
        "summary_sheet": "National_Tracking_Poll",
        "hierarchy": ["county", "sub_county"],
        "probability": "support_probability",
    },
    "kitui_governor": {
        "canonical": "D4D_Kitui_Governor.xlsx",
        "history_sheet": "Poll_History",
        "snapshot_sheet": "Latest_Snapshot",
        "master_sheet": "Ward_Master",
        "summary_sheet": "county_tracking_poll",
        "hierarchy": ["county", "sub_county", "ward"],
        "probability": "win_probability",
    },
}


def normalize(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_frame(workbook: Path, sheet: str) -> pd.DataFrame:
    frame = pd.read_excel(workbook, sheet_name=sheet)
    frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def validate_frame(frame: pd.DataFrame, cfg: dict, frame_name: str) -> tuple[list[str], list[str], dict]:
    errors: list[str] = []
    warnings: list[str] = []
    hierarchy = cfg["hierarchy"]
    probability = cfg["probability"]
    required = set(hierarchy + ["cycle_id", "cycle_date", "sample_size", "supporters", probability, "ci_low", "ci_high"])
    missing = sorted(required.difference(frame.columns))
    if missing:
        errors.append(f"{frame_name}: missing required columns: {', '.join(missing)}")
        return errors, warnings, {"rows": len(frame), "missing_columns": missing}

    frame = frame.copy()
    frame["cycle_id"] = pd.to_numeric(frame["cycle_id"], errors="coerce")
    frame["cycle_date"] = pd.to_datetime(frame["cycle_date"], errors="coerce")
    for column in ["sample_size", "supporters", probability, "ci_low", "ci_high"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame["cycle_id"].isna().any() or frame["cycle_date"].isna().any():
        errors.append(f"{frame_name}: missing or invalid cycle IDs/dates")
    if ((frame[probability] < 0) | (frame[probability] > 1)).any():
        errors.append(f"{frame_name}: {probability} values must be between 0 and 1")
    if ((frame["sample_size"] < 0) | (frame["supporters"] < 0)).any():
        errors.append(f"{frame_name}: sample size and supporters must be non-negative")
    if (frame["supporters"] > frame["sample_size"]).any():
        errors.append(f"{frame_name}: supporters exceed sample size")
    interval_mask = frame[["ci_low", "ci_high"]].notna().all(axis=1)
    if ((frame.loc[interval_mask, "ci_low"] < 0) | (frame.loc[interval_mask, "ci_high"] > 1)).any():
        errors.append(f"{frame_name}: confidence interval values must be between 0 and 1")
    if (frame.loc[interval_mask, "ci_low"] > frame.loc[interval_mask, "ci_high"]).any():
        errors.append(f"{frame_name}: ci_low exceeds ci_high")
    duplicate_count = int(frame.duplicated(subset=hierarchy + ["cycle_id"]).sum())
    if duplicate_count:
        errors.append(f"{frame_name}: {duplicate_count} duplicate geography/cycle keys")
    status_columns = [column for column in ["zone", "trend_label", "true_regime"] if column in frame]
    if frame_name == "Latest_Snapshot":
        missing_status = [column for column in ["zone", "trend_label", "trend_slope_per_cycle", "trend_slope_ci_low", "trend_slope_ci_high", "trend_pvalue", "trend_n_cycles"] if column not in frame]
        if missing_status:
            errors.append(f"{frame_name}: missing derived indicator columns: {', '.join(missing_status)}")
        if "trend_pvalue" in frame:
            frame["trend_pvalue"] = pd.to_numeric(frame["trend_pvalue"], errors="coerce")
            if ((frame["trend_pvalue"] < 0) | (frame["trend_pvalue"] > 1)).any():
                errors.append(f"{frame_name}: trend_pvalue values must be between 0 and 1")
    report = {
        "rows": len(frame),
        "cycles": sorted(int(value) for value in frame["cycle_id"].dropna().unique()),
        "min_date": frame["cycle_date"].min().date().isoformat() if frame["cycle_date"].notna().any() else None,
        "max_date": frame["cycle_date"].max().date().isoformat() if frame["cycle_date"].notna().any() else None,
        "duplicate_keys": duplicate_count,
        "status_columns": status_columns,
        "geographies": {column: int(frame[column].nunique()) for column in hierarchy if column in frame},
    }
    if frame_name == "Latest_Snapshot" and frame["cycle_id"].nunique() > 1:
        warnings.append(f"{frame_name}: latest snapshot contains multiple cycle IDs; this is allowed but should be reviewed.")
    return errors, warnings, report


def boundary_coverage(history: pd.DataFrame, cfg: dict) -> dict:
    county_geo = json.loads((DATA_DIR / "kenya_counties.geojson").read_text(encoding="utf-8"))
    subcounty_geo = json.loads((DATA_DIR / "kenya_subcounties.geojson").read_text(encoding="utf-8"))
    counties = {normalize(feature["properties"].get("county")) for feature in county_geo.get("features", [])}
    subcounties = {feature["properties"].get("subcounty_geo_key") for feature in subcounty_geo.get("features", [])}
    source_counties = {normalize(value) for value in history["county"].dropna().unique()}
    result = {
        "source_counties": len(source_counties),
        "matched_counties": len(source_counties & counties),
        "unmatched_counties": sorted(source_counties - counties),
    }
    if "sub_county" in cfg["hierarchy"]:
        source_subcounties = {f"{normalize(row.county)}__{normalize(row.sub_county)}" for row in history[["county", "sub_county"]].drop_duplicates().itertuples()}
        result.update({
            "source_subcounties": len(source_subcounties),
            "matched_subcounties": len(source_subcounties & subcounties),
            "unmatched_subcounties": sorted(source_subcounties - subcounties),
        })
    return result


def validate_workbook(workbook: Path, cfg: dict) -> tuple[dict, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    report: dict = {}
    try:
        history = read_frame(workbook, cfg["history_sheet"])
        snapshot = read_frame(workbook, cfg["snapshot_sheet"])
        master = read_frame(workbook, cfg["master_sheet"])
        summary = read_frame(workbook, cfg["summary_sheet"])
    except Exception as exc:  # pandas emits several exception types for malformed workbooks/sheets
        return {"workbook": str(workbook)}, [f"Unable to read workbook sheets: {exc}"], []

    for frame, name in [(history, "Poll_History"), (snapshot, "Latest_Snapshot")]:
        frame_errors, frame_warnings, frame_report = validate_frame(frame, cfg, name)
        errors.extend(frame_errors)
        warnings.extend(frame_warnings)
        report[name] = frame_report
    if not history.empty:
        report["boundary_coverage"] = boundary_coverage(history, cfg)
        if report["boundary_coverage"].get("unmatched_subcounties"):
            warnings.append(f"Boundary coverage gap: {len(report['boundary_coverage']['unmatched_subcounties'])} source sub-counties do not match the reference polygons.")
    report["master_rows"] = len(master)
    report["summary_rows"] = len(summary)
    report["source_sha256"] = sha256(workbook)
    return report, errors, warnings


def write_release_summary(path: Path, dataset: str, source: Path, report: dict, errors: list[str], warnings: list[str], promoted: bool) -> None:
    lines = [f"# Refresh release — {dataset}", "", f"- Source workbook: `{source.name}`", f"- Source SHA-256: `{report.get('source_sha256', '')}`", f"- Promoted: `{promoted}`", "", "## Result", "", "PASS" if not errors else "FAILED", ""]
    audit = report.get("derived_indicator_audit")
    if audit:
        lines += ["## Derived indicator audit", "", f"- Source snapshot rows: `{audit.get('source_rows')}`", f"- Computed snapshot rows: `{audit.get('computed_rows')}`", f"- Matched geographies: `{audit.get('matched_geographies')}`", f"- Source-only geographies: `{audit.get('source_only_geographies')}`", f"- Computed-only geographies: `{audit.get('computed_only_geographies')}`", f"- Zone/trend-label changes: `{audit.get('substantive_changes')}`", "- Full row-level comparison: `derived_indicator_audit.csv`", ""]
    if errors:
        lines += ["## Errors", ""] + [f"- {error}" for error in errors] + [""]
    if warnings:
        lines += ["## Warnings", ""] + [f"- {warning}" for warning in warnings] + [""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATASETS), required=True)
    parser.add_argument("--workbook", type=Path, required=True, help="Incoming workbook to validate")
    parser.add_argument("--promote", action="store_true", help="Back up and replace the dashboard's canonical workbook after validation passes")
    args = parser.parse_args()
    cfg = DATASETS[args.dataset]
    source = args.workbook.resolve()
    if not source.exists():
        parser.error(f"Workbook does not exist: {source}")
    if source.suffix.lower() != ".xlsx":
        parser.error("The refresh workflow currently accepts .xlsx workbooks only")

    release_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    release_dir = RELEASES_DIR / args.dataset / release_id
    release_dir.mkdir(parents=True, exist_ok=False)
    staged = release_dir / cfg["canonical"]
    processing_details = {}
    audit_summary = {}
    audit_detail = None
    try:
        source_snapshot = read_frame(source, cfg["snapshot_sheet"])
        processing_details = process_workbook(source, staged, cfg, args.dataset)
        report, errors, warnings = validate_workbook(staged, cfg)
        report["processing"] = processing_details
        computed_snapshot = read_frame(staged, cfg["snapshot_sheet"])
        audit_summary, audit_detail = compare_snapshots(source_snapshot, computed_snapshot, cfg["hierarchy"], cfg["probability"])
        report["derived_indicator_audit"] = audit_summary
        if audit_summary["substantive_changes"]:
            warnings.append(f"Derived indicator audit: {audit_summary['substantive_changes']} zone/trend-label changes require review.")
    except Exception as exc:
        report, errors, warnings = {"processing": {}}, [f"Data processing failed: {exc}"], []
    if audit_detail is not None:
        audit_detail.to_csv(release_dir / "derived_indicator_audit.csv", index=False)
    (release_dir / "validation_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    promoted = False
    canonical = DATA_DIR / cfg["canonical"]
    if args.promote and not errors:
        backup = release_dir / f"previous_{cfg['canonical']}"
        if canonical.exists():
            shutil.copy2(canonical, backup)
        shutil.copy2(staged, canonical)
        promoted = True
    write_release_summary(release_dir / "release_summary.md", args.dataset, source, report, errors, warnings, promoted)
    manifest = {
        "release_id": release_id,
        "dataset": args.dataset,
        "source_filename": source.name,
        "canonical_filename": cfg["canonical"],
        "created_at_utc": release_id,
        "promoted": promoted,
        "validation_passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "report": report,
    }
    (release_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(f"Release: {release_dir}")
    print(f"Validation: {'PASSED' if not errors else 'FAILED'}")
    print(f"Warnings: {len(warnings)}")
    print(f"Promoted: {promoted}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
