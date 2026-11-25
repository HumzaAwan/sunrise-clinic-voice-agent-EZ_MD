"""Call state kept in process memory. Redis, when enabled, receives the scrubbed view only."""

from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4


@dataclass
class CallState:
    session_id: str = field(default_factory=lambda: str(uuid4()))
    phase: str = "listen"
    intent: str | None = None
    verified_patient_id: str | None = None
    verified_first_name: str | None = None
    verification_attempts: int = 0
    held_name: str | None = None
    held_dob: date | None = None
    held_last4: str | None = None
    held_signature: str | None = None
    recording_notice_delivered: bool = False
    emergency: bool = False
    say: str = ""
    med_hint: str | None = None
    draft: dict = field(default_factory=dict)
    pending: dict | None = None
    just_verified: bool = False
    redact_user: bool = False
    lines: list[tuple[str, str]] = field(default_factory=list)

    def public(self) -> dict:
        return {
            "session_id": self.session_id,
            "phase": self.phase,
            "intent": self.intent,
            "verified": self.verified_patient_id is not None,
            "verification_attempts": self.verification_attempts,
            "emergency": self.emergency,
            "recording_notice_delivered": self.recording_notice_delivered,
        }

    def clear_identity_hold(self) -> None:
        self.held_name = None
        self.held_dob = None
        self.held_last4 = None
        self.held_signature = None

    def redis_view(self) -> dict:
        """Scrubbed session plus the redacted transcript. Identity hold stays in process memory."""
        view = self.public()
        # Full call transcript for this session (redacted lines only).
        view["lines"] = [{"who": who, "text": text} for who, text in self.lines]
        return view

    def scrubbed(self) -> dict:
        """State safe to put in Redis or a checkpoint. Drafts and pending details stay in memory."""
        return self.public()
