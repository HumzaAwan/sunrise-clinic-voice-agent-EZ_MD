"""Qwen decides the call and reads what the caller said.

The patient chart is never sent here. Spoken last-four digits are removed
before a sentence is sent. The code authenticator reads those digits itself.
"""

import json
import logging
import os
import re

import httpx

from datetime import date

from app.config import get_settings

log = logging.getLogger("clinic.llm")

_DIGITS = re.compile(r"\d")
_ALLOWED_INTENTS = {
    "refill",
    "appointment",
    "booking",
    "labs",
    "billing",
    "handoff",
    "advice",
    "emergency",
    "third_party",
    "injection",
    "off_topic",
}
_MEDS = {"lisinopril", "metformin", "atorvastatin", "levothyroxine"}
# Enough for a full phone-call transcript + prompts; cleared when the call ends.
_LLM_CTX = 4096
_HISTORY_CHARS = 6000

# Ollama structured-output schema — model can only emit these enum values.
_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": sorted(_ALLOWED_INTENTS),
        },
        "medication": {
            "type": "string",
            "enum": sorted(_MEDS) + [""],
        },
    },
    "required": ["intent", "medication"],
}
_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["yes", "no", "unsure"]}},
    "required": ["answer"],
}


def scrub_for_llm(text: str) -> str:
    """Drop every digit before the text can reach Qwen."""
    return _DIGITS.sub("", text)


def _session_memory() -> str:
    """Scrubbed prior turns for this call only. Empty once the session is gone."""
    try:
        from app.agent.graph import call_ctx

        call = call_ctx.get(None)
    except LookupError:
        return ""
    if call is None or not call.lines:
        return ""
    parts: list[str] = []
    for who, text in call.lines:
        cleaned = scrub_for_llm(text).strip()
        if cleaned:
            label = "caller" if who == "caller" else "agent"
            parts.append(f"{label}: {cleaned}")
    if not parts:
        return ""
    joined = "\n".join(parts)
    if len(joined) > _HISTORY_CHARS:
        joined = joined[-_HISTORY_CHARS:]
    return joined


def _with_memory(current: str) -> str:
    prior = _session_memory()
    if not prior:
        return current
    return f"Call so far:\n{prior}\n\nLatest caller:\n{current}"


async def understand_turn(text: str) -> dict | None:
    if os.environ.get("CLINIC_USE_LLM", "1") == "0":
        return None
    settings = get_settings()
    if not settings.ollama_model:
        return None
    spoken = scrub_for_llm(text)[:500]
    if not spoken.strip():
        return None
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": _INTENT_SCHEMA,
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": 40, "num_ctx": _LLM_CTX},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the decision brain for a clinic phone agent. "
                    "Use the call so far only for this session. "
                    "Map misheard drug names to the closest real medicine. "
                    "lice na frill, lisinovrel, and spelled L-I-S-I-N-O-P-R-I-L mean lisinopril. "
                    "A dose question or diagnosis request is advice. "
                    "A wife, husband, or friend is third_party. "
                    "Chest pain or trouble breathing is emergency. "
                    "Making an appointment, a new appointment, or saying they are a new patient is booking. "
                    "Asking when an existing appointment is, is appointment. "
                    "A clinic request is never off_topic. "
                    "Handoff only when they ask for a person or a staff member. "
                    "Bye, goodbye, and thank you are off_topic. "
                    "Do not include numbers."
                ),
            },
            {"role": "user", "content": _with_memory(spoken)},
        ],
    }
    base = settings.ollama_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = base + "/api/chat"
    import time

    from app.infrastructure.turn_timing import add_llm

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            content = response.json()["message"]["content"]
    except Exception:
        add_llm((time.perf_counter() - started) * 1000)
        log.info("qwen decision unavailable")
        return None
    add_llm((time.perf_counter() - started) * 1000)
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    intent = str(data.get("intent") or "")
    medication = str(data.get("medication") or "").lower()
    if intent not in _ALLOWED_INTENTS:
        intent = ""
    if medication not in _MEDS:
        medication = ""
    if _DIGITS.search(json.dumps(data)):
        medication = ""
    log.info("qwen intent=%s medication=%s", intent or "none", medication or "none")
    if not intent and not medication:
        return None
    return {"intent": intent, "medication": medication}


