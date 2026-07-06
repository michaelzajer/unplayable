"""End-to-end verification of the stamp-vote changes, run against a scratch SQLite DB.

Covers: legacy-schema migration, adapter output validation, gate, rate limits,
upload validation, vote enum + clear, weighted sort, and the three feed views.
"""
import os, sqlite3, sys, uuid

DB = "/tmp/test_unplayable.db"
if os.path.exists(DB):
    os.remove(DB)

# --- 1. Build a LEGACY schema first (old boolean vote model, no suggested_stamp) ---
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
os.environ["ANTHROPIC_API_KEY"] = "fake-key-never-used"

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import adapter, db, main  # noqa: E402  (runs init_db -> migration)
from fastapi.testclient import TestClient  # noqa: E402

ok_count = 0
def check(name, cond):
    global ok_count
    print(("PASS " if cond else "FAIL ") + name)
    if cond: ok_count += 1
    else: sys.exit(f"FAILED: {name}")

# --- 2. Migration ---
con = sqlite3.connect(DB)
cols = [r[1] for r in con.execute("PRAGMA table_info(vote)")]
check("migration: vote.stamp added, value dropped", "stamp" in cols and "value" not in cols)
stamps = [r[0] for r in con.execute("SELECT stamp FROM vote")]
check("migration: legacy votes mapped to brutal", stamps == ["brutal", "brutal"])
subcols = [r[1] for r in con.execute("PRAGMA table_info(submission)")]
check("migration: submission.suggested_stamp added", "suggested_stamp" in subcols)
con.close()

# --- 3. Adapter validation (no API call) ---
n = adapter._normalise({"ruling_type": "DROP TABLE", "rule_url": "javascript:alert(1)",
                        "suggested_stamp": "seventh_thing", "confidence": 9})
check("adapter: bad ruling_type -> unclear", n["ruling_type"] == "unclear")
check("adapter: off-domain rule_url stripped", n["rule_url"] == "")
check("adapter: invalid stamp -> fair_cop", n["suggested_stamp"] == "fair_cop")
check("adapter: confidence clamped", n["confidence"] == 1.0)
n2 = adapter._normalise({"rule_url": "https://www.randa.org/rog/rule-16", "suggested_stamp": "brutal"})
check("adapter: valid randa url kept", n2["rule_url"].startswith("https://www.randa.org/"))
check("adapter: valid stamp kept", n2["suggested_stamp"] == "brutal")

# --- 4. API surface ---
client = TestClient(main.app)
H = {"X-Access-Code": "test-code"}

check("gate reports required", client.get("/api/gate").json() == {"required": True})
check("verify-code accepts right code", client.post("/api/verify-code", data={"code": "test-code"}).json()["ok"])
check("verify-code rejects wrong code", not client.post("/api/verify-code", data={"code": "nope"}).json()["ok"])
for _ in range(10):
    client.post("/api/verify-code", data={"code": "guess"})
check("verify-code rate limited", client.post("/api/verify-code", data={"code": "guess"}).status_code == 429)

check("ruling without code -> 401", client.post("/api/ruling", data={"note": "hi"}).status_code == 401)

# Fake the AI so no network call happens.
main.adapter.get_ruling = lambda img, mt, note: {
    "on_topic": True, "situation": "in a tree", "ruling_type": "penalty",
    "verdict": "Unplayable", "explanation": "Bad luck.", "rule_number": "19.2c",
    "rule_url": "https://www.randa.org/rog/rule-19", "confidence": 0.9,
    "suggested_stamp": "brutal", "model_used": "stub"}

r = client.post("/api/ruling", headers=H, data={
    "note": "ball in tree", "session_id": "owner-sess",
    "course": "Woodlands GC", "hole": "7", "played_on": "2026-07-05"})
check("ruling stores and returns id", r.status_code == 200 and r.json()["id"])
con = sqlite3.connect(DB)
row = con.execute("SELECT course, hole, played_on FROM submission WHERE id=?",
                  (r.json()["id"],)).fetchone()
check("round details stored", row == ("Woodlands GC", 7, "2026-07-05"))
con.close()
bad_id_holder = {}
bad_details = client.post("/api/ruling", headers=H, data={
    "note": "x", "session_id": "owner-sess",
    "course": "C" * 200, "hole": "99", "played_on": "not-a-date"})
con = sqlite3.connect(DB)
row = con.execute("SELECT course, hole, played_on FROM submission WHERE id=?",
                  (bad_details.json()["id"],)).fetchone()
check("bad details sanitised (course capped, hole/date dropped)",
      len(row[0]) == 60 and row[1] is None and row[2] is None)
