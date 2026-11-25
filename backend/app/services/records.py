"""Chart reads and transactional writes. Tools refuse work without a ticket."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Appointment,
    AvailableSlot,
    CallbackRequest,
    EmailConfirmation,
    Medication,
    Patient,
    PatientMessage,
    RefillRequest,
)
from app.security.audit import write_audit
from app.security.authorization import AuthorizationError, require_ticket
from app.services.office_hours import get_clock


class SlotUnavailable(Exception):
    pass


def money_spoken(amount: str) -> str:
    quantized = Decimal(amount).quantize(Decimal("0.01"))
    dollars = int(quantized)
    cents = int((quantized - dollars) * 100)
    if cents == 0:
        unit = "dollar" if dollars == 1 else "dollars"
        return f"{dollars} {unit}"
    return f"{dollars} dollars and {cents} cents"


def when_spoken(slot_date: date, slot_time: str) -> str:
    return f"{slot_date.strftime('%B')} {slot_date.day}, {slot_date.year} at {slot_time}"


def _require_open(session_id: str) -> str:
    if not get_clock().is_open():
        raise AuthorizationError()
    return require_ticket(session_id).patient_id


async def get_patient(session: AsyncSession, patient_id: str) -> Patient | None:
    return await session.get(Patient, patient_id)


async def list_medications(session: AsyncSession, session_id: str) -> list[Medication]:
    patient_id = _require_open(session_id)
    rows = await session.scalars(select(Medication).where(Medication.patient_id == patient_id))
    return list(rows)


async def create_refill(
    session: AsyncSession,
    *,
    session_id: str,
    medication_name: str,
    pharmacy: str,
    refills_remaining: int,
) -> RefillRequest:
    patient_id = _require_open(session_id)
    status = "pending_doctor_approval" if refills_remaining <= 0 else "queued_for_processing"
    summary = "Doctor approval requested" if refills_remaining <= 0 else "Refill request recorded"
    row = RefillRequest(
        patient_id=patient_id,
        session_id=session_id,
        medication_name=medication_name,
        pharmacy=pharmacy,
        status=status,
        summary=summary,
    )
    session.add(row)
    await write_audit(session, session_id=session_id, patient_id=patient_id, action="refill_request", outcome="created")
    await session.flush()
    return row


async def next_appointment(session: AsyncSession, session_id: str) -> Appointment | None:
    patient_id = _require_open(session_id)
    rows = list(
        await session.scalars(select(Appointment).where(Appointment.patient_id == patient_id, Appointment.status != "cancelled"))
    )
    current = get_clock().now()
    upcoming = []
    for row in rows:
        starts = datetime.strptime(f"{row.slot_date.isoformat()} {row.slot_time}", "%Y-%m-%d %I:%M %p")
        starts = starts.replace(tzinfo=get_clock().zone)
        if starts >= current:
            upcoming.append((starts, row))
    if not upcoming:
        await write_audit(session, session_id=session_id, patient_id=patient_id, action="appointment_read", outcome="none")
        return None
    upcoming.sort(key=lambda item: item[0])
    await write_audit(session, session_id=session_id, patient_id=patient_id, action="appointment_read", outcome="allowed")
    return upcoming[0][1]


async def open_slots(
    session: AsyncSession,
    *,
    doctor: str | None = None,
    part_of_day: str | None = None,
) -> list[AvailableSlot]:
    rows = list(
        await session.scalars(
            select(AvailableSlot).where(AvailableSlot.is_available.is_(True)).order_by(AvailableSlot.slot_date, AvailableSlot.slot_time)
        )
    )
    if doctor:
        rows = [row for row in rows if row.doctor == doctor]
    if part_of_day == "morning":
        rows = [row for row in rows if row.slot_time.endswith("AM")]
    elif part_of_day == "afternoon":
        rows = [row for row in rows if row.slot_time.endswith("PM")]
    rows.sort(key=lambda row: datetime.strptime(f"{row.slot_date.isoformat()} {row.slot_time}", "%Y-%m-%d %I:%M %p"))
    return rows


async def _take_slot(session: AsyncSession, slot_id: str) -> AvailableSlot:
    result = await session.execute(
        update(AvailableSlot)
        .where(AvailableSlot.id == slot_id, AvailableSlot.is_available.is_(True))
        .values(is_available=False)
    )
    if result.rowcount != 1:
        raise SlotUnavailable()
    slot = await session.get(AvailableSlot, slot_id)
    if slot is None:
        raise SlotUnavailable()
    return slot


def _confirmation_body(name: str, doctor: str, when: str, reason: str) -> str:
    return (
        f"Sunrise Family Clinic appointment for {name} with {doctor} on {when}. "
        f"Reason: {reason}. Please arrive 15 minutes early with a photo ID and insurance card."
    )


async def book_new_patient(session: AsyncSession, *, session_id: str, draft: dict, slot_id: str) -> Appointment:
    if not get_clock().is_open():
        raise AuthorizationError()
    slot = await _take_slot(session, slot_id)
    patient = Patient(
        patient_code=f"N{uuid4().hex[:8]}",
        first_name=draft["first_name"],
        last_name=draft["last_name"],
        dob=date.fromisoformat(draft["dob"]),
        ssn_last4="",
        phone=draft["phone"],
        email=draft["email"],
        insurance_provider=draft.get("insurance_provider"),
        insurance_member_id=draft.get("member_id"),
        self_pay=bool(draft.get("self_pay")),
        lab_status="No recent labs",
        balance_due="0.00",
    )
    session.add(patient)
    await session.flush()
    appointment = Appointment(
        patient_id=patient.id,
        slot_id=slot.id,
        doctor=slot.doctor,
        slot_date=slot.slot_date,
        slot_time=slot.slot_time,
        reason=draft["reason"],
        status="booked",
    )
    session.add(appointment)
    await session.flush()
    when = when_spoken(slot.slot_date, slot.slot_time)
    session.add(
        EmailConfirmation(
            patient_id=patient.id,
            appointment_id=appointment.id,
            to_address=draft["email"],
            subject="Sunrise Family Clinic appointment",
            body=_confirmation_body(f"{patient.first_name} {patient.last_name}", slot.doctor, when, draft["reason"]),
        )
    )
    await write_audit(session, session_id=session_id, patient_id=patient.id, action="appointment_booked", outcome="created")
    await session.flush()
    return appointment


async def book_existing(session: AsyncSession, *, session_id: str, email: str, reason: str, slot_id: str) -> Appointment:
    patient_id = _require_open(session_id)
    patient = await session.get(Patient, patient_id)
    if patient is None:
        raise AuthorizationError()
    slot = await _take_slot(session, slot_id)
    if not patient.email:
        patient.email = email
    appointment = Appointment(
        patient_id=patient.id,
        slot_id=slot.id,
        doctor=slot.doctor,
        slot_date=slot.slot_date,
        slot_time=slot.slot_time,
        reason=reason,
        status="booked",
    )
    session.add(appointment)
    await session.flush()
    when = when_spoken(slot.slot_date, slot.slot_time)
    session.add(
        EmailConfirmation(
            patient_id=patient.id,
            appointment_id=appointment.id,
            to_address=email,
            subject="Sunrise Family Clinic appointment",
            body=_confirmation_body(f"{patient.first_name} {patient.last_name}", slot.doctor, when, reason),
        )
    )
    await write_audit(session, session_id=session_id, patient_id=patient.id, action="appointment_booked", outcome="created")
    await session.flush()
    return appointment


async def lab_status(session: AsyncSession, session_id: str) -> str:
    patient_id = _require_open(session_id)
    patient = await session.get(Patient, patient_id)
    if patient is None:
        raise AuthorizationError()
    await write_audit(session, session_id=session_id, patient_id=patient_id, action="lab_status_read", outcome="allowed")
    return patient.lab_status


async def balance_due(session: AsyncSession, session_id: str) -> str:
    patient_id = _require_open(session_id)
    patient = await session.get(Patient, patient_id)
    if patient is None:
        raise AuthorizationError()
    await write_audit(session, session_id=session_id, patient_id=patient_id, action="billing_read", outcome="allowed")
    return patient.balance_due


async def save_callback(
    session: AsyncSession,
    *,
    session_id: str,
    caller_name: str,
    callback_number: str,
    reason: str,
    kind: str,
    patient_id: str | None,
) -> CallbackRequest:
    summary = "After-hours message" if kind == "after_hours" else "Staff handoff requested"
    row = CallbackRequest(
        patient_id=patient_id,
        session_id=session_id,
        caller_name=caller_name,
        callback_number=callback_number,
        reason=reason[:160],
        kind=kind,
        summary=summary,
    )
    session.add(row)
    await write_audit(session, session_id=session_id, patient_id=patient_id, action=f"{kind}_saved", outcome="created")
    await session.flush()
    return row


async def save_doctor_message(
    session: AsyncSession,
    *,
    session_id: str,
    patient_id: str | None,
    summary: str,
    phone: str | None = None,
) -> None:
    note = summary.strip()
    if phone:
        note = f"{note} · phone {phone}"
    session.add(
        PatientMessage(
            patient_id=patient_id,
            session_id=session_id,
            category="doctor_message",
            summary=note[:160],
        )
    )
    await write_audit(session, session_id=session_id, patient_id=patient_id, action="doctor_message", outcome="created")
    await session.flush()
