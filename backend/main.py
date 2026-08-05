"""GolfRules.pro API. Run from the project root with:  uvicorn backend.main:app --reload"""

import base64
import html
import json
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

from . import adapter, db, storage

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
            # robots.txt must never redirect: crawlers treat that as fragile or
            # unreachable. The endpoint itself answers host-aware (disallow all
            # on the run.app host, so the duplicate host never gets indexed).
            if request.url.path != "/robots.txt":
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
    # Cutover done: / is the marketing landing (richest, most crawlable page).
    # The app lives at /app. Home-screen launches open /app (manifest start_url).
    return FileResponse(str(FRONTEND_DIR / "landing.html"))


@app.get("/landing")
def landing() -> RedirectResponse:
    # Old preview path — send it to the canonical home.
    return RedirectResponse("/", status_code=301)


@app.get("/app")
def app_page() -> FileResponse:
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


@app.get("/shots/{name}")
def landing_shot(name: str) -> FileResponse:
    """Serve landing-page screenshots from frontend/shots/ (drop 01.png … 08.png
    in there). Name is restricted to a safe pattern, no path traversal."""
    if not re.fullmatch(r"[\w.-]{1,64}", name):
        raise HTTPException(status_code=404, detail="Not found.")
    path = FRONTEND_DIR / "shots" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(str(path),
                        headers={"Cache-Control": "public, max-age=86400"})


# Static SEO/brand assets (served from frontend/). Long cache; they change rarely.
# Explicit routes only — a catch-all here would shadow /robots.txt, /sitemap.xml etc.
def _serve_asset(name: str, media_type: str) -> FileResponse:
    path = FRONTEND_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(str(path), media_type=media_type,
                        headers={"Cache-Control": "public, max-age=604800"})


@app.get("/favicon.ico")
def favicon_ico(): return _serve_asset("favicon.ico", "image/x-icon")


@app.get("/favicon.png")
def favicon_png(): return _serve_asset("favicon.png", "image/png")


@app.get("/apple-touch-icon.png")
def apple_touch_icon(): return _serve_asset("apple-touch-icon.png", "image/png")


@app.get("/og-image.png")
def og_image(): return _serve_asset("og-image.png", "image/png")


@app.get("/icon-192.png")
def icon_192(): return _serve_asset("icon-192.png", "image/png")


@app.get("/icon-512.png")
def icon_512(): return _serve_asset("icon-512.png", "image/png")


@app.get("/manifest.json")
def manifest(): return _serve_asset("manifest.json", "application/manifest+json")


@app.get("/llms.txt")
def llms_txt() -> Response:
    """Plain-language site guide for AI answer engines (GEO). Concise, factual,
    self-contained so an LLM can summarise and cite GolfRules.pro accurately."""
    base = f"https://{CANONICAL_HOST or 'golfrules.pro'}"
    body = f"""# GolfRules.pro

> GolfRules.pro gives golfers an instant ruling for an awkward lie. Photograph
> where your ball has come to rest and it returns the likely ruling — free
> relief, penalty, or play it as it lies — with the official R&A Rule of Golf
> that applies. It is a guide for settling on-course arguments, not an official
> referee or the match committee.

## What it does
- Takes a photo of a golf ball's lie and returns a plain-language ruling.
- Classifies each ruling as free relief, penalty, or play it as it lies.
- Links every ruling to the relevant official R&A rule number at randa.org.
- Hosts a public feed where golfers rate whether the call was right and see the
  hardest-luck lies of the week.

## Lies it rules on (the situations golfers ask about most)
- Free relief from abnormal course conditions: cart paths, sprinkler heads and
  other immovable obstructions, casual/temporary water, ground under repair.
- Drop options from a penalty area (red or yellow stakes).
- A ball in a bunker, and whether relief is available.
- Whether a ball against a tree, a fence, or buried in a bush is unplayable, and
  the unplayable relief options (stroke and distance, back-on-the-line, two
  club-lengths).
- Whether an ugly lie is actually still playable rather than unplayable.

## How it differs from other golf rules apps
- Other rules apps make you pick your situation from a menu. GolfRules.pro reads
  the actual photo of the lie, so the golfer points the camera instead of
  tapping through lists.

## Key facts
- Free to use. No account or login required.
- Built for mobile use on the golf course. Works in any browser.
- Rulings are generated by AI vision and are advisory; the official Rules of
  Golf always take precedence.
- Made in Melbourne, Australia.

## Main pages
- {base}/ — what GolfRules.pro is, how it works, and features.
- {base}/app — the app: take a photo and get a ruling, browse the feed.
- {base}/about — background and how the rulings work.
- {base}/r/<id> — an individual shared ruling with the lie, verdict and rule.

## Common questions
- Is it free? Yes, completely, with no account.
- Does it replace a referee? No. It is a guide to help settle arguments; always
  check the official rule.
- Which rules does it use? The R&A Rules of Golf, linked by rule number.
"""
    return Response(body, media_type="text/plain; charset=utf-8")


