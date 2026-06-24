"""Unplayable API. Run from the project root with:  uvicorn backend.main:app --reload"""

import base64
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import adapter, db

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = ROOT / "data" / "uploads"
FRONTEND_DIR = ROOT / "frontend"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
db.init_db()

app = FastAPI(title="Unplayable")

# Permissive CORS for local development (e.g. opening the page from your phone).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.post("/api/ruling")
async def ruling(
    photo: UploadFile | None = File(None),
    note: str = Form(""),
    session_id: str = Form(""),
):
    note = note.strip()
    image_b64 = None
    media_type = "image/jpeg"
    image_path = None

    if photo is not None:
        raw = await photo.read()
        media_type = photo.content_type or "image/jpeg"
        ext = ".png" if "png" in media_type else ".jpg"
        fname = f"{uuid.uuid4()}{ext}"
        (UPLOAD_DIR / fname).write_bytes(raw)
        image_path = f"/uploads/{fname}"
        image_b64 = base64.b64encode(raw).decode()

    if image_b64 is None and not note:
        return JSONResponse({"error": "Send a photo or describe the lie."}, status_code=400)

    result = adapter.get_ruling(image_b64, media_type, note)

    record = {
        **result,
        "image_path": image_path,
        "user_note": note,
        "session_id": session_id,
    }
    result["id"] = db.insert_submission(record)
    result["image_path"] = image_path
    return result


@app.get("/api/feed")
def feed():
    return db.get_feed()


@app.post("/api/vote")
def vote(
    submission_id: str = Form(...),
    value: int = Form(...),
    session_id: str = Form(...),
):
    db.add_vote(submission_id, session_id, 1 if value > 0 else -1)
    return {"ok": True}
