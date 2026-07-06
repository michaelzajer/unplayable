"""Seed fake stamp votes — LOCAL TESTING ONLY.

Simulates a crowd so you can see tallies, leaders, and the Worst-lies sort
without forty mates. Every seeded vote uses a session id prefixed 'seed-',
so they are identifiable and fully removable with --clear.

Honesty guardrail: never run this against the real feed. Seeded counts on a
live feed would break the app's own no-fake-votes rule.

Usage (from the project root, venv active):
  python scripts/seed_votes.py --list                 # show shared lies and their ids
  python scripts/seed_votes.py --id <sub_id> --brutal 12 --cooked 5 --stiff 3
  python scripts/seed_votes.py --random 30            # scatter 30 votes over all shared lies
  python scripts/seed_votes.py --clear                # remove every seeded vote
"""

import argparse
import random
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # pick up DATABASE_URL exactly as the app would

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import delete, insert, select  # noqa: E402

from backend import db  # noqa: E402


def guard(force: bool) -> None:
    url = str(db.engine.url)
    print(f"Target database: {url}")
    if not url.startswith("sqlite") and not force:
        sys.exit("That is not a local SQLite database. If you are sure, rerun with --force.")


def shared_ids(conn):
    return [r.id for r in conn.execute(
        select(db.submission.c.id).where(db.submission.c.shared == True))]  # noqa: E712


def seed(conn, sub_id: str, stamp: str, n: int) -> None:
    now = datetime.now(timezone.utc)
    for _ in range(n):
        conn.execute(insert(db.vote).values(
            id=str(uuid.uuid4()), submission_id=sub_id,
            session_id=f"seed-{uuid.uuid4()}", stamp=stamp, created_at=now))
    print(f"  {sub_id[:8]}…  +{n} × {stamp}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--id", help="submission id to stamp (default: all shared lies for --random)")
    for s in db.STAMPS:
        p.add_argument(f"--{s}", type=int, default=0, metavar="N")
    p.add_argument("--random", type=int, default=0, metavar="N",
                   help="scatter N random stamps across shared lies")
    p.add_argument("--list", action="store_true", help="list shared lies and exit")
    p.add_argument("--clear", action="store_true", help="delete all seeded votes")
    p.add_argument("--force", action="store_true", help="allow a non-SQLite database")
    args = p.parse_args()

    guard(args.force)

    with db.engine.begin() as conn:
        if args.clear:
            r = conn.execute(delete(db.vote).where(db.vote.c.session_id.like("seed-%")))
            print(f"Removed {r.rowcount} seeded votes.")
            return

        if args.list:
            rows = conn.execute(select(
                db.submission.c.id, db.submission.c.verdict, db.submission.c.created_at
            ).where(db.submission.c.shared == True)).all()  # noqa: E712
            for r in rows:
                print(f"  {r.id}  {r.created_at:%d %b}  {r.verdict or '(no verdict)'}")
            print(f"{len(rows)} shared lies." if rows else "No shared lies yet — post one first.")
            return

        explicit = {s: getattr(args, s) for s in db.STAMPS if getattr(args, s) > 0}
        if explicit:
            if not args.id:
                sys.exit("Give --id <submission_id> when seeding specific stamps (see --list).")
            for stamp, n in explicit.items():
                seed(conn, args.id, stamp, n)
        elif args.random:
            ids = [args.id] if args.id else shared_ids(conn)
            if not ids:
                sys.exit("No shared lies to stamp — post one to the feed first.")
            # Weighted towards the cursed end: banter feeds skew dramatic.
            weights = {"gift": 1, "fluke": 1, "fair_cop": 2, "stiff": 3, "cooked": 3, "brutal": 4}
            for _ in range(args.random):
                seed(conn, random.choice(ids),
                     random.choices(list(weights), weights=weights.values())[0], 1)
        else:
            p.print_help()
            return

    print("Done. Refresh the feed. Remove everything later with: "
          "python scripts/seed_votes.py --clear")


if __name__ == "__main__":
    main()