@app.get("/robots.txt")
def robots(request: Request) -> Response:
    """Point crawlers at the sitemap; keep them out of the API.

    On the direct *.run.app host, disallow everything instead: it is a
    duplicate of the canonical domain and should never be indexed."""
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host", "")).split(":")[0]
    if host.endswith(".run.app"):
        return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")
    base = f"https://{CANONICAL_HOST or 'golfrules.pro'}"
    body = f"User-agent: *\nAllow: /\nDisallow: /api/\n\nSitemap: {base}/sitemap.xml\n"
    return Response(body, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap() -> Response:
    """Static pages plus every shared lie, built live from the database."""
    base = f"https://{CANONICAL_HOST or 'golfrules.pro'}"
    # /feedback is intentionally absent — it is noindex (never sitemap a noindex page).
    urls: list[tuple[str, str | None]] = [
        (f"{base}/", None), (f"{base}/app", None), (f"{base}/about", None)]
    for row in db.list_shared():
        lastmod = str(row["created_at"])[:10] if row["created_at"] else None
        urls.append((f"{base}{row['share_path']}", lastmod))
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


# Colour + label per ruling type (bar + label), mirrored from the frontend.
_STAKE = {
    "free_relief": ("#0FA958", "Free relief"),
    "penalty": ("#C8102E", "Penalty"),
    "play_as_it_lies": ("#1B2420", "Play it as it lies"),
    "unclear": ("#F5A623", "Ruling"),
}

# Stamp ink per ruling type — matches the feed's VERDICT_TONE (play_as_it_lies is
# RED like penalty, not the dark bar colour), so the share stamp matches the feed.
_STAMP_INK = {
    "free_relief": "#0FA958",
    "penalty": "#C8102E",
    "play_as_it_lies": "#C8102E",
    "unclear": "#1B2420",
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


@app.get("/r/{ref}")
def share(ref: str, request: Request):
    """Public, ungated page for one ruling — what gets shared to mates.

    Accepts either the new readable slug (`ball-against-a-tree-root-a2691fd7`) or
    a bare submission id (old links). Non-canonical refs 301 to the slug URL, so
    there is one canonical address per ruling for SEO and sharing."""
    # Old links pass the full id; new slug URLs end in the 8-char short id.
    # Be forgiving: a share target may have appended junk (a caption) after the
    # id, so take the leading hex of the last hyphen segment and recover.
    tail = ref.rsplit("-", 1)[-1]
    short_m = re.match(r"[0-9A-Za-z]+", tail)  # leading id chars, stops at a space/junk
    sub = db.get_submission(ref) or (
        db.get_submission_by_prefix(short_m.group(0)[:8]) if short_m else None)
    if not sub:
        return JSONResponse({"error": "Not found."}, status_code=404)

    canonical_ref = db.ruling_slug(sub.get("situation"), sub.get("verdict"), sub["id"])
    if ref != canonical_ref:
        return RedirectResponse(f"/r/{canonical_ref}", status_code=301)

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
    ruling_type = sub.get("ruling_type") or "unclear"
    stake_color, label = _STAKE.get(ruling_type, _STAKE["unclear"])
    stamp_ink = _STAMP_INK.get(ruling_type, "#1B2420")

    image_path = sub.get("image_path")
    if image_path:
        # Storage URLs are absolute; the legacy /api/image path is relative.
        abs_url = image_path if image_path.startswith("http") else base + image_path
        img_abs = esc(abs_url)
        image_block = (f'<img src="{esc(image_path)}" class="w-full rounded-2xl shadow-sm '
                       f'border border-ink/10" alt="The lie" />')
    else:
        # No lie photo: fall back to the branded card so the share still previews.
        img_abs = esc(base + "/og-image.png")
        image_block = ""
    og_image = f'<meta property="og:image" content="{img_abs}" />'
    og_image_tw = f'<meta name="twitter:image" content="{img_abs}" />'
    tw_card = "summary_large_image"

    rn, ru = sub.get("rule_number"), sub.get("rule_url")
    rule_block = ""
    if rn and ru:
        rule_block = (f'<a href="{esc(ru)}" target="_blank" rel="noopener" '
                      f'class="inline-flex items-center gap-1.5 text-fairway font-semibold '
                      f'text-sm mt-4">Read Rule {esc(rn)} on randa.org</a>')

    # GEO: structure the ruling as a Q&A (the lie is the question, the verdict is
    # the answer) so answer engines can parse and cite it cleanly. Raw DB values —
    # json.dumps escapes them; </script> is neutralised via <.
    raw_verdict = sub.get("verdict") or "Ruling"
    raw_situation = sub.get("situation") or ""
    raw_explanation = sub.get("explanation") or ""
    answer_text = raw_verdict
    if raw_explanation:
        answer_text += ". " + raw_explanation
    if rn:
        answer_text += f" (R&A Rule {rn})"
    qa = {
        "@context": "https://schema.org",
        "@type": "QAPage",
        "name": raw_verdict,
        "mainEntity": {
            "@type": "Question",
            "name": raw_situation or f"Golf ruling: {raw_verdict}",
            "text": raw_situation or "What is the ruling for this golf lie?",
            "answerCount": 1,
            "acceptedAnswer": {"@type": "Answer", "text": answer_text},
        },
    }
    jsonld = ('<script type="application/ld+json">'
              + json.dumps(qa, ensure_ascii=False).replace("<", "\\u003c")
              + "</script>")

    # The crowd's read: luck average as one pill, call thumbs as two more.
    counts = db.get_stamp_counts(sub["id"])
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
        .replace("__SHARE_URL__", esc(f"{base}/r/{canonical_ref}"))
        .replace("__OG_IMAGE__", og_image)
        .replace("__OG_IMAGE_TW__", og_image_tw)
        .replace("__JSONLD__", jsonld)
        .replace("__TWITTER_CARD__", tw_card)
        .replace("__IMAGE_BLOCK__", image_block)
        .replace("__STAKE__", stake_color)
        .replace("__STAMP_INK__", stamp_ink)
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
        # Firebase Storage in prod (served from the CDN); DB fallback locally.
        image_path = storage.store_image(raw, media_type)

    record = {**result, "image_path": image_path, "user_note": note, "session_id": session_id,
              "course": course, "hole": hole_num, "played_on": played}
    result["id"] = db.insert_submission(record)
    result["image_path"] = image_path
    result["share_path"] = "/r/" + db.ruling_slug(
        result.get("situation"), result.get("verdict"), result["id"])
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


@app.post("/api/share/{submission_id}")
def record_share(submission_id: str, request: Request):
    """Count one share of a ruling — drives The Feed's most-shared sort.
    Ungated (sharing is public) but rate limited so it cannot be spammed."""
    _rate_limit(request, "share", limit=20, window_s=60)
    db.add_share(submission_id.strip()[:64])  # no-op if the id does not exist
    return {"ok": True}


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
