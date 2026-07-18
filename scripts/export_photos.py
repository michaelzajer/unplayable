"""Export the online photos + rulings into the Phase-0 grading workflow.

Pulls every stored submission that has a photo out of DATABASE_URL (your hosted
database included, with --force) and writes:

  scripts/test_photos/db-<id8>.jpg     the photos, ready for test_harness.py
  scripts/grading.csv                  one row per photo with the CURRENT verdict,
                                       plus empty `correct` and `notes` columns

The loop: run this, grade the CSV (y/n + what was wrong), tune the prompt in
backend/adapter.py and rules/rules-reference.md, then re-run
`python scripts/test_harness.py` against the same photos until the hit rate
improves. Real lies from real rounds are the best test set you will ever have.
`python scripts/show_feedback.py` lists the group's wrong-call reports — start
grading with those.
"""

import csv
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import func, select  # noqa: E402

from backend import db  # noqa: E402

PHOTOS_DIR = Path(__file__).resolve().parent / "test_photos"
GRADING = Path(__file__).resolve().parent / "grading.csv"


def main() -> None:
    force = "--force" in sys.argv
    url = str(db.engine.url)
    print(f"Source database: {url}")
    if not url.startswith("sqlite") and not force:
        sys.exit("That is not a local SQLite database. If you are sure, rerun with --force.")

    flagged_only = "--flagged" in sys.argv

    PHOTOS_DIR.mkdir(exist_ok=True)
    with db.engine.connect() as conn:
        rows = conn.execute(
            select(db.submission).where(db.submission.c.image_path.isnot(None))
            .order_by(db.submission.c.created_at)
        ).mappings().all()
        # Signals of a bad call: bad_call votes and wrong-call reports.
        bad_calls = dict(conn.execute(
            select(db.vote.c.submission_id, func.count(db.vote.c.id))
            .where(db.vote.c.stamp == "bad_call")
            .group_by(db.vote.c.submission_id)).all())
        reports: dict[str, str] = {}
        for sid, msg in conn.execute(
                select(db.feedback.c.submission_id, db.feedback.c.message)
                .where(db.feedback.c.submission_id.isnot(None))):
            reports.setdefault(sid, msg)

    if flagged_only:
        rows = [r for r in rows if bad_calls.get(r["id"]) or r["id"] in reports]
        print(f"Flagged only: {len(rows)} lies with bad-call votes or reports.")

    exported = 0
    with GRADING.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "id", "verdict", "ruling_type", "rule_number",
                    "user_note", "bad_calls", "report", "correct", "notes"])
        for r in rows:
            image_id = r["image_path"].removeprefix("/api/image/")
            found = db.get_image(image_id)
            if not found:
                continue
            _, data = found
            name = f"db-{r['id'][:8]}.jpg"
            (PHOTOS_DIR / name).write_bytes(data)
            w.writerow([name, r["id"], r["verdict"], r["ruling_type"],
                        r["rule_number"], r["user_note"] or "",
                        bad_calls.get(r["id"], 0),
                        (reports.get(r["id"], "") or "")[:200], "", ""])
            exported += 1

    print(f"Exported {exported} photos to {PHOTOS_DIR}/ and wrote {GRADING}.")
    print("Grade the 'correct' column (y/n), note what was wrong, tune the prompt "
          "and rules reference, then run: python scripts/test_harness.py")


if __name__ == "__main__":
    main()
