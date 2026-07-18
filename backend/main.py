"""GolfRules.pro API. Run from the project root with:  uvicorn backend.main:app --reload"""

import base64
import html
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from dotenv import load_dotenv

load_dotenv()

from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)

from . import adapter, db

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"

# --- Access gate ---
# Set ACCESS_CODE to require a shared code. Leave it unset/empty to run open (local dev).
ACCESS_CODE = os.environ.get("ACCESS_CODE", "").strip()


def require_access_code(x_access_code: str | None = Header(default=None)) -> None:
    if not ACCESS_CODE:
        return  # gate disabled
    supplied = (x_access_code or "").strip()
    # Constant-time compare so the check does not leak the code via timing.
    if not supplied or not secrets.compare_digest(supplied, ACCESS_CODE):
        raise HTTPException(status_code=401, detail="Invalid or missing access code.")


# --- Input guardrails (defensive; the client cannot be trusted to enforce these) ---
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_NOTE_LEN = 280
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}


# --- Rate limiting (in-memory, per instance) ---
# Protects the expensive/abusable endpoints: the AI call (spend), code
# verification (brute force), and voting (tally stuffing). Sliding window per
# client IP. On Cloud Run behind a proxy, the first x-forwarded-for hop is the
# caller. In-memory is fine for a single alpha instance; note in the review doc.
_rl_lock = threading.Lock()
_rl_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request, bucket: str, limit: int, window_s: int) -> None:
    key = f"{bucket}:{_client_ip(request)}"
    now = time.monotonic()
    with _rl_lock:
        hits = _rl_hits[key]
        while hits and now - hits[0] > window_s:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(status_code=429,
                                detail="Steady on — too many requests. Try again shortly.")
        hits.append(now)


def _looks_like_image(raw: bytes) -> bool:
    if raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return True
    return False


db.init_db()

app = FastAPI(title="GolfRules.pro")

# --- Canonical host ---
# Set CANONICAL_HOST (e.g. golfrules.pro) to bounce direct *.run.app hits to the
# real domain. Requests proxied by Firebase Hosting carry x-forwarded-host and
# pass through untouched; local dev (no env var, no .run.app host) is unaffected.
CANONICAL_HOST = os.environ.get("CANONICAL_HOST", "").strip()


# --- Branded error pages ---
# With Firebase rewriting every path here, users should never meet a bare
# JSON error or a generic platform page for anything this app controls.
_ERROR_PAGE = """<!DOCTYPE html>
<html lang="en-AU"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — GolfRules.pro</title>
<style>
  body{margin:0;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:#F4F8FB;color:#1B2420;}
  header{background:#1B2D4F;padding:1.4rem 1.5rem;}
  header b{color:#fff;font-size:1.4rem;}header b i{color:#EF4444;font-style:normal;}
  header b u{color:#C8D3E6;text-decoration:none;}
  main{max-width:26rem;margin:3rem auto;padding:0 1.5rem;text-align:center;}
  h1{font-size:3rem;margin:0;color:#1B2D4F;}
  p{color:#4A5560;line-height:1.5;}
  a{display:inline-block;margin-top:1rem;background:#1B2D4F;color:#fff;text-decoration:none;
    padding:.7rem 1.4rem;border-radius:.5rem;font-weight:600;}
  small{display:block;margin-top:2rem;color:#8892A0;}
</style></head><body>
<header><b>Golf<i>Rules</i><u>.pro</u></b></header>
<main><h1>__CODE__</h1><p>__MSG__</p>
<a href="/">Back to the feed</a>
<small>A guide, not the match committee.</small></main>
</body></html>"""


def _error_html(code: str, title: str, msg: str) -> HTMLResponse:
    page = (_ERROR_PAGE.replace("__CODE__", code)
            .replace("__TITLE__", title).replace("__MSG__", msg))
    return HTMLResponse(page, status_code=int(code))


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Not found."}, status_code=404)
    return _error_html("404", "Lost ball",
                       "That page has gone missing — probably deep in the rough.")


