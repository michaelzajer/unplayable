"""Delete specific lies (submissions) — photo, votes and all.

Usage (project root, venv active):
  python scripts/delete_lie.py --list                # see ids, dates, verdicts
  python scripts/delete_lie.py <id> [<id> ...]       # delete by id (full or first 8+ chars)
  add --force when the database is not local SQLite (e.g. your hosted Neon).
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import delete, select  # noqa: E402

from backend import db  # noqa: E402


def guard(force: bool) -> None:
    url = str(db.engine.url)
    print(f"Target database: {url}")
    if not url.startswith("sqlite") and not force:
        sys.exit("That is not a local SQLite database. If you are sure, rerun with --force.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ids", nargs="*", help="submission ids to delete (prefix of 8+ chars is fine)")
    p.add_argument("--list", action="store_true", help="list all submissions and exit")
    p.add_argument("--force", action="store_true", help="allow a non-SQLite database")
    args = p.parse_args()

    guard(args.force)
    s = db.submission

    with db.engine.begin() as conn:
        if args.list or not args.ids:
            rows = conn.execute(select(
                s.c.id, s.c.created_at, s.c.verdict, s.c.course, s.c.hole, s.c.shared
            ).order_by(s.c.created_at.desc())).all()
            for r in rows:
                place = f" · hole {r.hole}" if r.hole else ""
                flag = "feed" if r.shared else "private"
                print(f"  {r.id}  {r.created_at:%d %b}  [{flag}] {r.verdict or '(no verdict)'}"
                      f"{(' · ' + r.course) if r.course else ''}{place}")
            print(f"{len(rows)} submissions." if rows else "Nothing stored.")
            return

        for ref in args.ids:
            ref = ref.strip()
            if len(ref) < 8:
                print(f"  SKIP {ref}: give at least 8 characters of the id.")
                continue
            row = conn.execute(select(s.c.id, s.c.image_path, s.c.verdict)
                               .where(s.c.id.like(ref + "%"))).all()
            if not row:
                print(f"  SKIP {ref}: no submission matches.")
                continue
            if len(row) > 1:
                print(f"  SKIP {ref}: matches {len(row)} submissions — use more characters.")
                continue
            sub = row[0]
            conn.execute(delete(db.vote).where(db.vote.c.submission_id == sub.id))
            if sub.image_path and sub.image_path.startswith("/api/image/"):
                conn.execute(delete(db.image).where(
                    db.image.c.id == sub.image_path.removeprefix("/api/image/")))
            conn.execute(delete(s).where(s.c.id == sub.id))
            print(f"  DELETED {sub.id[:8]}…  {sub.verdict or ''}")

    print("Done.")


if __name__ == "__main__":
    main()
