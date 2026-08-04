"""One-off: move existing lie photos from the database into Firebase Storage.

For every submission whose image_path is the legacy "/api/image/{id}" form, this
reads the photo out of the DB, uploads it to the Storage bucket, and rewrites
image_path to the new public Storage URL. The old image rows are LEFT in place
(safe rollback); purge them later once you are happy.

Run from the project root with the bucket configured and credentials available:

    export FIREBASE_STORAGE_BUCKET=your-bucket-name
    python scripts/migrate_images_to_storage.py            # dry run (counts only)
    python scripts/migrate_images_to_storage.py --go       # actually migrate
    python scripts/migrate_images_to_storage.py --go --force   # against the prod DB

Credentials: locally, run `gcloud auth application-default login` first, or set
GOOGLE_APPLICATION_CREDENTIALS to a service-account key with Storage access.
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update  # noqa: E402

from backend import db, storage  # noqa: E402


def main() -> None:
    go = "--go" in sys.argv
    force = "--force" in sys.argv

    if not storage.enabled():
        sys.exit("FIREBASE_STORAGE_BUCKET is not set — nothing to migrate to.")

    url = str(db.engine.url)
    print(f"Database: {url}")
    print(f"Bucket:   {storage.BUCKET}")
    if not url.startswith("sqlite") and not force:
        sys.exit("That is a non-SQLite (prod) database. Re-run with --force when sure.")

    with db.engine.connect() as conn:
        rows = conn.execute(
            select(db.submission.c.id, db.submission.c.image_path)
            .where(db.submission.c.image_path.like("/api/image/%"))
        ).all()

    print(f"{len(rows)} photo(s) to move." + ("" if go else "  (dry run — pass --go to migrate)"))
    if not go:
        return

    moved = 0
    for sid, image_path in rows:
        image_id = image_path.removeprefix("/api/image/")
        found = db.get_image(image_id)
        if not found:
            print(f"  ! {sid[:8]}: image {image_id[:8]} missing, skipped")
            continue
        content_type, data = found
        new_url = storage.store_image(data, content_type)
        with db.engine.begin() as conn:
            conn.execute(update(db.submission)
                         .where(db.submission.c.id == sid)
                         .values(image_path=new_url))
        moved += 1
        print(f"  {sid[:8]} -> {new_url}")

    print(f"\nMoved {moved} photo(s). Old image rows left in place for rollback.")


if __name__ == "__main__":
    main()
