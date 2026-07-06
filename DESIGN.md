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
- `--ink-green` #0FA958 — good outcomes (Gift, Fluke stamps; free-relief verdicts; links)
- `--ink-amber` #C28400 — middling outcomes (Fair cop, Stiff stamps); darker ochre, never
  bright yellow — yellow text on white cards is unreadable
- `--ink-red` #E03131 — bad outcomes (Cooked, Brutal stamps; penalty verdicts)
- `--flag` #F5A623 — focus rings and the "unclear" ruling flag only
- `--rule` #DFE6EB — borders and ruling lines

Colour semantics are strict: blue means "the app and its actions"; the green→amber→red
traffic light means "how lucky the golf was". Never mix the two jobs.

Type: Oswald (condensed caps: verdicts, stamps, headers, primary buttons), Karla (body),
IBM Plex Mono (rule numbers, tallies, metadata). Google Fonts CDN. Stack stays Tailwind
CDN + vanilla JS, no build step.

## Layout: feed-first

The home page IS the feed (Snap Send Solve pattern). Sticky blue header with tabs
Latest / Worst lies / My lies. Below: a stamp filter row, the worst-lie hero, then the
slips. A floating round camera button ("Snap") sits bottom-centre, cradled in a slim
bottom bar (Feed · Snap · About). Tapping it opens the capture view: two large square
buttons (Take photo / Choose photo), note field, optional round details (course, hole
stepper that wraps 18↔1, date), then "Get the ruling". `/feed` serves the same page.

## Signature element: the verdict stamp

Rotated rubber-stamp treatment (`.stamp`): 3px border, slight rotation, ink-fade mask.
Used for AI verdicts over the photo (top-left on feed cards), on the result card, the
share page, and drawn onto the share image (with a second low-alpha stroke pass for ink
bleed). Verdicts are stamp-length by prompt: 2–4 words, e.g. "UNPLAYABLE — ONE STROKE".
Green ink favourable, red unfavourable, pencil/flag for neutral/unclear.

## Voting: the six stamps

One stamp per golfer per lie (reaction model). Fixed enum, lucky to cursed:
`gift, fluke, fair_cop, stiff, cooked, brutal`

- Traffic light: gift, fluke → green; fair_cop, stiff → amber; cooked, brutal → red.
- Each has a small line icon: gift box, horseshoe, scales, ball-under-lip, frying pan, skull.
- On feed cards the six render as a fixed 6-column grid of compact stacked chips
  (icon + count above, label below) overlaid on the photo bottom — all visible at once,
  no horizontal scrolling, ≥44px targets.
- The AI suggests one stamp per post (`suggested_stamp`, constrained to the enum,
  fallback `fair_cop`). Until a human stamps, the suggested chip is dashed with a "?";
  the hint line reads "Rate the ruling · dashed = the app's pick". Suggestions never
  count in tallies until a human confirms.
- Tap to stamp (solid ink fill + thunk), tap your own chip to clear, re-stamp replaces.
  Optimistic update, rollback on API failure.
- Hint line names the leader: "BRUTAL leads ×48 of 60".
- The golfer can also stamp from the result card (dashed suggestion + ⋯ tray); owners
  may stamp their own lie before it is shared. Only a confirmed stamp is drawn on the
  share image — never the app's unconfirmed suggestion.
- "Worst lies" sort is weighted: brutal 3, cooked 2, stiff 1, others 0.
- Feed filter row (All + six stamps, 7-column grid): filters to lies where that stamp
  LEADS the count; tap the active filter again to clear.

## Interaction rules

- Touch targets minimum 44px. `aria-pressed`/`aria-expanded`/`aria-selected` maintained;
  chips expose "Brutal, 48 of 60 stamps" labels. `prefers-reduced-motion` respected —
  no rotation or animation when set. Focus-visible ring in `--flag`.
- Button copy: "Get the ruling" (capture), "Post to the feed" above "Share" on the
  result card (post is primary), "Share" never "Share the slip".
- After posting, the app returns to the feed automatically.
- The capture card header carries the loop: "01 SNAP · 02 RATE · 03 SHARE"
  (snap the lie, rate the ruling, share the banter).

## Honesty guardrails (non-negotiable)

No fake counts, fake urgency, or manufactured scarcity. Seeded feed content must be real
lies, labelled as the founder's own if asked; seeded votes are for local testing and carry
`seed-` session ids so they can be stripped (`scripts/seed_votes.py --clear`). AI
suggestions are always visually provisional until confirmed. The worst-lie hero renders
only when a real post has real stamps, and only claims "yesterday's" when the post is
under 48 hours old.

## Backend notes

- Adapter (`backend/adapter.py`) returns `suggested_stamp` in structured output, validated
  server-side against the enum (fallback `fair_cop`). All model output is normalised:
  ruling_type whitelisted, confidence clamped, `rule_url` must start with
  `https://www.randa.org/`.
- Votes table: `(submission_id, session_id)` unique, `stamp` enum column. Legacy boolean
  votes migrated to `brutal`. Voting requires the lie to be shared, or to belong to the
  voting session (result-card stamping).
- Submissions carry optional round details: `course` (≤60 chars), `hole` (1–36),
  `played_on` (ISO date) — validated server-side; shown in the slip head as
  "HOLE 7 · WOODLANDS GC".
- Rate limits (per IP, in-memory): rulings 10/5min, code checks 10/min, votes 60/min.
- Seeding: `scripts/seed_photos.py` runs real photos through the real pipeline
  (resize/EXIF-strip like the client), spreads timestamps, optional crowd stamps
  clustered on the AI's read.
