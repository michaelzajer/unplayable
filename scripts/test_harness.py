"""
Phase 0 — the quality experiment.

Drop 30-50 real photos of weird lies into scripts/test_photos/, then run:

    python scripts/test_harness.py

It calls the adapter directly (no server needed), runs every photo through the model,
and writes results to scripts/results.csv with two empty columns — `correct` and
`notes` — for you to grade by hand. Do not build the rest of the app until the hit
rate is good enough to be fun and trustworthy.
"""

import base64
import csv
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from backend import adapter  # noqa: E402

PHOTO_DIR = ROOT / "scripts" / "test_photos"
OUT_CSV = ROOT / "scripts" / "results.csv"
EXTS = {".jpg", ".jpeg", ".png"}

FIELDS = [
    "filename",
    "situation",
    "ruling_type",
    "verdict",
    "explanation",
    "rule_number",
    "rule_url",
    "confidence",
    "model_used",
    "correct",  # you fill this: y / n / partial
    "notes",    # you fill this: what it got wrong, if anything
]


def media_type_for(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def main() -> None:
    photos = sorted(p for p in PHOTO_DIR.glob("*") if p.suffix.lower() in EXTS)
    if not photos:
        print(f"No photos found in {PHOTO_DIR}. Add some .jpg or .png files and re-run.")
        return

    print(f"Running {len(photos)} photo(s) through {adapter.MODEL}...\n")
    rows = []
    for i, path in enumerate(photos, 1):
        b64 = base64.b64encode(path.read_bytes()).decode()
        result = adapter.get_ruling(b64, media_type_for(path), note="")
        print(f"[{i}/{len(photos)}] {path.name}")
        print(f"    {result.get('verdict')}  (rule {result.get('rule_number')}, "
              f"conf {result.get('confidence')})")
        rows.append({**{k: result.get(k, '') for k in FIELDS if k not in ('filename', 'correct', 'notes')},
                     "filename": path.name, "correct": "", "notes": ""})

    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {OUT_CSV}")
    print("Open it, grade the `correct` column, and look at where it fails.")


if __name__ == "__main__":
    main()