@app.exception_handler(Exception)
async def server_error(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Something went wrong. Try again shortly."},
                            status_code=500)
    return _error_html("500", "Bad lie",
                       "Something went wrong on our end. Give it a minute and play on.")


@app.middleware("http")
async def canonical_redirect(request: Request, call_next):
    if CANONICAL_HOST and not request.headers.get("x-forwarded-host"):
        host = request.headers.get("host", "").split(":")[0]
        if host.endswith(".run.app"):
            url = f"https://{CANONICAL_HOST}{request.url.path}"
            if request.url.query:
                url += "?" + request.url.query
            return RedirectResponse(url, status_code=308)
    return await call_next(request)

# The frontend is served by this same app, so cross-origin access is only needed
# if ALLOWED_ORIGINS is explicitly set (comma-separated). Default: same-origin only.
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["X-Access-Code"],
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/about")
def about() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "about.html"))


@app.get("/feed")
def feed_page() -> FileResponse:
    # The home page is feed-first now; /feed stays alive for old links.
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/feedback")
def feedback_page() -> FileResponse:
    return FileResponse(str(FRONTEND_DIR / "feedback.html"))


@app.get("/robots.txt")
def robots() -> Response:
    """Point crawlers at the sitemap; keep them out of the API."""
    base = f"https://{CANONICAL_HOST or 'golfrules.pro'}"
    body = f"User-agent: *\nAllow: /\nDisallow: /api/\n\nSitemap: {base}/sitemap.xml\n"
    return Response(body, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap() -> Response:
    """Static pages plus every shared lie, built live from the database."""
    base = f"https://{CANONICAL_HOST or 'golfrules.pro'}"
    urls: list[tuple[str, str | None]] = [(f"{base}/", None), (f"{base}/about", None)]
    for row in db.list_shared():
        lastmod = str(row["created_at"])[:10] if row["created_at"] else None
        urls.append((f"{base}/r/{row['id']}", lastmod))
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in urls:
        entry = f"  <url><loc>{html.escape(loc)}</loc>"
        if lastmod:
            entry += f"<lastmod>{lastmod}</lastmod>"
        parts.append(entry + "</url>")
    parts.append("</urlset>")
    return Response("\n".join(parts), media_type="application/xml")


@app.post("/api/feedback")
def submit_feedback(
    request: Request,
    message: str = Form(""),
    contact: str = Form(""),
    submission_id: str = Form(""),
    session_id: str = Form(""),
):
    """Wrong-ruling reports. Deliberately ungated (feedback should never be
    locked out) but rate limited and length capped."""
    _rate_limit(request, "feedback", limit=5, window_s=600)
    message = message.strip()[:2000]
    if not message:
        return JSONResponse({"error": "Say what went wrong."}, status_code=400)
    submission_id = submission_id.strip()[:64]
    # Only store a reference to a ruling that actually exists.
    if submission_id and not db.get_submission(submission_id):
        submission_id = ""
    db.insert_feedback({
        "message": message,
        "contact": contact.strip()[:120],
        "submission_id": submission_id or None,
        "session_id": session_id.strip()[:64] or None,
    })
    return {"ok": True}


# Colour + label per ruling type, mirrored from the frontend.
_STAKE = {
    "free_relief": ("#0FA958", "Free relief"),
    "penalty": ("#C8102E", "Penalty"),
    "play_as_it_lies": ("#1B2420", "Play it as it lies"),
    "unclear": ("#F5A623", "Ruling"),
}

def _luck_bucket(avg: float) -> tuple[str, str]:
    """Label + ink for an average luck rating (1 good lie .. 5 hard luck)."""
    if avg < 1.8:
        return ("Good lie", "#0FA958")
    if avg < 2.6:
        return ("Not bad", "#0FA958")
    if avg < 3.4:
        return ("Fair", "#C28400")
    if avg < 4.2:
        return ("Rough", "#C8102E")
    return ("Hard luck", "#C8102E")
_SHARE_TEMPLATE = (FRONTEND_DIR / "share.html").read_text(encoding="utf-8")


@app.get("/r/{submission_id}")
def share(submission_id: str, request: Request):
    """Public, ungated page for one ruling — what gets shared to mates."""
    sub = db.get_submission(submission_id)
    if not sub:
        return JSONResponse({"error": "Not found."}, status_code=404)

    # Build an absolute base URL. Behind Firebase Hosting -> Cloud Run the
    # original domain arrives in x-forwarded-host; TLS ends upstream, so trust
    # the forwarded proto too.
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host", request.url.netloc))
    base = f"{proto}://{host}"

    def esc(v) -> str:
        return html.escape(str(v or ""), quote=True)

    verdict = esc(sub.get("verdict") or "Ruling")
    situation = esc(sub.get("situation"))
    explanation = esc(sub.get("explanation"))
    stake_color, label = _STAKE.get(sub.get("ruling_type") or "unclear", _STAKE["unclear"])

    image_path = sub.get("image_path")
    if image_path:
        img_abs = esc(base + image_path)
        image_block = (f'<img src="{esc(image_path)}" class="w-full rounded-2xl shadow-sm '
                       f'border border-ink/10" alt="The lie" />')
        og_image = f'<meta property="og:image" content="{img_abs}" />'
        og_image_tw = f'<meta name="twitter:image" content="{img_abs}" />'
        tw_card = "summary_large_image"
    else:
        image_block = og_image = og_image_tw = ""
        tw_card = "summary"

    rn, ru = sub.get("rule_number"), sub.get("rule_url")
    rule_block = ""
    if rn and ru:
        rule_block = (f'<a href="{esc(ru)}" target="_blank" rel="noopener" '
                      f'class="inline-flex items-center gap-1.5 text-fairway font-semibold '
                      f'text-sm mt-4">Read Rule {esc(rn)} on randa.org</a>')

    # The crowd's read: luck average as one pill, call thumbs as two more.
    counts = db.get_stamp_counts(submission_id)
    luck_counts = {k: n for k, n in counts.items() if k.isdigit()}
    total = sum(luck_counts.values())
    pills = ""
    if total:
        avg = sum(int(k) * n for k, n in luck_counts.items()) / total
        label, ink = _luck_bucket(avg)
        pills += (f'<span class="pill" style="border-color:{ink};color:{ink};">'
                  f'{label} · {avg:.1f}/5 · ×{total}</span>')
    gc, bc = counts.get("good_call", 0), counts.get("bad_call", 0)
    if gc:
        pills += (f'<span class="pill" style="border-color:#0FA958;color:#0FA958;">'
                  f'Good call ×{gc}</span>')
    if bc:
        pills += (f'<span class="pill" style="border-color:#C8102E;color:#C8102E;">'
                  f'Bad call ×{bc}</span>')
    if pills:
        stamp_block = (f'<div class="mt-4 flex flex-wrap gap-1.5" '
                       f'aria-label="How golfers rated this lie">{pills}</div>')
    else:
        stamp_block = ('<p class="mt-4 font-mono text-xs" style="color:#65706A;">'
                       'NOT RATED YET — BE THE FIRST</p>')

    desc = explanation or situation or "A golf rules ruling from GolfRules.pro."
    if total:
        desc = f"Rated {label.upper()} ({avg:.1f}/5) by {total} golfers. {desc}"

    page = (
        _SHARE_TEMPLATE
        .replace("__TITLE__", verdict)
        .replace("__DESC__", desc)
        .replace("__SHARE_URL__", esc(f"{base}/r/{submission_id}"))
        .replace("__OG_IMAGE__", og_image)
        .replace("__OG_IMAGE_TW__", og_image_tw)
        .replace("__TWITTER_CARD__", tw_card)
        .replace("__IMAGE_BLOCK__", image_block)
        .replace("__STAKE__", stake_color)
        .replace("__LABEL__", esc(label))
        .replace("__VERDICT__", verdict)
        .replace("__SITUATION__", situation)
        .replace("__EXPLANATION__", explanation)
        .replace("__STAMP_BLOCK__", stamp_block)
        .replace("__RULE_BLOCK__", rule_block)
    )
    return HTMLResponse(page)


@app.get("/api/gate")
def gate():
    """Tell the frontend whether a code is required (so local dev shows no gate)."""
    return {"required": bool(ACCESS_CODE)}


@app.post("/api/verify-code")
def verify_code(request: Request, code: str = Form("")):
    if not ACCESS_CODE:
        return {"ok": True, "required": False}
    _rate_limit(request, "verify", limit=10, window_s=60)  # no brute-forcing the code
    ok = bool(code) and secrets.compare_digest(code.strip(), ACCESS_CODE)
    return {"ok": ok, "required": True}


@app.post("/api/ruling")
async def ruling(
    request: Request,
    photo: UploadFile | None = File(None),
    note: str = Form(""),
    session_id: str = Form(""),
    course: str = Form(""),
    hole: str = Form(""),
    played_on: str = Form(""),
    _: None = Depends(require_access_code),
):
    # Each call costs an AI request; cap the burn from any single client.
    _rate_limit(request, "ruling", limit=10, window_s=300)
    note = note.strip()[:MAX_NOTE_LEN]
    session_id = session_id.strip()[:64]

    # Optional round details — validated, never trusted.
    course = course.strip()[:60] or None
    hole_num = None
    if hole.strip().isdigit():
        h = int(hole.strip())
        if 1 <= h <= 36:  # 18 + the odd composite/extra-holes course
            hole_num = h
    played = played_on.strip()
    played = played if re.fullmatch(r"\d{4}-\d{2}-\d{2}", played) else None
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

    result = adapter.get_ruling(image_b64, media_type, note)

    if result.get("on_topic") is False:
        return {**result, "id": None, "image_path": None, "stored": False}

    image_path = None
    if raw is not None:
        image_id = db.insert_image(raw, media_type)
        image_path = f"/api/image/{image_id}"

    record = {**result, "image_path": image_path, "user_note": note, "session_id": session_id,
              "course": course, "hole": hole_num, "played_on": played}
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
                    headers={"Cache-Control": "public, max-age=31536000, immutable",
                             "X-Content-Type-Options": "nosniff"})


