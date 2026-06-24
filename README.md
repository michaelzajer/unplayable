# Unplayable — alpha

Snap a photo of a weird golf lie, get a ruling back: verdict, a cheeky line, the rule
number, and a link to the official R&A rule. Local-first, no login, runs on your machine.

## What is here

```
backend/        FastAPI app (main), AI adapter, SQLite layer
frontend/       single-page app (index.html)
rules/          the Rules of Golf reference the model reads
scripts/        Phase 0 test harness + a folder for your test photos
data/           SQLite db + uploaded images (created at runtime, gitignored)
```

## Setup

You need Python 3.11+ and an Anthropic API key.

```bash
cd unplayable
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your ANTHROPIC_API_KEY into .env
```

## Phase 0 — test the core before building on it

This is the experiment. Put 30–50 real photos of odd lies into `scripts/test_photos/`,
then run:

```bash
python scripts/test_harness.py
```

It runs each photo through the model and writes `scripts/results.csv` with empty
`correct` and `notes` columns for you to grade. Tune the prompt in `backend/adapter.py`
and the reference in `rules/rules-reference.md` until the hit rate is good enough to
trust for banter. Do not move past this until it is.

## Run the app

```bash
uvicorn backend.main:app --reload
```

Open http://localhost:8000. Take a photo, get a ruling.

### Or with Docker

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

## Use it from your phone

Run the server on your Mac and reach it from your phone:

- **Same wifi:** open `http://<your-mac-lan-ip>:8000` on the phone (run uvicorn with
  `--host 0.0.0.0`).
- **On the course / mobile data:** put it behind Tailscale and hit the Mac's tailnet
  address, or use a `cloudflared` tunnel for a shareable URL. No public exposure needed.

Camera capture and `crypto.randomUUID()` need a secure context, so use HTTPS (Tailscale
serve or cloudflared both give you that) when testing on a real phone rather than plain
`http://` over the LAN.

## Swapping the model

Everything provider-specific lives in `backend/adapter.py`. Change `ANTHROPIC_MODEL` in
`.env` to benchmark Haiku (fast/cheap) or Opus (most accurate) against Sonnet. To try a
local Ollama vision model later, reimplement `get_ruling()` and leave the rest untouched.

## Notes

- The model is instructed to act as a rules assistant and return strict JSON; it only
  cites rules and URLs present in `rules/rules-reference.md`. Verify those URLs against
  the live R&A site before relying on them.
- Image compression happens in the browser (1024px long edge, JPEG ~0.72) to keep
  uploads and token costs down.
- This is an alpha: no auth, no rate limiting, no production hardening. Single machine,
  for you and a few mates.
