"""Detect incoming workbooks and run the Data for Decision release gate.

Run once from a scheduler, or use --watch for a long-running folder watcher.
Promotion is opt-in with --promote. Successful file hashes are recorded so a
workbook is not processed repeatedly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INCOMING = ROOT / "data" / "incoming"
STATE_PATH = ROOT / "data" / "refresh_state.json"
LOG_PATH = ROOT / "data" / "refresh_log.jsonl"
LOCK_PATH = ROOT / "data" / ".refresh.lock"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def infer_dataset(path: Path) -> str | None:
    name = path.name.lower()
    if "kitui" in name or "governor" in name:
        return "kitui_governor"
    if "presidential" in name or "kenya" in name:
        return "presidential"
    return None


def acquire_lock() -> bool:
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "created_at_utc": datetime.now(timezone.utc).isoformat()}, handle)
        return True
    except FileExistsError:
        return False


def append_log(entry: dict) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")


def process(path: Path, dataset: str, promote: bool, state: dict) -> bool:
    digest = file_hash(path)
    processed = state.setdefault("processed", {})
    if processed.get(str(path)) == digest:
        return True
    command = [sys.executable, str(ROOT / "scripts" / "release_gate.py"), "--dataset", dataset, "--workbook", str(path)]
    if promote:
        command.append("--promote")
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    entry = {
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "workbook": str(path),
        "dataset": dataset,
        "sha256": digest,
        "promote_requested": promote,
        "exit_code": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }
    append_log(entry)
    state.setdefault("attempts", []).append(entry)
    state["attempts"] = state["attempts"][-50:]
    if result.returncode == 0:
        processed[str(path)] = digest
        state["last_success"] = entry
        print(f"SUCCESS: {path.name} ({dataset})")
        return True
    state["last_failure"] = entry
    print(f"FAILED: {path.name} ({dataset}); see {LOG_PATH}")
    if result.stdout:
        print(result.stdout[-2000:])
    if result.stderr:
        print(result.stderr[-2000:])
    return False


def scan(dataset: str | None, promote: bool) -> int:
    INCOMING.mkdir(parents=True, exist_ok=True)
    state = load_json(STATE_PATH, {"processed": {}, "attempts": []})
    files = sorted(path for path in INCOMING.glob("*.xlsx") if not path.name.startswith("~$"))
    candidates = []
    for path in files:
        resolved_dataset = dataset or infer_dataset(path)
        if resolved_dataset:
            candidates.append((path, resolved_dataset))
        else:
            print(f"SKIPPED: cannot infer dataset from {path.name}; use --dataset")
    results = [process(path, resolved_dataset, promote, state) for path, resolved_dataset in candidates]
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    return 1 if any(result is False for result in results) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["presidential", "kitui_governor"], help="Required only when the filename does not identify the dataset")
    parser.add_argument("--promote", action="store_true", help="Pass --promote to the release gate after validation and smoke tests pass")
    parser.add_argument("--watch", action="store_true", help="Keep polling the incoming folder")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds for --watch (default: 60)")
    args = parser.parse_args()
    if args.interval < 5:
        parser.error("--interval must be at least 5 seconds")
    if not acquire_lock():
        print(f"Another refresh process is already running; lock: {LOCK_PATH}")
        return 2
    try:
        while True:
            result = scan(args.dataset, args.promote)
            if not args.watch:
                return result
            time.sleep(args.interval)
    finally:
        if LOCK_PATH.exists():
            LOCK_PATH.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