_PROTECTED = (
    "couldn't verify",
    "date of birth",
    "social security",
    "call 911",
    "can't diagnose",
    "someone else's",
    "can't change my rules",
    "office is closed",
    "medical advice",
    "please say",
    "what is your",
    "email address",
)


async def _chat(system: str, user: str, limit: int, schema: dict | str | None = None) -> str | None:
    if os.environ.get("CLINIC_USE_LLM", "1") == "0":
        return None
    settings = get_settings()
    if not settings.ollama_model or not user.strip():
        return None
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": schema if schema is not None else "json",
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": limit, "num_ctx": _LLM_CTX},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": _with_memory(user[:500])},
        ],
    }
    base = settings.ollama_base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    import time

    from app.infrastructure.turn_timing import add_llm

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(base + "/api/chat", json=payload)
            response.raise_for_status()
            content = response.json()["message"]["content"]
            add_llm((time.perf_counter() - started) * 1000)
            return content
    except Exception:
        add_llm((time.perf_counter() - started) * 1000)
        log.info("qwen decision unavailable")
        return None


def _json_object(content: str | None) -> dict | None:
    if not content:
        return None
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


IDENTITY_PROMPT = (
    "Extract only the name and date of birth the caller just said. "
    "You cannot look up a patient and you cannot decide if they match. "
    'Reply JSON only: {"name":"","dob":"YYYY-MM-DD","year":""}. '
    "name is the full name only. Use the closest spelling of what they said. "
    "Words like phone, decades, rate, and date are not part of the name. "
    "dob is the full date when they gave a month, a day, and a year. 14th March 1995 is 1995-03-14. "
    "If they gave only a year, leave dob empty and put that year in year. "
    "Do not repeat or invent any digits that are not a year. Leave a field empty when they did not say it."
)


def hide_ssn_for_llm(text: str) -> str:
    """Remove last-four digits before the sentence can reach Qwen. Years stay."""

    def drop_grouped(match: re.Match[str]) -> str:
        digits = match.group(0).replace(",", "")
        if len(digits) == 4 and not digits.startswith(("19", "20")):
            return " "
        return match.group(0)

    hidden = re.sub(r"\b\d,\d{3}\b", drop_grouped, text)
    hidden = re.sub(r"\b(?!(?:19|20)\d{2}\b)\d{4}\b", " ", hidden)
    hidden = re.sub(r"\b\d\b(?:[\s,]+)\d\b(?:[\s,]+)\d\b(?:[\s,]+)\d\b", " ", hidden)
    word = r"zero|oh|one|two|three|four|five|six|seven|eight|nine"
    hidden = re.sub(rf"\b(?:{word})\b(?:\s+\b(?:{word})\b){{3}}", " ", hidden, flags=re.IGNORECASE)
    return hidden


