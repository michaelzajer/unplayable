"""End-to-end verification, run against a scratch SQLite DB (real data untouched).

Covers: legacy migrations (boolean votes -> six stamps -> good/bad call),
adapter output validation, gate, rate limits, upload validation, round details,
the call-rating enum, the disputed sort, the three feed views, the share page,
and feedback. Run from the project root: python3 scripts/test_backend.py
"""
import os, sqlite3, sys, uuid

DB = "/tmp/test_unplayable.db"
if os.path.exists(DB):
    os.remove(DB)

# --- 1. Build a LEGACY schema (old boolean vote model) ---
con = sqlite3.connect(DB)
con.executescript("""
CREATE TABLE submission (
  id VARCHAR PRIMARY KEY, created_at TIMESTAMP NOT NULL, image_path VARCHAR,
  user_note TEXT, situation TEXT, ruling_type VARCHAR, verdict TEXT, explanation TEXT,
  rule_number VARCHAR, rule_url TEXT, confidence FLOAT, model_used VARCHAR,
  session_id VARCHAR, shared BOOLEAN DEFAULT FALSE);
CREATE TABLE image (
  id VARCHAR PRIMARY KEY, created_at TIMESTAMP NOT NULL,
  content_type VARCHAR NOT NULL, data BLOB NOT NULL);
CREATE TABLE vote (
  id VARCHAR PRIMARY KEY, submission_id VARCHAR NOT NULL, session_id VARCHAR NOT NULL,
  value INTEGER NOT NULL, created_at TIMESTAMP NOT NULL);
INSERT INTO submission (id, created_at, verdict, ruling_type, shared, session_id)
  VALUES ('legacy1', '2026-07-01 00:00:00', 'Unplayable', 'penalty', TRUE, 'owner-sess');
INSERT INTO vote VALUES ('v1', 'legacy1', 'sessA', 1, '2026-07-01 00:00:00');
INSERT INTO vote VALUES ('v2', 'legacy1', 'sessB', -1, '2026-07-01 00:00:00');
""")
con.commit(); con.close()

os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ["ACCESS_CODE"] = "test-code"
os.environ["CANONICAL_HOST"] = "golfrules.pro"
os.environ["ANTHROPIC_API_KEY"] = "fake-key-never-used"

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import adapter, db, main  # noqa: E402  (runs init_db -> migrations)
from fastapi.testclient import TestClient  # noqa: E402

ok_count = 0
def check(name, cond):
    global ok_count
    print(("PASS " if cond else "FAIL ") + name)
    if cond: ok_count += 1
    else: sys.exit(f"FAILED: {name}")

# --- 2. Migrations: boolean -> brutal -> bad_call, all in one chain ---
con = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(vote)")]
check("migration: vote.stamp added, value dropped", "stamp" in cols and "value" not in cols)
stamps = [r[0] for r in con.execute("SELECT stamp FROM vote")]
check("migration: old-scale votes deleted (luck slider measures a new thing)", stamps == [])
subcols = [r[1] for r in con.execute("PRAGMA table_info(submission)")]
check("migration: round-detail columns added", "course" in subcols and "played_on" in subcols)
con.close()

# --- 3. Adapter validation (no API call) ---
n = adapter._normalise({"ruling_type": "DROP TABLE", "rule_url": "javascript:alert(1)",
                        "confidence": 9})
check("adapter: bad ruling_type -> unclear", n["ruling_type"] == "unclear")
check("adapter: off-domain rule_url stripped", n["rule_url"] == "")
check("adapter: confidence clamped", n["confidence"] == 1.0)
n2 = adapter._normalise({"rule_url": "https://www.randa.org/rog/rule-16"})
check("adapter: valid randa url kept", n2["rule_url"].startswith("https://www.randa.org/"))
check("adapter: prompt discourages unplayable default",
      "Do NOT default" in adapter._build_system_prompt())

# --- 4. API surface ---
client = TestClient(main.app)
H = {"X-Access-Code": "test-code"}

check("gate reports required", client.get("/api/gate").json() == {"required": True})

# Canonical host: direct run.app hits bounce to the real domain; proxied pass.
r_dir = client.get("/api/gate", headers={"host": "unplayable-x.a.run.app"},
                   follow_redirects=False)
check("canonical: direct run.app hit redirects 308",
      r_dir.status_code == 308 and r_dir.headers["location"] == "https://golfrules.pro/api/gate")
r_prox = client.get("/api/gate", headers={"host": "unplayable-x.a.run.app",
                                          "x-forwarded-host": "golfrules.pro"})
check("canonical: Firebase-proxied request passes through", r_prox.status_code == 200)

