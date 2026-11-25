"""Mask identifiers before they reach logs, transcripts, or the model."""

import re

_FULL_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def redact_text(text: str) -> str:
    masked = _FULL_SSN.sub("***-**-****", text)
    return _ISO_DATE.sub(lambda match: f"****-**-{match.group(3)}", masked)


def verification_placeholder() -> str:
    return "[verification details redacted]"
