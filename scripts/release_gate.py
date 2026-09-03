"""Run the refresh validation gate and optionally promote a release.

The gate is staging-only unless --promote is supplied. Promotion is backed up
and followed by application smoke tests; the previous workbook is restored if
the post-promotion smoke test fails.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from refresh_release import DATASETS, DATA_DIR, RELEASES_DIR, read_frame, sha256, validate_workbook, write_release_summary
from src.data_processing import compare_snapshots, process_workbook


def smoke_test(dataset_name: str) -> tuple[bool, str]:
    try:
        from streamlit.testing.v1 import AppTest

        def check(app_test, label):
            if app_test.exception:
                return f"{label}: " + "; ".join(str(error.value) for error in app_test.exception)
            return ""

        test = AppTest.from_file(str(ROOT / "app.py"))
        test.run(timeout=30)
        error = check(test, "default view")
        if error:
            return False, error
        if dataset_name == "kitui_governor":
            test.selectbox[1].select_index(1).run(timeout=30)
            error = check(test, "dataset selection")
            if error:
                return False, error

        # Cycle slider path.
        test.slider[0].set_value(test.slider[0].min).run(timeout=30)
        error = check(test, "cycle view")
        if error:
            return False, error

        # Latest assessment path.
        test.selectbox[2].select_index(1).run(timeout=30)
        error = check(test, "latest assessment")
        if error:
            return False, error
        if not test.slider[0].disabled:
            return False, "latest assessment: cycle slider is not disabled"
        return True, "selected dataset default, cycle, and latest-assessment views passed"
    except Exception as exc:
        return False, f"smoke test unavailable or failed: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=sorted(DATASETS), required=True)
    parser.add_argument("--workbook", type=Path, required=True)
    parser.add_argument("--promote", action="store_true", help="Promote only after validation and pre-promotion smoke tests pass")
    args = parser.parse_args()
    cfg = DATASETS[args.dataset]
    source = args.workbook.resolve()
    if not source.exists() or source.suffix.lower() != ".xlsx":
        parser.error(f"Expected an existing .xlsx workbook: {source}")

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
        if audit_summary["source_only_geographies"] or audit_summary["computed_only_geographies"]:
            warnings.append("Derived indicator audit: source and computed snapshot geography counts differ.")
    except Exception as exc:
        report, errors, warnings = {"processing": processing_details}, [f"Data processing failed: {exc}"], []
    smoke_passed, smoke_message = (False, "not run because validation failed") if errors else smoke_test(args.dataset)
    if not smoke_passed:
        errors.append(f"Application smoke test failed: {smoke_message}")

    canonical = DATA_DIR / cfg["canonical"]
    backup = release_dir / f"previous_{cfg['canonical']}"
    promoted = False
    if args.promote and not errors:
        if canonical.exists():
            shutil.copy2(canonical, backup)
        shutil.copy2(staged, canonical)
        promoted = True
        post_passed, post_message = smoke_test(args.dataset)
        if not post_passed:
            if backup.exists():
                shutil.copy2(backup, canonical)
            errors.append(f"Post-promotion smoke test failed; previous workbook restored: {post_message}")
            promoted = False

    report["smoke_test"] = {"passed": smoke_passed, "message": smoke_message}
    report["post_promotion_smoke_test"] = {"passed": promoted and not errors, "message": "passed" if promoted and not errors else "not run or failed"}
    (release_dir / "validation_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    if audit_detail is not None:
        audit_detail.to_csv(release_dir / "derived_indicator_audit.csv", index=False)
    write_release_summary(release_dir / "release_summary.md", args.dataset, source, report, errors, warnings, promoted)
    manifest = {
        "release_id": release_id,
        "dataset": args.dataset,
        "source_filename": source.name,
        "canonical_filename": cfg["canonical"],
        "created_at_utc": release_id,
        "source_sha256": sha256(source),
        "promoted": promoted,
        "validation_passed": not errors,
        "smoke_test_passed": smoke_passed,
        "errors": errors,
        "warnings": warnings,
    }
    (release_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(f"Release: {release_dir}")
    print(f"Validation: {'PASSED' if not errors else 'FAILED'}")
    print(f"Smoke test: {'PASSED' if smoke_passed else 'FAILED'}")
    print(f"Promoted: {promoted}")
    for error in errors:
        print(f"ERROR: {error}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
