# Unplayable — Design Brief

Visual direction: vibrant civic-app energy (Snap Send Solve register) with the rubber-stamp
identity kept. Tone: cheeky and honest — "a guide, not the match committee".
Reference implementation: `frontend/index.html`. Match it, do not reinterpret it.

## Tokens

Colours (CSS variables, defined in index.html; mirrored in the Tailwind config,
`backend/main.py` `_STAKE`, and the share-image canvas):

- `--fairway` #29ABE2 — brand chrome ONLY: header, tabs, buttons, FAB, bottom bar
- `--card` #FFFFFF — cards / slips
- `--page` #F4F8FB — page background
- `--pencil` #1B2420 — body text
- `--pencil-soft` #65706A — metadata, secondary text
- `--ink-green` #0FA958 — good outcomes (Good call; free-relief verdicts; links)
- `--ink-amber` #C28400 — reserved (no current use); darker ochre, never bright yellow
- `--ink-red` #E03131 — bad outcomes (Bad call; penalty verdicts; disputed banner)
- `--flag` #F5A623 — focus rings and the "unclear" ruling flag only
- `--rule` #DFE6EB — borders and ruling lines

Colour semantics are strict: blue means "the app and its actions"; green/red mean
"good call / bad call". Never mix the two jobs.

Type: Oswald (condensed caps: verdicts, stamps, headers, primary buttons), Karla (body),
IBM Plex Mono (rule numbers, tallies, metadata). Google Fonts CDN. Stack stays Tailwind
CDN + vanilla JS, no build step.

## Layout: feed-first

The home page IS the feed (Snap Send Solve pattern). Sticky blue header with tabs
Latest / The Feed / My lies ("The Feed" = hardest-luck sort).
Below: a rating filter row, then the slips. A floating round camera button ("Snap") sits bottom-centre, cradled in a slim
bottom bar (Feed · Snap · About). Tapping it opens the capture view: two large square
buttons (Take photo / Choose photo), note field, optional round details (course, hole
stepper that wraps 18↔1, date), then "Get the ruling". `/feed` serves the same page.

## Signature element: the verdict stamp

Rotated rubber-stamp treatment (`.stamp`): 3px border, slight rotation, ink-fade mask.
Used for AI verdicts over the photo (top-left on feed cards), on the result card, the
share page, and drawn onto the share image (with a second low-alpha stroke pass for ink
bleed). Verdicts are stamp-length by prompt: 2–4 words, e.g. "UNPLAYABLE — ONE STROKE".
Green ink favourable, red unfavourable, pencil/flag for neutral/unclear.

## Rating: call chips + share

ONE rating per golfer per lie — the CALL (the ruling): quiet "Good call" (green,
cup icon) / "Bad call" (red, splash icon) underlined text actions (`.linkact` with
tone classes), not boxed buttons. Muted (75% opacity) until stamped; selected =
full ink, bold, thick underline. Tap to rate, tap again to clear, switching
replaces. Stored as good_call/bad_call. Vote rows share one table; one row per
kind per (submission, session), enforced by same-kind replacement.

The luck slider was removed (unused). Legacy luck votes ("1".."5") remain in the
table and still drive the luck buckets, feed filters and share-image crowd stamp
where present; no new luck votes are collected.

- On feed cards the action row sits directly UNDER the photo (where the slider
  was): call chips on the left, Post-to-feed / Share on the right. Share is a
  quiet underlined mono link with an icon (`.linkact`), not a boxed button.
- On the result card the call chips and Share sit on one row; "Post to the feed"
  stays as the primary full-width button above it.
- Average luck buckets (legacy): <1.8 Good lie, <2.6 Not bad, <3.4 Fair, <4.2
  Rough, else Hard luck — still used on pills and share images where votes exist.
- No filter row — the feed always shows everything for the selected tab.
- My lies: unshared lies get a solid "Post to feed" button next to Share.

## Interaction rules

- Touch targets minimum 44px for primary actions; card chips/links minimum 32px.
  `aria-pressed`/`aria-expanded`/`aria-selected` maintained. `prefers-reduced-motion`
  respected — no rotation or animation when set. Focus-visible ring in `--flag`.
- Button copy: "Get the ruling" (capture), "Post to the feed" is primary on the
  result card, "Share" never "Share the slip".
- After posting, the app returns to the feed automatically.
- The capture card header carries the loop: "01 SNAP · 02 RATE · 03 SHARE"
  (snap the lie, rate the ruling, share the banter).

## Honesty guardrails (non-negotiable)

No fake counts, fake urgency, or manufactured scarcity. Seeded feed content must be real
lies, labelled as the founder's own if asked; seeded votes are for local testing and carry
`seed-` session ids so they can be stripped (`scripts/seed_votes.py --clear`). The
"MOST DISPUTED CALL" banner is computed live from real ratings only.

## Backend notes

- Adapter (`backend/adapter.py`): all model output is normalised server-side:
  ruling_type whitelisted, confidence clamped, `rule_url` must start with
  `https://www.randa.org/`.
- Votes table: `(submission_id, session_id)` unique, `stamp` enum column
  ("1".."5"; votes from earlier rating models are deleted, not faked). Rating
  requires the lie to be shared, or to belong to the rating session (result-card).
- Submissions carry optional round details: `course` (≤60 chars), `hole` (1–36),
  `played_on` (ISO date) — validated server-side; shown in the slip head as
  "HOLE 7 · WOODLANDS GC".
- Rate limits (per IP, in-memory): rulings 10/5min, code checks 10/min, votes 60/min.
- Seeding: `scripts/seed_photos.py` runs real photos through the real pipeline
  (resize/EXIF-strip like the client), spreads timestamps, optional crowd ratings
  (mostly good-call, some dispute).
