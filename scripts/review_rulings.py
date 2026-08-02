"""
Visual ruling review — see each photo next to the ruling it produces.

Runs every photo in scripts/test_photos/ through the adapter and writes a
self-contained HTML page (images embedded) styled like the app, so you can eyeball
the photo, the verdict, and the cheeky message exactly as a golfer would online.

    python scripts/review_rulings.py
    open scripts/rulings_review.html      # macOS

Add --note "..." to send the same note with every photo.
"""

import base64
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

from backend import adapter  # noqa: E402

PHOTO_DIR = ROOT / "scripts" / "test_photos"
OUT_HTML = ROOT / "scripts" / "rulings_review.html"
EXTS = {".jpg", ".jpeg", ".png"}

# Colour + label per ruling type, mirrored from the app.
STAKE = {
    "free_relief": ("#0FA958", "Free relief"),
    "penalty": ("#C8102E", "Penalty"),
    "play_as_it_lies": ("#1B2420", "Play it as it lies"),
    "unclear": ("#F5A623", "Ruling"),
}


def media_type_for(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def esc(v) -> str:
    return html.escape(str(v or ""), quote=True)


def card(path: Path, r: dict) -> str:
    mt = media_type_for(path)
    b64 = base64.b64encode(path.read_bytes()).decode()
    colour, label = STAKE.get(r.get("ruling_type") or "unclear", STAKE["unclear"])
    rule = ""
    if r.get("rule_number") and r.get("rule_url"):
        rule = (f'<a href="{esc(r["rule_url"])}" target="_blank" rel="noopener" '
                f'class="rule">Read Rule {esc(r["rule_number"])} on randa.org &nearr;</a>')
    err = f'<p class="err">ERROR: {esc(r["error"])}</p>' if r.get("error") else ""
    conf = r.get("confidence")
    return f"""
    <figure class="card">
      <img src="data:{mt};base64,{b64}" alt="lie" />
      <figcaption>
        <div class="label" style="color:{colour}">{esc(label)}</div>
        <div class="stamp" style="color:{colour}">{esc(r.get('verdict'))}</div>
        <p class="situation">{esc(r.get('situation'))}</p>
        <p class="explain">{esc(r.get('explanation'))}</p>
        {rule}
        <div class="meta">confidence {esc(conf)} &middot; {esc(r.get('model_used'))} &middot; {esc(path.name)}</div>
        {err}
      </figcaption>
    </figure>"""


def main() -> None:
    note = ""
    if "--note" in sys.argv:
        i = sys.argv.index("--note")
        note = sys.argv[i + 1] if i + 1 < len(sys.argv) else ""

    photos = sorted(p for p in PHOTO_DIR.glob("*") if p.suffix.lower() in EXTS)
    if not photos:
        print(f"No photos in {PHOTO_DIR}. Run: python scripts/export_photos.py --force")
        return

    print(f"Running {len(photos)} photo(s) through {adapter.MODEL}...")
    cards = []
    for i, path in enumerate(photos, 1):
        b64 = base64.b64encode(path.read_bytes()).decode()
        r = adapter.get_ruling(b64, media_type_for(path), note=note)
        print(f"  [{i}/{len(photos)}] {path.name}: {r.get('verdict')}")
        cards.append(card(path, r))

    page = f"""<!DOCTYPE html><html lang="en-AU"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Ruling review — GolfRules.pro</title>
<style>
  body{{margin:0;background:#F4F8FB;color:#1B2420;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;}}
  header{{background:#1B2D4F;color:#fff;padding:1rem 1.5rem;font-weight:700;font-size:1.2rem;}}
  header i{{color:#EF4444;font-style:normal;}} header u{{color:#C8D3E6;text-decoration:none;}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1.25rem;padding:1.25rem;max-width:1200px;margin:0 auto;}}
  .card{{margin:0;background:#fff;border:1px solid #DFE6EB;border-radius:14px;overflow:hidden;box-shadow:0 6px 18px -12px rgba(27,45,79,.4);}}
  .card img{{width:100%;height:220px;object-fit:cover;display:block;background:#1B2D4F;}}
  figcaption{{padding:.9rem 1rem 1.1rem;}}
  .label{{font-size:.7rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;}}
  .stamp{{font-weight:800;letter-spacing:.04em;text-transform:uppercase;font-size:1.15rem;margin-top:.15rem;}}
  .situation{{color:#65706A;font-size:.85rem;margin:.5rem 0 .1rem;}}
  .explain{{font-size:.95rem;line-height:1.4;margin:.4rem 0;}}
  .rule{{color:#0FA958;font-weight:700;font-size:.85rem;text-decoration:none;}}
  .meta{{color:#8892A0;font-size:.72rem;margin-top:.6rem;font-family:ui-monospace,monospace;}}
  .err{{color:#C8102E;font-size:.8rem;margin-top:.5rem;font-family:ui-monospace,monospace;}}
</style></head><body>
<header>Golf<i>Rules</i><u>.pro</u> &nbsp;&mdash;&nbsp; ruling review ({len(photos)} lies, {esc(adapter.MODEL)})</header>
<div class="grid">{''.join(cards)}</div>
</body></html>"""
    OUT_HTML.write_text(page, encoding="utf-8")
    print(f"\nWrote {OUT_HTML}\nOpen it:  open {OUT_HTML}")


if __name__ == "__main__":
    main()
