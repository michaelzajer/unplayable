"""Seed the feed from a folder of real lie photos.

Each photo goes through the REAL ruling pipeline (adapter -> Claude), so the
verdict, rule reference, and suggested stamp are genuine. The script then:
  - resizes to a 1024px long edge and re-encodes as JPEG (strips EXIF/GPS,
    mirroring what the app's frontend does),
  - stores the image + submission and posts it to the feed,
  - spreads created_at over the past ~3 weeks so the feed looks lived-in,
  - adds a small crowd of stamps clustered around the AI's read
    (~60% agree, the rest spill to neighbouring stamps on the scale).

All seeded votes use 'seed-' session ids, so `seed_votes.py --clear` removes
them without touching real ones.

Honesty note: seeded VOTES are still fake counts. The lies are real and the
rulings are real; the crowd is not. Run with --max-votes 0 to seed photos
with no votes at all, or keep counts small and low-key.

Usage (project root, venv active, ANTHROPIC_API_KEY in .env):
  pip install Pillow                       # one-off
  python scripts/seed_photos.py scripts/seed_photos --wipe

Optional scripts/seed_photos/metadata.csv with columns:
  file,note,course,hole,played_on          (all except file may be blank)
"""

import argparse
import base64
import csv
import io
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import delete, insert, text, update  # noqa: E402

from backend import adapter, db  # noqa: E402

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("This script needs Pillow. Run: pip install Pillow")

EXTS = {".jpg", ".jpeg", ".png", ".webp"}
STAMP_ORDER = list(db.STAMPS)


def guard(force: bool) -> None:
    url = str(db.engine.url)
    print(f"Target database: {url}")
    if not url.startswith("sqlite") and not force:
        sys.exit("That is not a local SQLite database. If you are sure, rerun with --force.")


def prep_image(path: Path) -> bytes:
    """Resize + re-encode, exactly like the app's client does. Strips EXIF/GPS."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)  # bake in the rotation before metadata goes
    img = img.convert("RGB")
    img.thumbnail((1024, 1024))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=72)
    return buf.getvalue()


def crowd(n: int) -> list[str]:
    """A realistic spread on the luck slider, skewed to the rough end."""
    return [random.choices(STAMP_ORDER, weights=(1, 1, 2, 3, 3))[0] for _ in range(n)]


def load_metadata(folder: Path) -> dict[str, dict]:
    meta_path = folder / "metadata.csv"
    if not meta_path.exists():
        return {}
    with meta_path.open(newline="", encoding="utf-8") as f:
        return {row["file"].strip(): row for row in csv.DictReader(f)}


def wipe() -> None:
    with db.engine.begin() as conn:
        v = conn.execute(delete(db.vote)).rowcount
        s = conn.execute(delete(db.submission)).rowcount
        i = conn.execute(delete(db.image)).rowcount
    print(f"Wiped: {s} submissions, {i} images, {v} votes.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("folder", help="folder containing the seed photos")
    p.add_argument("--wipe", action="store_true", help="remove ALL existing feed content first")
    p.add_argument("--min-votes", type=int, default=2)
    p.add_argument("--max-votes", type=int, default=7)
    p.add_argument("--course", default="", help="default course when metadata.csv has none")
    p.add_argument("--force", action="store_true", help="allow a non-SQLite database")
    args = p.parse_args()

    guard(args.force)
    folder = Path(args.folder)
    photos = sorted(q for q in folder.iterdir() if q.suffix.lower() in EXTS)
    if not photos:
        sys.exit(f"No photos found in {folder} (looking for {', '.join(sorted(EXTS))}).")
    print(f"{len(photos)} photos to seed.")

    if args.wipe:
        wipe()

    meta = load_metadata(folder)
    now = datetime.now(timezone.utc)
    ages_h = sorted(random.sample(range(2, 21 * 24), len(photos)))  # spread over ~3 weeks

    seeded = skipped = 0
    for idx, photo in enumerate(photos):
        m = meta.get(photo.name, {})
        note = (m.get("note") or "").strip()[:280]
        course = (m.get("course") or args.course).strip()[:60] or None
        hole = m.get("hole", "").strip()
        hole = int(hole) if hole.isdigit() and 1 <= int(hole) <= 36 else None
        played_on = (m.get("played_on") or "").strip() or None

        raw = prep_image(photo)
        result = adapter.get_ruling(base64.b64encode(raw).decode(), "image/jpeg", note)

        if result.get("on_topic") is False:
            print(f"  SKIP {photo.name}: model says not a golf lie ({result.get('verdict')})")
            skipped += 1
            continue
        if result.get("error"):
            print(f"  SKIP {photo.name}: adapter error ({result['error'][:80]})")
            skipped += 1
            continue

        image_id = db.insert_image(raw, "image/jpeg")
        rec = {**result, "image_path": f"/api/image/{image_id}", "user_note": note,
               "session_id": "seed-founder", "course": course, "hole": hole,
               "played_on": played_on}
        sid = db.insert_submission(rec)
        db.set_shared(sid)

        # Newest file in the list gets the freshest timestamp.
        created = now - timedelta(hours=ages_h[len(photos) - 1 - idx])
        with db.engine.begin() as conn:
            conn.execute(update(db.submission)
                         .where(db.submission.c.id == sid).values(created_at=created))
            n_votes = random.randint(args.min_votes, max(args.min_votes, args.max_votes))
            for stamp in crowd(n_votes):
                conn.execute(insert(db.vote).values(
                    id=str(uuid.uuid4()), submission_id=sid,
                    session_id=f"seed-{uuid.uuid4()}", stamp=stamp,
                    created_at=created + timedelta(hours=random.uniform(0.5, 48))))

        print(f"  OK   {photo.name}: {result.get('verdict', '?')!r} · {n_votes} ratings")
        seeded += 1

    print(f"\nDone: {seeded} seeded, {skipped} skipped. "
          "Seeded votes can be removed later with: python scripts/seed_votes.py --clear")


if __name__ == "__main__":
    main()
