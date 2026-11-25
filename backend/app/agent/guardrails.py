"""Deterministic safety and intent checks. These run before any model call."""

import re
from difflib import SequenceMatcher

EMERGENCY_PHRASES = (
    "chest pain", "can't breathe", "cannot breathe", "difficulty breathing", "short of breath",
    "suicidal", "suicide", "kill myself", "want to die", "heart attack", "stroke",
    "unconscious", "severe bleeding", "not breathing",
)
INJECTION_PHRASES = (
    "ignore your instructions", "ignore previous", "ignore all instructions", "system prompt",
    "developer mode", "reveal all patients", "read me all", "dump the database", "all patients",
    "print all records", "override your rules",
)
ADVICE_PHRASES = (
    "what should i take", "should i take", "should i increase", "can i increase",
    "increase the dosage", "increase my dose", "increase the dose", "diagnose", "dose", "dosage",
    "change my dose", "change the dose", "what does my result mean", "what does that mean",
    "is it serious", "is this serious", "what medicine should", "recommend a medicine", "is it bad",
)
THIRD_PARTY_MARKERS = (
    "my wife", "my husband", "my spouse", "my son", "my daughter", "my mother", "my father",
    "my mom", "my dad", "my parent", "my friend", "my child", "for my wife", "for my husband",
)
THIRD_PARTY_TOPICS = ("lab", "appointment", "balance", "refill", "result", "record", "medication", "prescription", "bill", "chart")
HANDOFF_PHRASES = (
    "talk to a person", "real person", "representative", "receptionist", "human", "staff member",
    "transfer me", "i am confused", "i'm confused", "i am upset", "i'm upset", "this is ridiculous",
)
REFILL_PHRASES = ("refill", "prescription", "medication", "medicine")
LAB_PHRASES = ("lab", "labs", "test result", "results")
BILL_PHRASES = ("bill", "billing", "balance", "what do i owe", "payment")
BOOK_PHRASES = (
    "book", "schedule", "new patient", "first visit", "i'm new", "i am new",
    "make an appointment", "need an appointment", "need a appointment",
)
YES_WORDS = {"yes", "yeah", "yep", "yup", "correct", "confirm", "sure", "ok", "okay"}
NO_WORDS = {"no", "nope", "cancel", "stop", "dont", "incorrect"}
MED_WORDS = ("lisinopril", "metformin", "atorvastatin", "levothyroxine")


def classify(text: str) -> str:
    lowered = text.lower()
    if any(phrase in lowered for phrase in EMERGENCY_PHRASES):
        return "emergency"
    if any(phrase in lowered for phrase in INJECTION_PHRASES):
        return "injection"
    if any(marker in lowered for marker in THIRD_PARTY_MARKERS) and any(topic in lowered for topic in THIRD_PARTY_TOPICS):
        return "third_party"
    if any(phrase in lowered for phrase in ADVICE_PHRASES):
        return "advice"
    return "ok"


def detect_intent(text: str) -> str:
    lowered = text.lower()
    if any(phrase in lowered for phrase in HANDOFF_PHRASES):
        return "handoff"
    if any(phrase in lowered for phrase in REFILL_PHRASES):
        return "refill"
    if any(phrase in lowered for phrase in LAB_PHRASES):
        return "labs"
    if any(phrase in lowered for phrase in BILL_PHRASES):
        return "billing"
    if any(phrase in lowered for phrase in BOOK_PHRASES):
        return "booking"
    if "appointment" in lowered:
        return "appointment"
    return "unknown"


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower().replace("'", "")))


def is_yes(text: str) -> bool:
    return bool(_tokens(text) & YES_WORDS)


def is_no(text: str) -> bool:
    return bool(_tokens(text) & NO_WORDS)


_MED_ALIASES = {
    "lisinopril": ("lisinopril", "lisinovrel", "lisinapril", "lysinopril", "lisnopril", "lice", "lizzie", "lizzy", "na frill"),
    "metformin": ("metformin", "metforman"),
    "atorvastatin": ("atorvastatin", "lipitor"),
    "levothyroxine": ("levothyroxine", "synthroid"),
}


def _spelled_word(text: str) -> str:
    letters = re.findall(r"\b([a-z])\b", text.lower())
    return "".join(letters) if len(letters) >= 6 else ""


def medication_hint(text: str) -> str | None:
    lowered = text.lower()
    spelled = _spelled_word(lowered)
    for stem, aliases in _MED_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return stem
        if spelled and SequenceMatcher(None, spelled, stem).ratio() >= 0.72:
            return stem
    return None


def doctor_preference(text: str) -> str | None:
    lowered = text.lower()
    if "patel" in lowered:
        return "Dr. Patel"
    if "lee" in lowered:
        return "Dr. Lee"
    return None


def day_part(text: str) -> str | None:
    lowered = text.lower()
    if "morning" in lowered:
        return "morning"
    if "afternoon" in lowered:
        return "afternoon"
    return None


def word_count(text: str) -> int:
    return len(text.split())
