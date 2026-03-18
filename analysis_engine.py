import json
import os
from typing import Any, Dict, Optional


def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def extract_structured_response(oai_client, transcript_text: str) -> Optional[Dict[str, Any]]:
    if not oai_client or not (transcript_text or "").strip():
        return None

    model = os.environ.get("OPENAI_ANALYSIS_MODEL", "gpt-4o-mini")

    system = (
        "You are a strict JSON generator. "
        "Given a phone conversation transcript between an AI assistant and a customer, extract:\n"
        "- visit_intent: one of yes, no, maybe, undecided, voicemail, or unknown\n"
        "- visit_date: the specific date/day the customer mentioned (empty string if none)\n"
        "- next_action: one of site_visit, photo_update, callback, reschedule, no_action, or unknown\n"
        "- notes: one-sentence summary of what the customer said\n"
        "Return ONLY valid JSON with keys: visit_intent, visit_date, next_action, notes."
    )

    user = "Transcript:\n" + transcript_text + "\n\nReturn the JSON now."

    content = ""
    try:
        resp = oai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
    except Exception:
        try:
            resp = oai_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.1,
                max_tokens=200,
            )
            content = resp.choices[0].message.content or ""
        except Exception:
            return None

    parsed = _safe_json_loads(content.strip())
    if not parsed:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            parsed = _safe_json_loads(content[start : end + 1])
    if not parsed:
        return None

    return {
        "visit_intent": str(parsed.get("visit_intent", "unknown") or "unknown").strip().lower(),
        "visit_date": str(parsed.get("visit_date", "") or "").strip(),
        "next_action": str(parsed.get("next_action", "unknown") or "unknown").strip().lower(),
        "notes": str(parsed.get("notes", "") or "").strip(),
    }