@app.get("/api/feed")
def feed(sort: str = "latest", mine: int = 0, session_id: str = ""):
    sort = sort if sort in ("latest", "worst") else "latest"
    session_id = session_id.strip()[:64]
    return db.get_feed(sort=sort, mine=bool(mine), session_id=session_id or None)


@app.post("/api/vote")
def vote(
    request: Request,
    submission_id: str = Form(...),
    stamp: str = Form(...),
    session_id: str = Form(...),
    _: None = Depends(require_access_code),
):
    _rate_limit(request, "vote", limit=60, window_s=60)
    session_id = session_id.strip()[:64]
    if not session_id:
        return JSONResponse({"error": "Missing session."}, status_code=400)
    if stamp == "clear":            # clears the golfer's CALL rating
        db.clear_vote(submission_id, session_id, kind="call")
        return {"ok": True}
    if stamp == "clear_luck":
        db.clear_vote(submission_id, session_id, kind="luck")
        return {"ok": True}
    if not db.add_vote(submission_id, session_id, stamp):
        return JSONResponse({"error": "Unknown stamp or lie."}, status_code=400)
    return {"ok": True}


@app.get("/api/export/ratings")
def export_ratings(_: None = Depends(require_access_code)):
    """Rulings joined with their good_call/bad_call tallies, newest first.
    Model-improvement data: pull with the access code header, e.g.
      curl -H "X-Access-Code: <code>" https://<host>/api/export/ratings
    """
    return db.export_call_ratings()


@app.post("/api/post-to-feed")
def post_to_feed(
    submission_id: str = Form(...),
    _: None = Depends(require_access_code),
):
    if not db.set_shared(submission_id):
        return JSONResponse({"error": "Not found."}, status_code=404)
    return {"ok": True}