# Branded errors: pages get HTML, API paths keep JSON.
r404 = client.get("/no-such-page")
check("errors: unknown page gets branded 404 HTML",
      r404.status_code == 404 and "GolfRules" in r404.text and "rough" in r404.text)
r404api = client.get("/api/no-such-endpoint")
check("errors: unknown API path stays JSON",
      r404api.status_code == 404 and r404api.json().get("error") == "Not found.")
check("verify-code accepts right code", client.post("/api/verify-code", data={"code": "test-code"}).json()["ok"])
check("verify-code rejects wrong code", not client.post("/api/verify-code", data={"code": "nope"}).json()["ok"])
for _ in range(10):
    client.post("/api/verify-code", data={"code": "guess"})
check("verify-code rate limited", client.post("/api/verify-code", data={"code": "guess"}).status_code == 429)

check("ruling without code -> 401", client.post("/api/ruling", data={"note": "hi"}).status_code == 401)

# Fake the AI so no network call happens.
main.adapter.get_ruling = lambda img, mt, note: {
    "on_topic": True, "situation": "in a tree", "ruling_type": "penalty",
    "verdict": "UNPLAYABLE - ONE STROKE", "explanation": "Bad luck.",
    "rule_number": "19.2c", "rule_url": "https://www.randa.org/rog/rule-19",
    "confidence": 0.9, "model_used": "stub"}

r = client.post("/api/ruling", headers=H, data={
    "note": "ball in tree", "session_id": "owner-sess",
    "course": "Woodlands GC", "hole": "7", "played_on": "2026-07-05"})
check("ruling stores and returns id", r.status_code == 200 and r.json()["id"])
new_id = r.json()["id"]
con = sqlite3.connect(DB)
row = con.execute("SELECT course, hole, played_on FROM submission WHERE id=?",
                  (new_id,)).fetchone()
check("round details stored", row == ("Woodlands GC", 7, "2026-07-05"))
con.close()
bad_details = client.post("/api/ruling", headers=H, data={
    "note": "x", "session_id": "owner-sess",
    "course": "C" * 200, "hole": "99", "played_on": "not-a-date"})
con = sqlite3.connect(DB)
row = con.execute("SELECT course, hole, played_on FROM submission WHERE id=?",
                  (bad_details.json()["id"],)).fetchone()
check("bad details sanitised (course capped, hole/date dropped)",
      len(row[0]) == 60 and row[1] is None and row[2] is None)
con.close()
extra_id = bad_details.json()["id"]

bad = client.post("/api/ruling", headers=H,
                  files={"photo": ("x.jpg", b"not an image at all", "image/jpeg")})
check("upload: fake image bytes rejected (415)", bad.status_code == 415)
bad2 = client.post("/api/ruling", headers=H,
                   files={"photo": ("x.gif", b"GIF89a....", "image/gif")})
check("upload: disallowed type rejected (415)", bad2.status_code == 415)

main.adapter.get_ruling = lambda img, mt, note: {
    "on_topic": False, "situation": "", "ruling_type": "unclear", "verdict": "Not a golf lie",
    "explanation": "Golf only.", "rule_number": "", "rule_url": "", "confidence": 0.0,
    "model_used": "stub"}
jpg = b"\xff\xd8\xff" + b"\x00" * 64
r2 = client.post("/api/ruling", headers=H, files={"photo": ("x.jpg", jpg, "image/jpeg")})
check("off-topic: not stored, no id", r2.json()["stored"] is False and r2.json()["id"] is None)
con = sqlite3.connect(DB)
check("off-topic: image NOT persisted", con.execute("SELECT COUNT(*) FROM image").fetchone()[0] == 0)
con.close()

# --- 5. Luck ratings ---
check("rate: old stamp values rejected", client.post("/api/vote", headers=H,
      data={"submission_id": "legacy1", "stamp": "brutal", "session_id": "s1"}).status_code == 400)
check("rate: out-of-range value rejected", client.post("/api/vote", headers=H,
      data={"submission_id": "legacy1", "stamp": "6", "session_id": "s1"}).status_code == 400)
check("rate: unshared submission rejected", client.post("/api/vote", headers=H,
      data={"submission_id": new_id, "stamp": "4", "session_id": "s1"}).status_code == 400)
check("rate: unknown id rejected", client.post("/api/vote", headers=H,
      data={"submission_id": "nope", "stamp": "4", "session_id": "s1"}).status_code == 400)

# Owner may rate their own lie before it is shared (result-page rating).
check("rate: owner can rate own unshared lie", client.post("/api/vote", headers=H,
      data={"submission_id": new_id, "stamp": "1", "session_id": "owner-sess"}).status_code == 200)
client.post("/api/vote", headers=H,
            data={"submission_id": new_id, "stamp": "clear_luck", "session_id": "owner-sess"})