con.close()
bad_id_holder["id"] = bad_details.json()["id"]
check("ruling carries suggested_stamp", r.json()["suggested_stamp"] == "brutal")
new_id = r.json()["id"]

bad = client.post("/api/ruling", headers=H,
                  files={"photo": ("x.jpg", b"not an image at all", "image/jpeg")})
check("upload: fake image bytes rejected (415)", bad.status_code == 415)
bad2 = client.post("/api/ruling", headers=H,
                   files={"photo": ("x.gif", b"GIF89a....", "image/gif")})
check("upload: disallowed type rejected (415)", bad2.status_code == 415)

main.adapter.get_ruling = lambda img, mt, note: {
    "on_topic": False, "situation": "", "ruling_type": "unclear", "verdict": "Not a golf lie",
    "explanation": "Golf only.", "rule_number": "", "rule_url": "", "confidence": 0.0,
    "suggested_stamp": "fair_cop", "model_used": "stub"}
jpg = b"\xff\xd8\xff" + b"\x00" * 64
r2 = client.post("/api/ruling", headers=H, files={"photo": ("x.jpg", jpg, "image/jpeg")})
check("off-topic: not stored, no id", r2.json()["stored"] is False and r2.json()["id"] is None)
con = sqlite3.connect(DB)
check("off-topic: image NOT persisted", con.execute("SELECT COUNT(*) FROM image").fetchone()[0] == 0)
con.close()

# --- 5. Votes ---
check("vote: invalid stamp rejected", client.post("/api/vote", headers=H,
      data={"submission_id": "legacy1", "stamp": "seventh", "session_id": "s1"}).status_code == 400)
check("vote: unshared submission rejected", client.post("/api/vote", headers=H,
      data={"submission_id": new_id, "stamp": "brutal", "session_id": "s1"}).status_code == 400)
check("vote: unknown id rejected", client.post("/api/vote", headers=H,
      data={"submission_id": "nope", "stamp": "brutal", "session_id": "s1"}).status_code == 400)

# Owner may stamp their own lie before it is shared (result-page stamping).
check("vote: owner can stamp own unshared lie", client.post("/api/vote", headers=H,
      data={"submission_id": new_id, "stamp": "gift", "session_id": "owner-sess"}).status_code == 200)
client.post("/api/vote", headers=H,
            data={"submission_id": new_id, "stamp": "clear", "session_id": "owner-sess"})

client.post("/api/post-to-feed", data={"submission_id": new_id}, headers=H)
check("vote: valid stamp on shared post", client.post("/api/vote", headers=H,
      data={"submission_id": new_id, "stamp": "cooked", "session_id": "s1"}).status_code == 200)
client.post("/api/vote", headers=H, data={"submission_id": new_id, "stamp": "stiff", "session_id": "s1"})

feed = client.get("/api/feed", params={"session_id": "s1"}).json()
item = next(i for i in feed if i["id"] == new_id)
check("vote: re-stamp replaces (one per golfer)",
      item["stamp_counts"]["stiff"] == 1 and item["stamp_counts"]["cooked"] == 0)
check("feed: my_stamp reflects caller's vote", item["my_stamp"] == "stiff")
check("feed: suggested_stamp present", item["suggested_stamp"] == "brutal")

client.post("/api/vote", headers=H, data={"submission_id": new_id, "stamp": "clear", "session_id": "s1"})
feed = client.get("/api/feed", params={"session_id": "s1"}).json()
item = next(i for i in feed if i["id"] == new_id)
check("vote: clear removes stamp", item["my_stamp"] is None and item["vote_count"] == 0)

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

# --- 6. Weighted sort ---
# legacy1 has 2x brutal (weight 6). Give new_id 3x stiff (weight 3): worst puts legacy1 first.
for s in ("sA", "sB", "sC"):
    client.post("/api/vote", headers=H, data={"submission_id": new_id, "stamp": "stiff", "session_id": s})
worst = client.get("/api/feed", params={"sort": "worst"}).json()
check("worst sort: weighted (2xbrutal=6 beats 3xstiff=3)", worst[0]["id"] == "legacy1")
latest = client.get("/api/feed").json()
check("latest sort: newest first", latest[0]["id"] == new_id)
mine = client.get("/api/feed", params={"mine": 1, "session_id": "owner-sess"}).json()
check("my lies: filtered by session",
      {i["id"] for i in mine} == {"legacy1", new_id, bad_id_holder["id"]})
check("my lies: empty without session", client.get("/api/feed", params={"mine": 1}).json() == [])

print(f"\nAll {ok_count} checks passed.")
