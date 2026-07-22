# GolfRules.pro — project brief for Claude sessions

Golf app: photograph a bad lie, get an AI ruling (Claude vision) with the official
R&A rule, rate lies and calls on a social feed, share branded images. Built and
run by Michael Zajer (Melbourne, AEST). Live at https://golfrules.pro.
The repo/folder is still named `unplayable` — that was the working title.

## Stack

- **Backend**: FastAPI, `backend/main.py`. SQLAlchemy Core in `backend/db.py` —
  SQLite locally (`data/`), Neon Postgres in prod via `DATABASE_URL`. Lightweight
  in-code migrations run inside `init_db()`; extend that chain, never hand-migrate.
- **AI**: Anthropic API (claude-sonnet-4-6 vision) in `backend/adapter.py`. The
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

1. Change + test locally.
2. Commit and push to GitHub.
3. Deploy: `gcloud run deploy unplayable --source . --region australia-southeast1`
   (project `unplayable-app`). Secrets via Secret Manager: `anthropic-key`,
   `database-url`. Env: `CANONICAL_HOST=golfrules.pro`, no `ACCESS_CODE` set (open).

## Hosting topology (the tricky part)

- Cloud Run region australia-southeast1 has NO native domain mapping, so
  **Firebase Hosting** fronts it: `firebase.json` rewrites `**` to the service.
  `public/` holds only 404.html — `public/index.html` must NEVER exist or it
  shadows the app.
- golfrules.pro: apex A 199.36.158.100; www CNAME → unplayable-app.web.app
  (Crazy Domains DNS; its subdomain field takes ONLY the label, it auto-appends
  the domain). www should redirect to apex, not serve.
- Behind the proxy the real host arrives in `x-forwarded-host`; share URLs use
  it. Direct `*.run.app` hits get a 308 to golfrules.pro (canonical middleware).
- `/robots.txt` + `/sitemap.xml` are backend endpoints (sitemap built live from
  shared lies). robots.txt is host-aware and exempt from the canonical 308: the
  direct run.app host gets disallow-all (Googlebot treats a redirected
  robots.txt as unreachable; the duplicate host must not be indexed).
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
