"""Validate Data for Decision workbooks and local boundary assets."""

from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

DATASETS = {
    "presidential": ("D4D_Kenya_Presidential.xlsx", "Poll_History", "County_Master", ["county", "sub_county"], "support_probability"),
    "kitui_governor": ("D4D_Kitui_Governor.xlsx", "Poll_History", "Ward_Master", ["county", "sub_county", "ward"], "win_probability"),
}


def normalize(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def main():
    failures = []
    for name, (filename, history_sheet, master_sheet, hierarchy, probability) in DATASETS.items():
        path = DATA / filename
        if not path.exists():
            failures.append(f"{name}: missing {filename}")
            continue
        history = pd.read_excel(path, sheet_name=history_sheet)
        master = pd.read_excel(path, sheet_name=master_sheet)
        required = set(hierarchy + ["cycle_id", "cycle_date", "sample_size", "supporters", probability])
        missing = sorted(required.difference(history.columns))
        if missing:
            failures.append(f"{name}: missing columns {missing}")
            continue
        history["cycle_id"] = pd.to_numeric(history["cycle_id"], errors="coerce")
        history[probability] = pd.to_numeric(history[probability], errors="coerce")
        history["sample_size"] = pd.to_numeric(history["sample_size"], errors="coerce")
        history["supporters"] = pd.to_numeric(history["supporters"], errors="coerce")
        if history["cycle_id"].isna().any(): failures.append(f"{name}: missing cycle IDs")
        if ((history[probability] < 0) | (history[probability] > 1)).any(): failures.append(f"{name}: probability outside 0–1")
        if (history["sample_size"] < 0).any() or (history["supporters"] < 0).any(): failures.append(f"{name}: negative counts")
        if (history["supporters"] > history["sample_size"]).any(): failures.append(f"{name}: supporters exceed sample size")
        duplicate_keys = history.duplicated(subset=hierarchy + ["cycle_id"]).sum()
        print(f"{name}: rows={len(history):,}; cycles={history.cycle_id.nunique()}; duplicate keys={duplicate_keys}")
        print(f"  current cycle={int(history.cycle_id.max())}; date={pd.to_datetime(history.cycle_date).max().date()}")
        print(f"  counties={history.county.nunique()}; master rows={len(master):,}")

    for level in ("country", "counties", "subcounties"):
        path = DATA / f"kenya_{level}.geojson"
        if not path.exists():
            failures.append(f"missing boundary asset: {path.name}")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        print(f"boundary {path.name}: features={len(payload.get('features', []))}")

    if failures:
        print("\nVALIDATION FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("\nVALIDATION PASSED")


if __name__ == "__main__":
    main()
