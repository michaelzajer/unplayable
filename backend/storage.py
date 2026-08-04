"""
Lie-photo storage for GolfRules.pro.

PROD: when FIREBASE_STORAGE_BUCKET is set, photos upload to Firebase Storage (a
Cloud Storage bucket) and are served straight from Google's CDN. The image bytes
never pass through Cloud Run or the database, which is what lets the app scale —
the DB then holds only small text rows.

LOCAL / TESTS: with no bucket configured, this falls back to the database BLOB
store (db.insert_image / the /api/image/{id} route), so the app still runs
offline with no GCP credentials and the test suite is unchanged.

store_image() returns the value to save as submission.image_path:
  - an absolute https URL   (Firebase Storage, prod)
  - or "/api/image/{id}"     (database fallback, local/legacy)
"""

import os
import uuid

from . import db

BUCKET = os.environ.get("FIREBASE_STORAGE_BUCKET", "").strip()

_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

_bucket = None


def enabled() -> bool:
    return bool(BUCKET)


def _get_bucket():
    global _bucket
    if _bucket is None:
        from google.cloud import storage  # lazy: only needed when a bucket is set
        _bucket = storage.Client().bucket(BUCKET)
    return _bucket


def store_image(data: bytes, content_type: str) -> str:
    """Upload the photo and return the image_path to store on the submission."""
    if not BUCKET:
        # DB fallback (local dev / tests): served by the /api/image/{id} route.
        image_id = db.insert_image(data, content_type)
        return f"/api/image/{image_id}"

    ext = _EXT.get(content_type, "jpg")
    name = f"lies/{uuid.uuid4()}.{ext}"
    blob = _get_bucket().blob(name)
    blob.cache_control = "public, max-age=31536000, immutable"
    blob.upload_from_string(data, content_type=content_type)
    # Bucket grants allUsers objectViewer (public read) — see CLAUDE.md. The
    # public URL is served from Google's edge, not Cloud Run.
    return f"https://storage.googleapis.com/{BUCKET}/{name}"
