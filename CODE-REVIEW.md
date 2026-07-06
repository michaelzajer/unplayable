# Unplayable — Security & Fitness Review
Reviewed: 6 July 2026. Scope: full backend (`backend/`), all frontend pages, Dockerfile, and deployment posture. Focus areas as requested: only golf photos get in, and the AI stays inside golf-rules territory.

## Verdict

The app is in good shape for a gated alpha. The core containment design is sound: the adapter is the only file that touches the AI, model output is treated as untrusted, off-topic submissions are never stored, and all HTML output is escaped. I fixed eight issues directly (listed below), all verified by a 32-check test suite now at `scripts/test_backend.py`. Three things need a decision from you before the next phase; none block the Woodlands round.

## The two questions you asked

### Does the app ensure only golf photos are uploaded?

Yes, through four layers, with one caveat. First, the client restricts the picker to images and re-encodes through a canvas, but that is advisory only — anyone can call the API directly. Second, the server independently enforces content type, magic-byte signatures (JPEG/PNG/WebP), and a 6 MB cap, so non-image files cannot get in regardless of what the client claims. Third, the model makes the "is this golf" call (`on_topic`), and this is the correct layer for it — no signature check can tell a bunker from a birthday cake. Fourth, and this is the important structural property: **off-topic images are never persisted**. The image row is only inserted after the on-topic check passes, which the test suite confirms.

The caveat: the golf judgement itself rests on the model, so a false positive (a photo the model wrongly reads as golf) would be stored and could be posted to the feed. For the alpha that risk is acceptable; before a public launch you want a report/remove path on feed posts (see open items).

### Is the AI confined to golf-rules review?

Yes, and the containment is defence-in-depth rather than prompt-hope. The system prompt scopes the model to exactly one task and instructs it to refuse everything else, and player notes and any text inside the photo are explicitly framed as untrusted description, never instructions. But the real containment is structural: the model's output is parsed into a fixed schema and every field is now validated server-side — `ruling_type` against four values, `suggested_stamp` against the six-stamp enum (invalid output falls back to `fair_cop`, so a wonky generation cannot invent a seventh), `confidence` clamped to 0–1, and `rule_url` rejected unless it starts with `https://www.randa.org/`. Even a fully jailbroken model response cannot execute anything, link anywhere off-domain, or write outside those columns. Off-topic refusals also skip the retry UI, so the app does not invite repeated probing.

## Fixed in this pass

1. **`.env` baked into Docker images (High).** `COPY . .` with no `.dockerignore` shipped your live Anthropic key and database URL inside every image pushed to a registry. Added `.dockerignore`. If any previous image was pushed anywhere shared, rotate the key.
2. **Model-controlled link injection (Medium).** `rule_url` came from the model and was rendered as an `href` on the public share page and the feed. A manipulated generation could have emitted a `javascript:` or phishing URL. Now allow-listed to `randa.org` server-side, before storage.
3. **No rate limiting (Medium).** `/api/ruling` let one client burn unbounded Anthropic spend, and `/api/verify-code` allowed brute-forcing the access code with a constant-time comparison but unlimited attempts. Added per-IP sliding-window limits: rulings 10/5min, code checks 10/min, votes 60/min.
4. **Vote endpoint trusted its inputs (Medium).** Any integer counted as a vote on any submission id, including unshared and nonexistent ones. Votes now validate the stamp against the enum and require the target to exist and be shared.
5. **CORS wide open (Low).** `allow_origins=["*"]` with all methods and headers. The frontend is same-origin, so the middleware is now off by default and opt-in via an `ALLOWED_ORIGINS` env var.
6. **Model output stored unvalidated (Low).** `ruling_type` and `confidence` went into the database as whatever the model said. Now normalised and clamped.
7. **Images served without `nosniff` (Low).** Added `X-Content-Type-Options: nosniff` to `/api/image` responses.
8. **Unbounded `session_id` (Low).** Client-supplied session ids are now capped at 64 characters everywhere they enter the API.

## Open items — your call

**Vote integrity (decide before the feed matters).** Session identity is a client-generated UUID in localStorage. One person with a script can mint sessions and stuff any tally; the rate limiter slows this but cannot stop it. Fine for a feed of mates; not fine once "worst lie of the month" carries clubhouse bragging rights. The proper fix is a server-issued signed cookie, or accounts. Cheap interim: server-set httpOnly cookie on first visit.

**Feed and images are public even when the app is gated (decide intentionally).** `/api/feed`, `/api/image/*`, and `/r/*` bypass the access code. That is deliberate — share links must work for people without the code — but it means every posted lie and photo is world-readable if the URL leaks. If the alpha should be genuinely private, gate the feed and accept that share links break for non-members.

**EXIF location data (before public launch).** The real frontend strips metadata by re-encoding through a canvas, but a direct API upload keeps EXIF intact, including GPS coordinates, and the image is stored and served byte-for-byte. Strip EXIF server-side (Pillow re-encode) before storing. Worth doing at the same time as a photo report/remove button — currently nothing posted to the feed can be taken down without touching the database.

## Noted, no action needed at alpha scale

The rate limiter is in-memory per instance, so it resets on deploy and is per-container on Cloud Run — acceptable now, use Redis or similar if you scale out. Feed aggregation pulls up to 200 rows and tallies in Python — fine into the thousands of posts, revisit after. The vote table's unique constraint only applies to freshly created databases; migrated tables rely on the delete-then-insert transaction, which is correct but worth folding into a real migration when you adopt one. The Docker container runs as root — add a non-root user when convenient. There is no CSP header, which is hard to do meaningfully while the pages use CDN Tailwind and inline scripts — park it. The access code lives in localStorage and rides every request as a header; that is a shared password, not authentication, and everyone should understand it as such.

## Verification

`scripts/test_backend.py` runs 32 checks against a scratch database (your real data is untouched): legacy boolean-vote migration to stamps, adapter output validation including hostile input, the access gate and rate limits, upload rejection of fake and disallowed files, the never-store-off-topic guarantee, stamp enum enforcement, one-stamp-per-golfer replacement and clearing, the weighted worst-lies sort, and all three feed tabs. Run it any time with `python3 scripts/test_backend.py` from the project root. All 32 pass as of this review.
