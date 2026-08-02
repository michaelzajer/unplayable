# GolfRules.pro — project brief for Claude sessions

Golf app: photograph a bad lie, get an AI ruling (Claude vision) with the official
R&A rule, rate lies and calls on a social feed, share branded images. Built and
run by Michael Zajer (Melbourne, AEST). Live at https://golfrules.pro.
The repo/folder is still named `unplayable` — that was the working title.

## Stack

- **Backend**: FastAPI, `backend/main.py`. SQLAlchemy Core in `backend/db.py` —
  SQLite locally (`data/`), Neon Postgres in prod via `DATABASE_URL`. Lightweight
  in-code migrations run inside `init_db()`; extend that chain, never hand-migrate.
- **AI**: Google Gemini (multimodal) via the `google-genai` SDK in
  `backend/adapter.py`; model from `GEMINI_MODEL` (default `gemini-3.5-flash`;
  2.5-flash is retired for new keys). Thinking is disabled (`thinking_budget=0`)
  and `max_output_tokens=2048` so the JSON is not truncated by reasoning tokens.
  Key from `GEMINI_API_KEY`/`GOOGLE_API_KEY`. Swapped from Anthropic Claude; the
  prompt, on-topic gate, and `_normalise()` contract are unchanged. The
  prompt has an on-topic gate (golf photos only), treats user notes as untrusted,
  and `_normalise()` enforces: ruling_type whitelist, confidence clamped 0-1,
  rule_url must start `https://www.randa.org/`. Verdicts are 2-5 punchy words,
  ≤28 chars. Do NOT let the model default to "unplayable — one stroke".
- **Frontend**: vanilla JS + Tailwind CDN, no build step, in `frontend/`
  (index.html is the app; about, feedback, share templates). Michael edits these
  files directly himself — ALWAYS re-read current file state before editing, and
  never revert his changes.
- **Design**: see `DESIGN.md` (source of truth). Navy #1B2D4F, red #C8102E,
  wordmark Golf<red>Rules</red><light>.pro</light>. Mobile-first: 17px root font
  under 640px, 16px inputs (iOS zoom guard).

## Rating model (v4)

Golfers rate ONE thing in the UI: the ruling, via quiet `good_call` / `bad_call`
links (`.linkact`) on each card. Same-kind votes replace; `clear` removes a call.

**Legacy luck votes** (`"1".."5"` in `vote.stamp`) still exist in the data and
still power the hardest-luck sort (`STAMP_WEIGHTS`), the luck buckets
(<1.8 Good lie, <2.6 Not bad, <3.4 Fair, <4.2 Rough, else Hard luck), and the
crowd stamps on share pages/images — but the luck slider was removed from the
UI, so no NEW luck votes are being captured. The backend still accepts luck
stamps and `clear_luck` (scripts and tests use them). Do not resurface the
slider without asking Michael.

**Honesty guardrails (non-negotiable)**: never fabricate counts or ratings.
Seeded/simulated votes must carry `seed-` session ids so they can be stripped
(`scripts/seed_votes.py --clear`). When the rating model changes, delete
old-scale votes rather than reinterpreting them.

## Run and test locally

```
uvicorn backend.main:app --reload        # from project root, venv active
python3 scripts/test_backend.py          # 52-check suite, scratch SQLite, no network
```
Use http://localhost:8000 (not 0.0.0.0 — crypto.randomUUID needs a secure origin;
there is a `makeId()` fallback regardless). It is NOT an npm project — no package.json.

## Release cycle (always in this order)

