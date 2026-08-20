"""Show the most recent rulings — every upload that got a ruling, feed or not.

Useful for checking real traffic: who snapped a lie and what it ruled, with the
photo URL you can open directly and the share link.

    python scripts/recent.py                 # last 20 (local SQLite)
    python scripts/recent.py --force         # against the prod (Neon) database
    python scripts/recent.py --n 50 --force  # last 50

Photos live in Firebase Storage now, so the image URL is a link you can paste
straight into a browser to see the lie.
"""

import os
import sys
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

MEL = ZoneInfo("Australia/Melbourne")  # handles AEST/AEDT automatically


def _melbourne(dt) -> str:
    """Show a stored UTC timestamp in Melbourne local time."""
    if dt is None:
        return "?"
    if dt.tzinfo is None:                # SQLite hands back naive UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MEL).strftime("%Y-%m-%d %H:%M") + " AEST/AEDT"

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from backend import db  # noqa: E402


def main() -> None:
    force = "--force" in sys.argv
    n = 20
    if "--n" in sys.argv:
        i = sys.argv.index("--n")
        if i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])

    url = str(db.engine.url)
    if not url.startswith("sqlite") and not force:
        sys.exit("That is the prod database — re-run with --force to read it.")

    base = f"https://{os.environ.get('CANONICAL_HOST', 'golfrules.pro').strip() or 'golfrules.pro'}"
    s = db.submission
    with db.engine.connect() as conn:
        rows = conn.execute(
            select(s).order_by(s.c.created_at.desc()).limit(n)
        ).mappings().all()

    if not rows:
        print("No rulings yet.")
        return

    print(f"Last {len(rows)} ruling(s), newest first:\n")
    for r in rows:
        when = _melbourne(r["created_at"])
        posted = "FEED" if r["shared"] else "private"
        where = " · ".join(x for x in [
            (f"hole {r['hole']}" if r.get("hole") else None), r.get("course")] if x)
        print(f"[{when}]  {r['verdict']!r}  ({r['ruling_type']}, {posted}"
              + (f", {where}" if where else "") + ")")
        if r.get("situation"):
            print(f"    lie:   {r['situation']}")
        if r.get("image_path"):
            img = r["image_path"] if r["image_path"].startswith("http") else base + r["image_path"]
            print(f"    photo: {img}")
        slug = db.ruling_slug(r.get("situation"), r.get("verdict"), r["id"])
        print(f"    share: {base}/r/{slug}")
        print(f"    id:    {r['id']}  ·  shares: {r.get('share_count') or 0}")
        print()


if __name__ == "__main__":
    main()
