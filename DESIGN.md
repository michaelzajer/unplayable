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

## Rating: luck slider + call thumbs

TWO independent ratings per golfer per lie:
- LUCK (the lie): 5-notch slider directly UNDER the photo, above the commentary.
  Green "Good lie" -> amber -> red "Hard luck". Thumb hollow until rated, then
  solid; crowd line between the labels reads "CROWD 3.8/5 · ×12" / "SLIDE TO RATE".
  Re-sliding replaces; no un-rate. Stored as "1".."5".
- CALL (the ruling): small "Good call" (green, cup icon) / "Bad call" (red, splash
  icon) buttons in the bottom row next to Post-to-feed/Share. Tap to rate, tap
  again to clear, switching replaces. Stored as good_call/bad_call.

Both appear on feed cards and the result card. Vote rows share one table; one row
per kind per (submission, session), enforced by same-kind replacement.

- Average luck buckets: <1.8 Good lie, <2.6 Not bad, <3.4 Fair, <4.2 Rough, else
  Hard luck (green/green/amber/red/red ink) — used on pills and the share image.
- "The Feed" tab sorts hardest luck first (weight = notch - 1; call votes weigh 0);
  the top lie wears a red "HARDEST LUCK GOING ROUND" banner.
- Filter row: All / Good lies (avg <= 2.5) / Hard luck (avg >= 3.5).
- Share image: golfer's own luck rating as corner stamp; crowd average as a small
  ink stamp + "4.2/5 · CROWD ×12". Share page: luck pill + call pills.
- My lies: unshared lies get a solid "Post to feed" button next to Share.

## Interaction rules

- Touch targets minimum 44px. `aria-pressed`/`aria-expanded`/`aria-selected` maintained;
  the slider exposes "Rate the lie: 1 good lie to 5 hard luck". `prefers-reduced-motion` respected —
  no rotation or animation when set. Focus-visible ring in `--flag`.
- Button copy: "Get the ruling" (capture), "Post to the feed" above "Share" on the
  result card (post is primary), "Share" never "Share the slip".
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
