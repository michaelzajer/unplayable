"""Correct a stored lie — edit fields directly, or re-run the ruling.

Usage (project root, venv active; add --force for the hosted database):

  python scripts/fix_lie.py <id> --show
      See every stored field for one submission.

  python scripts/fix_lie.py <id> --verdict "FREE DROP" --ruling-type free_relief \
      --explanation "Sprinkler head — immovable obstruction, drop away." \
      --rule-number 16.1 --suggested-stamp fluke
      Set fields by hand. Only the flags you pass are changed.

  python scripts/fix_lie.py <id> --rerun --note "ball is INSIDE the red penalty area"
      Re-run the real AI on the stored photo with a corrective note. Overwrites
      the AI fields (verdict, ruling type, explanation, rule, suggested stamp).

Ids come from: python scripts/delete_lie.py --list  (first 8+ chars are enough).
"""

import argparse
import base64
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import select, update  # noqa: E402

from backend import db  # noqa: E402

RULING_TYPES = ("free_relief", "penalty", "play_as_it_lies", "unclear")


def guard(force: bool) -> None:
    url = str(db.engine.url)
    print(f"Target database: {url}")
    if not url.startswith("sqlite") and not force:
        sys.exit("That is not a local SQLite database. If you are sure, rerun with --force.")


def find(conn, ref: str):
    if len(ref) < 8:
        sys.exit("Give at least 8 characters of the id.")
    rows = conn.execute(select(db.submission).where(
        db.submission.c.id.like(ref + "%"))).mappings().all()
    if not rows:
        sys.exit(f"No submission matches '{ref}'.")
    if len(rows) > 1:
        sys.exit(f"'{ref}' matches {len(rows)} submissions — use more characters.")
    return dict(rows[0])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("id", help="submission id (8+ character prefix)")
    p.add_argument("--show", action="store_true", help="print all fields and exit")
    p.add_argument("--rerun", action="store_true",
                   help="re-run the AI ruling on the stored photo")
    p.add_argument("--note", default=None,
                   help="with --rerun: corrective context for the AI; alone: replace the note")
    p.add_argument("--verdict", default=None)
    p.add_argument("--explanation", default=None)
    p.add_argument("--situation", default=None)
    p.add_argument("--ruling-type", default=None, choices=RULING_TYPES)
    p.add_argument("--rule-number", default=None)
    p.add_argument("--rule-url", default=None)
    p.add_argument("--suggested-stamp", default=None, choices=list(db.STAMPS))
    p.add_argument("--course", default=None)
    p.add_argument("--hole", default=None)
    p.add_argument("--played-on", default=None)
    p.add_argument("--bury", type=int, default=None, metavar="DAYS",
                   help="push this lie DAYS into the past so it drops down the Latest feed")
    p.add_argument("--feature", action="store_true",
                   help="pin this lie to the very top of The Feed")
    p.add_argument("--unfeature", action="store_true", help="remove the top-of-Feed pin")
    p.add_argument("--force", action="store_true", help="allow a non-SQLite database")
    args = p.parse_args()

    guard(args.force)

    with db.engine.connect() as conn:
        sub = find(conn, args.id.strip())

    if args.show:
        for k in ("id", "created_at", "shared", "verdict", "ruling_type", "situation",
                  "explanation", "rule_number", "rule_url", "confidence",
                  "suggested_stamp", "user_note", "course", "hole", "played_on",
                  "image_path", "model_used", "featured", "share_count"):
            print(f"  {k:16} {sub.get(k)!r}")
        return

    changes: dict = {}

    if args.rerun:
        if not sub.get("image_path"):
            sys.exit("No stored photo on this one — set the fields by hand instead.")
        image_id = sub["image_path"].removeprefix("/api/image/")
        found = db.get_image(image_id)
        if not found:
            sys.exit("Stored photo is missing from the image table.")
        content_type, raw = found
        note = args.note if args.note is not None else (sub.get("user_note") or "")
        from backend import adapter  # imported late: needs ANTHROPIC_API_KEY
        print("Re-running the ruling…")
        result = adapter.get_ruling(base64.b64encode(raw).decode(), content_type, note)
        if result.get("error"):
            sys.exit(f"Adapter error, nothing changed: {result['error'][:200]}")
        if result.get("on_topic") is False:
            sys.exit("Model refused it as not-golf, nothing changed. "
                     "Try a clearer --note and rerun.")
        changes = {k: result[k] for k in ("verdict", "ruling_type", "situation",
                                          "explanation", "rule_number", "rule_url",
                                          "confidence")}
        changes["model_used"] = result.get("model_used")
        if args.note is not None:
            changes["user_note"] = args.note.strip()[:280]
    else:
        # Manual field edits — validated like the API would.
        if args.verdict is not None:
            changes["verdict"] = args.verdict.strip()
        if args.explanation is not None:
            changes["explanation"] = args.explanation.strip()
        if args.situation is not None:
            changes["situation"] = args.situation.strip()
        if args.ruling_type is not None:
            changes["ruling_type"] = args.ruling_type
        if args.rule_number is not None:
            changes["rule_number"] = args.rule_number.strip()
        if args.rule_url is not None:
            if args.rule_url and not args.rule_url.startswith("https://www.randa.org/"):
                sys.exit("rule-url must start with https://www.randa.org/")
            changes["rule_url"] = args.rule_url.strip()
        if args.suggested_stamp is not None:
            changes["suggested_stamp"] = args.suggested_stamp
        if args.note is not None:
            changes["user_note"] = args.note.strip()[:280]
        if args.course is not None:
            changes["course"] = args.course.strip()[:60] or None
        if args.hole is not None:
            h = args.hole.strip()
            if h and not (h.isdigit() and 1 <= int(h) <= 36):
                sys.exit("hole must be 1-36 (or empty to clear)")
            changes["hole"] = int(h) if h else None
        if args.played_on is not None:
            d = args.played_on.strip()
            if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                sys.exit("played-on must be YYYY-MM-DD (or empty to clear)")
            changes["played_on"] = d or None
        if args.bury is not None:
            if not 1 <= args.bury <= 365:
                sys.exit("bury must be 1-365 days")
            from datetime import datetime, timedelta, timezone
            changes["created_at"] = datetime.now(timezone.utc) - timedelta(days=args.bury)
        if args.feature:
            changes["featured"] = True
        if args.unfeature:
            changes["featured"] = False

    if not changes:
        sys.exit("Nothing to change — pass --show, --rerun, or at least one field flag.")

    with db.engine.begin() as conn:
        conn.execute(update(db.submission)
                     .where(db.submission.c.id == sub["id"]).values(**changes))

    print(f"Updated {sub['id'][:8]}…")
    for k, v in changes.items():
        print(f"  {k:16} -> {str(v)[:90]!r}")


if __name__ == "__main__":
    main()
