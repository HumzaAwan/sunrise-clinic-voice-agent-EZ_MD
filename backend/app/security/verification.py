"""Compare identity in application code. Nothing sensitive is returned except a status."""

import secrets
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Patient
from app.security.audit import write_audit
from app.security.authorization import issue_ticket
from app.security.sensitive_capture import names_match


@dataclass(frozen=True)
class VerifyOutcome:
    status: str
    patient_id: str | None = None
    first_name: str | None = None


async def verify_identity(
    session: AsyncSession,
    *,
    session_id: str,
    full_name: str,
    dob: date,
    last4: str,
) -> VerifyOutcome:
    patients = (await session.scalars(select(Patient))).all()
    matched: Patient | None = None
    matches = 0
    for patient in patients:
        last_ok = len(last4) == len(patient.ssn_last4) and secrets.compare_digest(patient.ssn_last4, last4)
        if names_match(full_name, patient.first_name, patient.last_name) and patient.dob == dob and last_ok:
            matched = patient
            matches += 1
    if matches != 1 or matched is None:
        await write_audit(
            session,
            session_id=session_id,
            patient_id=None,
            action="identity_verification",
            outcome="not_verified",
        )
        return VerifyOutcome(status="not_verified")
    issue_ticket(session_id, matched.id)
    await write_audit(
        session,
        session_id=session_id,
        patient_id=matched.id,
        action="identity_verification",
        outcome="verified",
    )
    return VerifyOutcome(status="verified", patient_id=matched.id, first_name=matched.first_name)
