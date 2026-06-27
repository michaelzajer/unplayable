"""Unplayable API. Run from the project root with:  uvicorn backend.main:app --reload"""

import base64

from dotenv import load_dotenv

load_dotenv()

from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from . import adapter, db

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"

# --- Input guardrails (defensive; the client cannot be trusted to enforce these) ---
MAX_IMAGE_BYTES = 6 * 1024 * 1024          # reject anything larger than 6 MB
MAX_NOTE_LEN = 280                          # cap the free-text note
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
# Magic-byte signatures so a renamed non-image cannot sneak past the content-type.
MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}


def _looks_like_image(raw: bytes) -> bool:
    if raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":  # webp
        return True
    return False


db.init_db()

app = FastAPI(title="Unplayable")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/about")
def about() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "about.html"))


@app.post("/api/ruling")
async def ruling(
    photo: UploadFile | None = File(None),
    note: str = Form(""),
    session_id: str = Form(""),
):
    note = note.strip()[:MAX_NOTE_LEN]
    raw = None
    media_type = "image/jpeg"

    if photo is not None:
        raw = await photo.read()
        if len(raw) > MAX_IMAGE_BYTES:
            return JSONResponse({"error": "Image is too large (6 MB max)."}, status_code=413)
        media_type = (photo.content_type or "").lower()
        if media_type not in ALLOWED_TYPES or not _looks_like_image(raw):
            return JSONResponse({"error": "That file is not a supported image."}, status_code=415)

    if raw is None and not note:
        return JSONResponse({"error": "Send a photo or describe the lie."}, status_code=400)

    image_b64 = base64.b64encode(raw).decode() if raw is not None else None

    # Ask the model for a ruling AND an on-topic decision before storing anything.
    result = adapter.get_ruling(image_b64, media_type, note)

    # Off-topic / abuse: refuse, and store nothing (no DB row, never reaches the feed).
    if result.get("on_topic") is False:
        return {**result, "id": None, "image_path": None, "stored": False}

    # On topic: now persist the image and the ruling.
    image_path = None
    if raw is not None:
        image_id = db.insert_image(raw, media_type)
        image_path = f"/api/image/{image_id}"

    record = {**result, "image_path": image_path, "user_note": note, "session_id": session_id}
    result["id"] = db.insert_submission(record)
    result["image_path"] = image_path
    result["stored"] = True
    return result


@app.get("/api/image/{image_id}")
def get_image(image_id: str):
    found = db.get_image(image_id)
    if not found:
        return JSONResponse({"error": "Not found."}, status_code=404)
    content_type, data = found
    return Response(content=data, media_type=content_type,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/feed")
def feed():
    return db.get_feed()


@app.post("/api/vote")
def vote(
    submission_id: str = Form(...),
    value: int = Form(...),
    session_id: str = Form(...),
):
    db.add_vote(submission_id, session_id, value)
    return {"ok": True}
