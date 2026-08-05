# GolfRules.pro — roadmap

Agreed-but-not-built ideas, in rough priority order. Guiding principle: **grow the
audience first, monetise second.** Keep everything aligned with the honest, clean,
no-dark-patterns brand. Nothing here is live yet.

## 1. Growth features (do these first)

### Share the URL, not the PNG
Feed/result "Share" currently shares a static branded PNG to WhatsApp — good in a
group chat but a viral dead end, because there is no way back to the site.
- Change it to share the `/r/{id}` link via the Web Share API, so every share is a
  growth loop: a mate sees it, taps through, snaps their own lie, shares again.
- The `/r/` pages already carry Open Graph tags with the lie photo as `og:image`,
  so WhatsApp/iMessage/social unfurl a rich preview — the visual appeal of the
  image AND a clickable link back.
- Desktop fallback: copy link to clipboard.
- Later refinement: make the `/r/` `og:image` the branded verdict card rather than
  the raw photo, so the unfurl looks like the app.
- Effort: ~1 hour. Highest-leverage, lowest-cost growth change.
- Paired with descriptive slug URLs (below).

### Descriptive slug URLs for share pages
`/r/{uuid}` is opaque — the raw UUID reads like spam in a shared link and does
nothing for SEO. Move to `/r/{slug}-{shortid}`, e.g.
`/r/ball-against-a-tree-root-a2691f`, slug derived from the lie's `situation`.
Puts the words people search into the URL and makes shared links trustworthy and
clickable. Keep old `/r/{uuid}` working via a 301 to the canonical slug so no
existing link breaks. Built together with share-the-URL.

### GPS location → course auto-match
Auto-fill the course a lie was taken on (currently typed by hand).
- Use the browser **Geolocation API at capture** — NOT EXIF (browsers strip it,
  and we strip it too for privacy). Phone GPS outdoors is ~10 m, plenty to know
  which course.
- Reverse-match lat/long to a course: **OpenStreetMap golf-course polygons (free)**
  first; **Google Places** is cleaner but paid.
- Opt-in (the "GPS toggle"); store the matched **course name** (pre-fill the
  existing course field), not raw coordinates. Fits the privacy stance.
- Unlocks per-course feeds/leaderboards ("hardest luck at [course] this month")
  and a **course-page SEO long-tail** (a page per course accumulating real lies).
- Effort: ~1–2 days. Strong post-launch engagement feature.

### Evergreen SEO explainer pages
A handful of hand-written pages for the highest-volume rules scenarios (unplayable,
free relief from cart paths/sprinklers/casual water/GUR, penalty-area drops). Each
ranks for its query and funnels to the app. Do AFTER the `/r/` share pages prove
the long-tail pattern.

## 2. Infrastructure

### Phase 2 — retire Neon → Firestore
Phase 1 (photos → Firebase Storage) is done, which took the scaling load off the
DB. Phase 2 moves the structured data (submissions, votes, feedback) from Neon to
Firestore to fully retire Neon and go all-Firebase.
- Big job: `db.py` rewrite (NoSQL), vote tallies/hardest-luck sort become counter
  fields updated on each vote (honesty-critical), test suite + local dev rework
  (Firestore emulator), Firestore security rules, data migration.
- Honest value: mostly consolidation (one fewer vendor), since Neon holding tiny
  text rows already scales fine. Do it when "everything in the Firebase console"
  is worth the effort, not for a functional gain.

### Purge old image BLOBs from Neon
After confirming photos load from Storage on the live site, delete the old `image`
rows the migration left in place for rollback, to reclaim the space.

## 3. Monetisation (only once real traffic exists)

Golfers are a premium, advertiser-friendly audience (affluent; spend on gear,
travel, memberships) — an asset whichever route. But do NOT bolt this on before
there is an audience; it only taxes the clean UX for pennies. Ranked best-fit
first:

1. **Affiliate links** to golf gear/retailers — natural (a golfer looking at a
   ruling is thinking about their game), non-intrusive, scales with traffic, zero
   running cost.
2. **A single relevant sponsor** ("rulings brought to you by …") rather than a
   programmatic ad network — reads as partnership, better rates, no third-party
   tracking. Needs some sales effort and an audience worth a brand's while.
3. **Light premium tier** — keep core rulings free; charge a small annual fee for
   power features (course auto-match, saved round history, ad-free, club/group
   features). Golfers already pay for apps (GHIN, Arccos).
4. **B2B licensing (underrated, best fit for Michael's skills)** — clubs, societies
   running competitions, corporate/charity golf days. Branded or white-labelled
   per-club version, or a sponsored event instance. Highest value per customer and
   most defensible; plays to consulting/relationships strengths.

**Rank lower:** display/AdSense banners — work, and golf rates beat average, but
low-yield until real scale, most intrusive on a mobile app used pitch-side, and
bring the tracking that runs against the brand. If used, keep them in the feed
(browsing), never in the ruling flow, and be transparent.

**Avoid:** selling user data or insights — the obvious temptation with a
data-generating app, and precisely what the brand and Michael's stated values are
built against. The clean, honest positioning is a differentiator worth protecting.

**Sequence:** grow the audience → affiliate + a relevant sponsor for the consumer
side → light premium once the feature set justifies it → seriously explore club/
event licensing as the higher-value play.

## 4. Quality

### Gemini prompt-tuning pass
Now on `gemini-3.5-flash` (swapped from Claude). Rulings are "generally good" but
Gemini does not rule identically to Claude. Run `scripts/review_rulings.py` /
`test_harness.py` over the seed photos, grade, and tune `backend/adapter.py` +
`rules/rules-reference.md` until the hit rate is solid. Watch for the unplayable
bias creeping back with the new model.