1. Change + test locally (`uvicorn` serves everything from `frontend/`).
2. Commit and push to GitHub.
3. Deploy — TWO targets now (static edge + dynamic backend):
   - **Static pages/assets changed** (frontend/landing, index, about, feedback,
     images, shots): `python3 scripts/build_static.py` (syncs frontend/ → public/,
     regenerates public/sitemap.xml from the DB) then
     `firebase deploy --only hosting`.
   - **Backend/dynamic changed** (backend/*, /r/ share template, /api):
     `gcloud run deploy unplayable --source . --region australia-southeast1`
     (project `unplayable-app`; secrets via Secret Manager `gemini-key`
     (→ `GEMINI_API_KEY`), `database-url`; env `CANONICAL_HOST=golfrules.pro`,
     no `ACCESS_CODE`).
   - Most changes touch both — do both. Keep one Cloud Run instance warm:
     `gcloud run services update unplayable --min-instances 1 --region australia-southeast1`.

## Hosting topology (edge-first — the important part)

- **Firebase Hosting fronts Cloud Run** (australia-southeast1 has no native domain
  mapping). Firebase serves files in `public/` FROM ITS CDN EDGE first, then falls
  through the `**` rewrite to Cloud Run for anything not found. `cleanUrls:true`
  so `/about` serves `public/about.html`, `/app` serves `public/app.html`.
- **Static, crawl-critical pages serve from the edge → no cold start, no 5xx.**
  `scripts/build_static.py` copies frontend/ into public/ (landing→`index.html`,
  app shell→`app.html`, about, feedback, all icons/og-image/manifest/shots) and
  writes `public/sitemap.xml`. These public/ copies are GENERATED (gitignored;
  source of truth is frontend/). `public/index.html` SHOULD exist now — it is the
  landing (this reverses the old rule from when / served the app via Cloud Run).
- **Only genuinely dynamic paths reach Cloud Run**: `/api/*`, `/r/` share pages
  (per-ruling server-rendered meta), and `/sitemap.xml` if the static one is
  absent. The `**` rewrite stays as a safety net so a missing build never breaks
  the site (it just falls back to Cloud Run with cold-start risk).
- Cloud Run still serves the same pages at its own routes for LOCAL DEV (uvicorn)
  and direct *.run.app hits; in prod the edge shadows them. `public/` is excluded
  from the Docker image (.dockerignore) — Cloud Run serves from `frontend/`.
- golfrules.pro: apex A 199.36.158.100; www CNAME → unplayable-app.web.app
  (Crazy Domains DNS; its subdomain field takes ONLY the label, it auto-appends
  the domain). www should redirect to apex, not serve.
- Behind the proxy the real host arrives in `x-forwarded-host`; share URLs use
  it. Direct `*.run.app` hits get a 308 to golfrules.pro (canonical middleware).
## SEO / GEO

Every page has a unique title, meta description, canonical, and OG/Twitter tags.
- **landing.html**: JSON-LD `@graph` (Organization, WebSite, WebApplication,
  FAQPage) + a visible `<details>` FAQ that mirrors the FAQPage. Canonical `/`.
- **index.html** (app): canonical + og:url point at `/app` (its post-cutover home),
  so it does not compete with the landing for `/`.
- **about.html**: AboutPage JSON-LD. **feedback.html**: `noindex, follow` (thin form).
- **/r/ share pages** (share.html + `share()`): per-ruling QAPage JSON-LD (lie =
  question, verdict+rule = answer), self-canonical, og:image = the lie photo or
  the branded card fallback.
- Brand/GEO assets served from `frontend/` via backend routes (explicit, never a
  catch-all — that would shadow /robots.txt etc.): `/og-image.png` (1200×630
  navy card), `/favicon.ico|.png`, `/apple-touch-icon.png`, and `/llms.txt`
  (plain-language site guide for answer engines). These are NEW binary files —
  remember to `git add frontend/og-image.png frontend/favicon.* frontend/apple-touch-icon.png frontend/shots/`.

- `/sitemap.xml` is a backend endpoint (built live from shared lies).
  `public/robots.txt` is a STATIC Firebase file so Googlebot's robots fetch
  terminates at the CDN edge (Firebase serves public/ files before the rewrite;
  Googlebot robots fetches through the Cloud Run rewrite were failing).
  The backend `/robots.txt` endpoint still covers direct *.run.app hits
  (host-aware disallow-all, exempt from the canonical 308 — a redirected
  robots.txt reads as unreachable, and the duplicate host must not be indexed).
  Search Console: Domain property, sitemap submitted 18 Jul 2026.

## Email (golfrules.pro)

Google Workspace. MX single record `smtp.google.com` prio 1; SPF
`v=spf1 include:_spf.google.com ~all`; DKIM `google._domainkey` TXT (verified).
Titan/Crazy Domains trial email is being retired — **cancel before its 30-day
trial ends (~mid-Aug 2026)**.

## Admin toolkit (`scripts/`, run from root; `--force` required for prod DB)

- `delete_lie.py --list` / `<id>` / `--worst` — list or delete lies (8+ char id prefix ok)
- `fix_lie.py <id> --show | --rerun --note "…" | --verdict … | --bury DAYS` — correct
  a ruling by hand or re-run the AI with a corrective note; bury pushes it down Latest
- `seed_photos.py` / `seed_votes.py` — seeding (EXIF-stripped; seed- session ids)
- `export_photos.py [--flagged]` — export photos + grading.csv; `--flagged` = only
  lies with bad_call votes or wrong-call reports
- `show_feedback.py` — list wrong-call reports
- `test_harness.py` — re-run the prompt against `scripts/test_photos/`

## Prompt-improvement loop (when real bad-call data accumulates)

`export_photos.py --flagged --force` → grade grading.csv (correct y/n + notes) →
tune `backend/adapter.py` prompt and `rules/rules-reference.md` → `test_harness.py`
until hit rate improves → normal release cycle.

## Front door: landing at /, app at /app (cutover DONE)

- `/` serves `frontend/landing.html` — static marketing page (hero, 4-screenshot
  slider using `frontend/shots/01–04.png` via `/shots/{name}`, how-it-works,
  features, `<details>` FAQ, footer). Richest, most crawlable page; self-canonical.
- `/app` serves the app (`index.html`). `/landing` 301-redirects to `/`.
- `/feed` still serves the app (old links).
- PWA: `frontend/manifest.json` (`start_url=/app`, navy theme, icons 192/512/180)
  linked from index.html + landing.html, served at `/manifest.json`; icons at
  `/icon-192.png` `/icon-512.png`. Home-screen launch opens `/app` → on-course
  fast entry, skipping the landing. index.html has theme-color + apple-mobile
  meta.
- Internal links repointed to `/app`: about.html (back-to-app, `/app#snap`, Feed),
  feedback.html (both back-to-app), share.html "Get your own ruling" CTA. Share
  header wordmark and landing are the brand home (`/`).
- Welcome overlay still lives in the app (first visit → Get started → snap screen),
  gated by localStorage `golfrules_welcomed`.
- NEW binary files to `git add`: frontend/shots/, frontend/og-image.png,
  frontend/favicon.*, frontend/apple-touch-icon.png, frontend/icon-192.png,
  frontend/icon-512.png, frontend/manifest.json.

## Open items

- Search Console: sitemap "Couldn't fetch" + "Robots.txt unreachable" as of
  18 Jul 2026 — endpoints verified live and correct; waiting on Google's crawler
  (new-domain DNS propagation). Recheck, then Request indexing.
- Cancel Titan email trial (see Email above).
- No Anthropic spend cap and no access code — rate limits only (rulings 10/5min/IP).
- `CODE-REVIEW.md` open items: vote integrity, public feed/images.
- Roadmap: GPS location toggle → course auto-match.

## Working conventions

- Australian English in all user-facing copy; the tone is banter-between-mates,
  never corporate. "A guide, not the match committee."
- Michael co-edits files during sessions; check current state before every edit.
- Keep the whole thing honest: no fake social proof, no dark patterns.
