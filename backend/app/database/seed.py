"""Load synthetic clinic data. The seed file has last-four values only."""

import json
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SEED_PATH
from app.database.models import Appointment, AvailableSlot, Medication, Patient


async def seed_if_empty(session: AsyncSession, path=SEED_PATH) -> int:
    count = await session.scalar(select(func.count()).select_from(Patient))
    if count:
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    created = 0
    for row in payload["patients"]:
        patient = Patient(
            patient_code=row["patient_code"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            dob=date.fromisoformat(row["dob"]),
            ssn_last4=row["ssn_last4"],
            phone=row["phone"],
            lab_status=row["lab_status"],
            balance_due=row["balance_due"],
        )
        session.add(patient)
        await session.flush()
        for med in row["medications"]:
            session.add(
                Medication(
                    patient_id=patient.id,
                    name=med["name"],
                    refills_remaining=med["refills_remaining"],
                    last_filled=med["last_filled"],
                    pharmacy=med["pharmacy"],
                )
            )
        nxt = row.get("next_appointment")
        if nxt:
            session.add(
                Appointment(
                    patient_id=patient.id,
                    doctor=nxt["doctor"],
                    slot_date=date.fromisoformat(nxt["date"]),
                    slot_time=nxt["time"],
                    reason="Existing appointment",
                    status="scheduled",
                )
            )
        created += 1
    for block in payload["available_slots"]:
        slot_date = date.fromisoformat(block["date"])
        for time_label in block["times"]:
            session.add(
                AvailableSlot(
                    doctor=block["doctor"],
                    slot_date=slot_date,
                    slot_time=time_label,
                    is_available=True,
                )
            )
    await session.flush()
    return created