client.post("/api/post-to-feed", data={"submission_id": new_id}, headers=H)
check("rate: valid rating on shared post", client.post("/api/vote", headers=H,
      data={"submission_id": new_id, "stamp": "4", "session_id": "s1"}).status_code == 200)
client.post("/api/vote", headers=H, data={"submission_id": new_id, "stamp": "2", "session_id": "s1"})

feed = client.get("/api/feed", params={"session_id": "s1"}).json()
item = next(i for i in feed if i["id"] == new_id)
check("rate: re-rate replaces (one per golfer)",
      item["stamp_counts"]["2"] == 1 and item["stamp_counts"]["4"] == 0)
check("feed: my_luck reflects caller's rating", item["my_luck"] == "2")

# The call rating coexists with the luck rating for the same golfer.
check("rate: call rating accepted alongside luck", client.post("/api/vote", headers=H,
      data={"submission_id": new_id, "stamp": "good_call", "session_id": "s1"}).status_code == 200)
feed = client.get("/api/feed", params={"session_id": "s1"}).json()
item = next(i for i in feed if i["id"] == new_id)
check("rate: both kinds held at once",
      item["my_luck"] == "2" and item["my_call"] == "good_call"
      and item["stamp_counts"]["2"] == 1 and item["stamp_counts"]["good_call"] == 1)

client.post("/api/vote", headers=H, data={"submission_id": new_id, "stamp": "clear", "session_id": "s1"})
feed = client.get("/api/feed", params={"session_id": "s1"}).json()
item = next(i for i in feed if i["id"] == new_id)
check("rate: clear removes the call, keeps the luck",
      item["my_call"] is None and item["my_luck"] == "2")
client.post("/api/vote", headers=H, data={"submission_id": new_id, "stamp": "clear_luck", "session_id": "s1"})
feed = client.get("/api/feed", params={"session_id": "s1"}).json()
item = next(i for i in feed if i["id"] == new_id)
check("rate: clear_luck removes the luck rating",
      item["my_luck"] is None and item["vote_count"] == 0)

# --- Share page shows the crowd's luck rating ---
client.post("/api/vote", headers=H, data={"submission_id": "legacy1", "stamp": "5", "session_id": "rA"})
client.post("/api/vote", headers=H, data={"submission_id": "legacy1", "stamp": "4", "session_id": "rB"})
client.post("/api/vote", headers=H, data={"submission_id": "legacy1", "stamp": "good_call", "session_id": "rA"})
share_html = client.get("/r/legacy1").text  # avg 4.5 of 2 luck ratings + 1 call
check("share page: luck pill present", "Hard luck · 4.5/5 · ×2" in share_html)
check("share page: call pill present", "Good call ×1" in share_html)
check("share page: rating in preview description", "Rated HARD LUCK (4.5/5) by 2 golfers" in share_html)
share_none = client.get(f"/r/{new_id}").text  # no ratings at this point
check("share page: honest empty state", "NOT RATED YET" in share_none)

# --- Feedback ---
check("feedback: empty message rejected", client.post("/api/feedback",
      data={"message": "  "}).status_code == 400)
check("feedback: stored ungated", client.post("/api/feedback",
      data={"message": "Said free relief, was a penalty area.",
            "submission_id": new_id, "contact": "m@example.com"}).status_code == 200)
con = sqlite3.connect(DB)
fb = con.execute("SELECT message, submission_id, contact FROM feedback").fetchone()
check("feedback: row correct", fb[1] == new_id and "penalty area" in fb[0])
fake_ref = client.post("/api/feedback", data={"message": "x", "submission_id": "not-real"})
fb2 = con.execute("SELECT submission_id FROM feedback ORDER BY created_at DESC LIMIT 1").fetchone()
check("feedback: bogus ruling ref dropped", fake_ref.status_code == 200 and fb2[0] is None)
con.close()

# --- 6. Hardest-luck sort ---
# legacy1 has 5+4 (weight 7). Give new_id 3x "1" (weight 0).
for s in ("sA", "sB", "sC"):
    client.post("/api/vote", headers=H, data={"submission_id": new_id, "stamp": "1", "session_id": s})
worst = client.get("/api/feed", params={"sort": "worst"}).json()
check("hardest-luck sort: roughest lies first", worst[0]["id"] == "legacy1")
latest = client.get("/api/feed").json()
check("latest sort: newest first", latest[0]["id"] == new_id)
mine = client.get("/api/feed", params={"mine": 1, "session_id": "owner-sess"}).json()
check("my lies: filtered by session", {i["id"] for i in mine} == {"legacy1", new_id, extra_id})
check("my lies: empty without session", client.get("/api/feed", params={"mine": 1}).json() == [])

print(f"\nAll {ok_count} checks passed.")
