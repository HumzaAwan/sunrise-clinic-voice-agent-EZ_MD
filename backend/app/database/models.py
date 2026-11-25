"""Authoritative clinic records. Full SSNs are not a column."""

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_code: Mapped[str] = mapped_column(String(16), unique=True)
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    dob: Mapped[date] = mapped_column(Date)
    ssn_last4: Mapped[str] = mapped_column(String(4))
    phone: Mapped[str] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    insurance_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    insurance_member_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    self_pay: Mapped[bool] = mapped_column(Boolean, default=False)
    lab_status: Mapped[str] = mapped_column(String(160), default="No recent labs")
    balance_due: Mapped[str] = mapped_column(String(16), default="0.00")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Medication(Base):
    __tablename__ = "medications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"))
    name: Mapped[str] = mapped_column(String(120))
    refills_remaining: Mapped[int] = mapped_column(Integer, default=0)
    last_filled: Mapped[str] = mapped_column(String(32), default="")
    pharmacy: Mapped[str] = mapped_column(String(120))


class AvailableSlot(Base):
    __tablename__ = "available_slots"
    __table_args__ = (UniqueConstraint("doctor", "slot_date", "slot_time"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    doctor: Mapped[str] = mapped_column(String(80))
    slot_date: Mapped[date] = mapped_column(Date)
    slot_time: Mapped[str] = mapped_column(String(16))
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"))
    slot_id: Mapped[str | None] = mapped_column(ForeignKey("available_slots.id"), nullable=True, unique=True)
    doctor: Mapped[str] = mapped_column(String(80))
    slot_date: Mapped[date] = mapped_column(Date)
    slot_time: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(32), default="booked")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefillRequest(Base):
    __tablename__ = "refill_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"))
    session_id: Mapped[str] = mapped_column(String(36))
    medication_name: Mapped[str] = mapped_column(String(120))
    pharmacy: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CallbackRequest(Base):
    __tablename__ = "callback_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    session_id: Mapped[str] = mapped_column(String(36))
    caller_name: Mapped[str] = mapped_column(String(120))
    callback_number: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="open")
    summary: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PatientMessage(Base):
    __tablename__ = "patient_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    session_id: Mapped[str] = mapped_column(String(36))
    category: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CallSessionRow(Base):
    __tablename__ = "call_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    livekit_room: Mapped[str | None] = mapped_column(String(80), nullable=True)
    phase: Mapped[str] = mapped_column(String(40), default="listen")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CallRecording(Base):
    __tablename__ = "call_recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36))
    storage_path: Mapped[str] = mapped_column(String(255))
    excluded_verification: Mapped[bool] = mapped_column(Boolean, default=True)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(String(36))
    content_redacted: Mapped[str] = mapped_column(Text)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    patient_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80))
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))
    scenario_id: Mapped[str] = mapped_column(String(80))
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str] = mapped_column(String(255), default="")


class EmailConfirmation(Base):
    __tablename__ = "email_confirmation_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"))
    appointment_id: Mapped[str] = mapped_column(ForeignKey("appointments.id"))
    to_address: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
