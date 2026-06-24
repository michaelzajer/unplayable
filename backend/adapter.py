"""
AI adapter for Unplayable.

This is the only file that knows which AI provider you are using. The rest of the
app calls get_ruling() and gets back a plain dict. To benchmark a different model,
change ANTHROPIC_MODEL in .env. To try a different provider entirely (e.g. a local
Ollama vision model), reimplement get_ruling() here and leave everything else alone.
"""

import json
import os
from pathlib import Path

import anthropic

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
RULES_PATH = Path(__file__).resolve().parent.parent / "rules" / "rules-reference.md"

_client = None


def _get_client():
    # Lazy so the module imports fine without a key (useful for tests and boot checks).
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    return _client


def _load_rules() -> str:
    try:
        return RULES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "(No rules reference file found.)"


SCHEMA = """{
  "situation": "one short sentence describing the lie",
  "ruling_type": "one of: free_relief | penalty | play_as_it_lies | unclear",
  "verdict": "the headline ruling in a few words",
  "explanation": "one or two cheeky sentences a golfer would enjoy",
  "rule_number": "the exact rule, e.g. 16.3",
  "rule_url": "the official R&A URL for that rule, copied from the reference",
  "confidence": 0.0
}"""


def _build_system_prompt() -> str:
    return f"""You are the rules assistant for Unplayable, a light-hearted golf app for \
club golfers settling on-course arguments. You are given a photo of a ball's lie and an \
optional note from the player. Work out the situation and give a ruling.

Return ONLY a JSON object matching this schema. No prose, no markdown, nothing outside the JSON:
{SCHEMA}

Rules for your behaviour:
- Use ONLY the Rules of Golf reference below. Never invent rule numbers, rule text, or URLs.
- Copy rule_url verbatim from the reference for the rule you cite.
- Keep explanation light and a little cheeky, but never wrong on the ruling itself.
- ruling_type must reflect the outcome: free_relief (relief with no penalty), penalty \
(relief or a stroke that costs a shot), play_as_it_lies (no relief available), or unclear.
- If the photo and note do not give you enough to rule confidently, set confidence below 0.5, \
set ruling_type to "unclear", and put a single specific clarifying question in explanation.
- This is a banter tool, not a match committee. When genuinely ambiguous, say so rather than guessing.

RULES OF GOLF REFERENCE
=======================
{_load_rules()}
"""


def _normalise(data: dict) -> dict:
    return {
        "situation": str(data.get("situation", "")).strip(),
        "ruling_type": str(data.get("ruling_type", "unclear")).strip().lower(),
        "verdict": str(data.get("verdict", "")).strip(),
        "explanation": str(data.get("explanation", "")).strip(),
        "rule_number": str(data.get("rule_number", "")).strip(),
        "rule_url": str(data.get("rule_url", "")).strip(),
        "confidence": float(data.get("confidence", 0.0) or 0.0),
    }


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    # Strip a leading ```json / ``` fence if the model added one.
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    # Slice to the outermost object as a last resort.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return _normalise(json.loads(text))


def _fallback(reason: str) -> dict:
    return {
        "situation": "",
        "ruling_type": "unclear",
        "verdict": "Could not read that one",
        "explanation": "The ruling engine could not work that out. Try a clearer photo, "
        "or describe the lie in a sentence.",
        "rule_number": "",
        "rule_url": "",
        "confidence": 0.0,
        "model_used": MODEL,
        "error": reason,
    }


def get_ruling(image_b64: str | None, media_type: str, note: str) -> dict:
    """Return a ruling dict. image_b64 may be None for text-only (fallback) requests."""
    content = []
    if image_b64:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": image_b64},
            }
        )
    user_text = "Here is my ball's lie." if image_b64 else "No photo — here is my description."
    if note:
        user_text += f' Player note: "{note}".'
    user_text += " Give your ruling as JSON only."
    content.append({"type": "text", "text": user_text})

    try:
        msg = _get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_build_system_prompt(),
            messages=[{"role": "user", "content": content}],
        )
        raw = "".join(b.text for b in msg.content if b.type == "text")
        data = _parse_json(raw)
        data["model_used"] = MODEL
        return data
    except Exception as exc:  # noqa: BLE001 - alpha: surface any failure as a graceful fallback
        return _fallback(str(exc))
