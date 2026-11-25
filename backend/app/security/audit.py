"""Audit rows record the action, not the verification values."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditEvent


async def write_audit(
    session: AsyncSession,
    *,
    session_id: str | None,
    patient_id: str | None,
    action: str,
    outcome: str,
) -> None:
    session.add(
        AuditEvent(
            session_id=session_id,
            patient_id=patient_id,
            action=action,
            outcome=outcome,
        )
    )
    await session.flush()