def _near(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 2:
        return False
    previous = list(range(len(right) + 1))
    for index, char in enumerate(left, start=1):
        current = [index]
        for other_index, other in enumerate(right, start=1):
            cost = 0 if char == other else 1
            current.append(min(previous[other_index] + 1, current[other_index - 1] + 1, previous[other_index - 1] + cost))
        previous = current
    return previous[-1] <= 1


def accept_identity(text: str, data: dict) -> dict:
    """Keep a model reading only when the caller's words support it."""
    words = re.findall(r"[a-z']+", text.lower())
    digits = re.sub(r"\D", "", re.sub(r"(?<=\d),\s*(?=\d)", "", text))
    raw_name = str(data.get("name") or "").strip()
    name_parts = [part for part in re.findall(r"[a-z']+", raw_name.lower()) if part not in {"phone", "date", "rate"}]
    name = None
    if len(name_parts) >= 2 and all(any(part[0] == word[0] and _near(part, word) for word in words) for part in name_parts[:2]):
        name = " ".join(name_parts[:2])
    dob = None
    parsed = str(data.get("dob") or "")[:10]
    try:
        found = date.fromisoformat(parsed)
    except ValueError:
        found = None
    if found is not None and (str(found.year) in digits or str(data.get("year") or "") == str(found.year)):
        dob = found
    year_text = str(data.get("year") or "")
    year = int(year_text) if re.fullmatch(r"(19|20)\d{2}", year_text) and year_text in digits else None
    if not name and dob is None and year is None:
        return {}
    return {"name": name, "dob": dob, "year": year}


CONTACT_PROMPT = (
    "The caller is giving contact details or changing the request. "
    'Reply JSON only: {"name":"","phone":"","changed_request":"booking|refill|appointment|labs|billing|handoff|none"}. '
    "name is their full name only when they said it. "
    "phone is ten digits written NNN-NNN-NNNN. 5550109192 is 555-010-9192. "
    "A nine-digit number is not a phone; leave phone empty. "
    "changed_request is none when they are only giving a name or a number. "
    "changed_request is booking when they say they are a new patient or ask for an appointment. "
    "Do not invent."
)


def accept_contact(text: str, data: dict) -> dict:
    """Keep a name or phone only when those words were actually spoken."""
    words = re.findall(r"[a-z']+", text.lower())
    digits = re.sub(r"\D", "", text)
    raw_name = str(data.get("name") or "")
    parts = [part for part in re.findall(r"[a-z']+", raw_name.lower()) if part not in {"phone", "callback", "number"}]
    name = None
    if len(parts) >= 2 and all(any(part[0] == word[0] and _near(part, word) for word in words) for part in parts[:2]):
        name = " ".join(parts[:2])
    raw_phone = re.sub(r"\D", "", str(data.get("phone") or ""))
    phone = None
    if len(raw_phone) == 10 and raw_phone in digits:
        phone = f"{raw_phone[:3]}-{raw_phone[3:6]}-{raw_phone[6:]}"
    changed = str(data.get("changed_request") or "")
    if changed not in {"booking", "refill", "appointment", "labs", "billing", "handoff"}:
        changed = ""
    lowered = text.lower()
    if changed == "booking" and not any(phrase in lowered for phrase in ("appointment", "new patient", "book", "schedule")):
        changed = ""
    if not name and not phone and not changed:
        return {}
    return {"name": name, "phone": phone, "changed_request": changed}


async def extract_contact(text: str) -> dict:
    content = await _chat(CONTACT_PROMPT, text, 60)
    heard = accept_contact(text, _json_object(content) or {})
    if heard:
        log.info("qwen contact name=%s phone=%s request=%s", bool(heard.get("name")), bool(heard.get("phone")), heard.get("changed_request") or "none")
    return heard


async def extract_identity(text: str) -> dict:
    """Read the name and date. The last four is removed before this text reaches Qwen."""
    shown = hide_ssn_for_llm(text)
    content = await _chat(IDENTITY_PROMPT, shown, 80)
    heard = accept_identity(text, _json_object(content) or {})
    if heard:
        log.info(
            "qwen identity name=%s dob=%s year=%s",
            bool(heard.get("name")),
            bool(heard.get("dob")),
            bool(heard.get("year")),
        )
    return heard


_BOOKING_PROMPTS = {
    "name": 'Reply JSON only: {"name":""}. The full name just spoken. Empty if they did not say a name.',
    "dob": 'Reply JSON only: {"dob":"YYYY-MM-DD"}. The date of birth just spoken. July 4 1992 is 1992-07-04. Empty if the date is incomplete.',
    "phone": 'Reply JSON only: {"phone":"NNN-NNN-NNNN"}. A ten-digit phone. 5550109192 is 555-010-9192. Empty if it is not ten digits.',
    "email": 'Reply JSON only: {"email":""}. The email just spoken, lowercase. "alex dot rivera at example dot com" is alex.rivera@example.com. Empty if none.',
    "insurance": 'Reply JSON only: {"self_pay":false,"provider":""}. self_pay is true if they said self-pay, selfie, selfy, or no insurance. Otherwise provider is the insurer they named. Empty both if unclear.',
    "reason": 'Reply JSON only: {"reason":""}. The visit reason in a few words, such as annual check-up. Empty if they did not give one.',
    "pref": 'Reply JSON only: {"doctor":"Dr. Patel|Dr. Lee|","part":"morning|afternoon|"}. Empty when they stated no preference.',
    "slot": 'Reply JSON only: {"choice":"first|second|third|"}. "the first one" is first. "3 p.m." or a spoken clock time leaves choice empty so code can match the time. Empty if they did not pick.',
}


def accept_booking(kind: str, text: str, data: dict) -> dict:
    """Keep a booking answer only when the caller's words support it."""
    from app.security.sensitive_capture import spoken_year

    lowered = text.lower()
    words = re.findall(r"[a-z']+", lowered)
    digits = re.sub(r"\D", "", text)
    if kind == "name":
        parts = [part for part in re.findall(r"[a-z']+", str(data.get("name") or "").lower())]
        if len(parts) >= 2 and all(any(part[0] == word[0] and _near(part, word) for word in words) for part in parts[:2]):
            return {"name": " ".join(parts[:2])}
    if kind == "dob":
        try:
            found = date.fromisoformat(str(data.get("dob") or "")[:10])
        except ValueError:
            found = None
        if found is not None and (str(found.year) in digits or spoken_year(text) == found.year):
            return {"dob": found}
    if kind == "phone":
        raw = re.sub(r"\D", "", str(data.get("phone") or ""))
        if len(raw) == 10 and raw in digits:
            return {"phone": f"{raw[:3]}-{raw[3:6]}-{raw[6:]}"}
    if kind == "email":
        email = str(data.get("email") or "").strip().lower()
        pieces = re.findall(r"[a-z0-9]+", email)
        if "@" in email and len(pieces) >= 2 and all(piece in lowered for piece in pieces if len(piece) > 2):
            return {"email": email}
    if kind == "insurance":
        self_pay_heard = any(
            token in lowered
            for token in ("self pay", "self-pay", "selfpay", "selfie", "selfy", "no insurance")
        )
        if data.get("self_pay") is True and self_pay_heard:
            return {"self_pay": True, "provider": ""}
        if self_pay_heard:
            return {"self_pay": True, "provider": ""}
        provider = str(data.get("provider") or "").strip()
        provider_words = [part for part in re.findall(r"[a-z']+", provider.lower()) if len(part) > 2]
        if provider_words and all(part in words for part in provider_words[:3]):
            return {"self_pay": False, "provider": provider[:120]}
    if kind == "reason":
        reason = " ".join(str(data.get("reason") or "").split())
        reason_words = [part for part in re.findall(r"[a-z']+", reason.lower()) if len(part) > 2]
        if reason and len(reason.split()) <= 12 and reason_words and any(part in words or any(_near(part, word) for word in words) for part in reason_words):
            return {"reason": reason}
    if kind == "pref":
        doctor = str(data.get("doctor") or "")
        part = str(data.get("part") or "")
        kept: dict = {}
        if doctor == "Dr. Patel" and "patel" in lowered:
            kept["doctor"] = doctor
        elif doctor == "Dr. Lee" and re.search(r"\blee\b", lowered):
            kept["doctor"] = doctor
        if part in {"morning", "afternoon"} and part in lowered:
            kept["part"] = part
        return kept
    if kind == "slot":
        choice = str(data.get("choice") or "")
        if choice in {"first", "second", "third"} and choice in words:
            return {"choice": choice}
    return {}


async def extract_booking(text: str, kind: str) -> dict:
    content = await _chat(_BOOKING_PROMPTS[kind], text, 50)
    heard = accept_booking(kind, text, _json_object(content) or {})
    if heard:
        log.info("qwen booking field=%s", kind)
    return heard


async def confirm_answer(text: str) -> str:
    """Let Qwen read a spoken yes or no. Digits are removed first."""
    content = await _chat(
        "yeah, sure, go ahead, and please do mean yes. "
        "no, don't, and cancel mean no.",
        scrub_for_llm(text),
        20,
        schema=_ANSWER_SCHEMA,
    )
    answer = str((_json_object(content) or {}).get("answer") or "")
    if answer in {"yes", "no"}:
        log.info("qwen answer=%s", answer)
        return answer
    return ""


async def phrase_for_speech(draft: str) -> str | None:
    """Qwen speaks a clinic fact. Safety lines stay exactly as written."""
    lowered = draft.lower()
    if any(line in lowered for line in _PROTECTED):
        return None
    content = await _chat(
        "Rewrite the clinic fact as one short spoken sentence. "
        'Reply JSON only: {"say":"..."}. Keep every fact. '
        "If the fact says it is not an approval, keep that. "
        "Do not add medical advice, dates of birth, or social security digits. "
        "Do not invent numbers.",
        draft,
        80,
    )
    spoken = str((_json_object(content) or {}).get("say") or "").strip()
    if not spoken or _FULL_SSN.search(spoken):
        return None
    if re.findall(r"\d+", spoken) != re.findall(r"\d+", draft):
        return None
    if "not an approval" in lowered and "not an approval" not in spoken.lower():
        return None
    log.info("qwen phrased reply")
    return spoken


_FULL_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
