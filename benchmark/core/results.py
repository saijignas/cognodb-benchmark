import json
import time
from pathlib import Path

RAW_RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "results" / "raw"


def append_raw_result(platform: str, workload: str, record: dict) -> None:
    RAW_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_RESULTS_DIR / f"{platform}_{workload}.jsonl"
    record = {"platform": platform, "workload": workload, "recorded_at": time.time(), **record}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
