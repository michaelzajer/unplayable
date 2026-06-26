# Unplayable — alpha

Snap a photo of a weird golf lie, get a ruling back: verdict, a cheeky line, the rule
number, and a link to the official R&A rule. No login. Stateless — stores nothing on
local disk — so it runs on any managed host.

## What is here

```
backend/        FastAPI app (main), AI adapter, storage layer (db)
frontend/       single-page app (index.html)
rules/          the Rules of Golf reference the model reads
scripts/        Phase 0 test harness, photos folder, pre-push safety check
data/           local SQLite file (development only; gitignored)
```

## Storage

The app talks to a database through one environment variable, `DATABASE_URL`:

- **Unset** — uses a local SQLite file under `data/`. Zero setup, for development.
- **A Postgres URL** — uses managed Postgres in production (Neon, Supabase, Cloud SQL,
  Render, Railway, and so on). A plain `postgres://...` URL is fine; the app rewrites it
  to the right driver.

Uploaded photos are stored as rows in the same database, not as files. That keeps the
app stateless: there is no disk to mount and nothing is lost on restart or redeploy,
which is what lets it run on serverless and free-tier hosts. (At alpha scale this is the
right trade. If photo volume grows, moving images to object storage like S3, R2, or a
Cloud Storage bucket is a contained change in `backend/db.py` and `backend/main.py`.)

## Setup

Python 3.11+ and an Anthropic API key.

```bash
cd unplayable
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # paste your ANTHROPIC_API_KEY
```

## Phase 0 — test the core before building on it

Put 30–50 real photos of odd lies into `scripts/test_photos/`, then run:

```bash
python scripts/test_harness.py
```

It runs each photo through the model and writes `scripts/results.csv` with empty
`correct` and `notes` columns for you to grade. Tune the prompt in `backend/adapter.py`
and the reference in `rules/rules-reference.md` until the hit rate is good enough to
trust. Do not move past this until it is.

## Run the app locally

```bash
uvicorn backend.main:app --reload
```

Open http://localhost:8000. With no `DATABASE_URL` set, it uses local SQLite.

### With Docker

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

## Deploying

Because the app is stateless, deployment is: build the container, set the environment
variables, attach a managed Postgres, point traffic at it. The two variables that matter
are `ANTHROPIC_API_KEY` and `DATABASE_URL`. No persistent disk required.

1. Create a managed Postgres (Neon, Supabase, Cloud SQL, Render, Railway). Copy its
   connection string.
2. Deploy the container to your host of choice (Cloud Run, Render, Railway, Fly, a VPS).
3. Set `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, and `DATABASE_URL` in the host's
   environment — never in the repo.

The app creates its tables on first start, so there is no separate migration step for
the alpha.

## Swapping the model

Everything provider-specific lives in `backend/adapter.py`. Change `ANTHROPIC_MODEL` in
the environment to benchmark Haiku (fast/cheap) or Opus (most accurate) against Sonnet.
To try a local Ollama vision model later, reimplement `get_ruling()` and leave the rest
untouched.

## Before pushing to GitHub

```bash
git add -A
bash scripts/check_safe.sh     # must print "All clear. Safe to push."
```

It confirms `.env` is ignored, nothing under `data/` is tracked, and no real API key sits
in any tracked file.

## Notes

- The model only cites rules and URLs present in `rules/rules-reference.md`. Verify those
  URLs against the live R&A site before relying on them.
- Image compression happens in the browser (1024px long edge, JPEG ~0.72) to keep
  uploads fast and database rows small.
- This is an alpha: no auth, no rate limiting. Set an Anthropic spend cap and gate access
  before sharing the URL.
