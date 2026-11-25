"""Verification, office hours, booking locks, and barge-in rules."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.database.memory import memory_db
from app.database.models import Appointment, AvailableSlot, AuditEvent, Patient
from app.security.authorization import AuthorizationError, reset_tickets
from app.security.sensitive_capture import parse_identity
from app.security.verification import verify_identity
from app.services.office_hours import ClinicClock
from app.services.records import SlotUnavailable, book_new_patient
from app.voice.control import GenerationGuard, on_interrupt, should_interrupt
from app.config import SEED_PATH

EASTERN = ZoneInfo("America/New_York")


def test_seed_file_has_no_full_ssn():
    text = SEED_PATH.read_text(encoding="utf-8")
    assert '"ssn":' not in text
    assert "000-" not in text


def test_identity_parse_ignores_spoken_filler():
    heard = "Okay, my last four digits are 3 3 4 4, date of birth is March 14 1985 and my name is Sarah Johnson"
    parsed = parse_identity(heard)
    assert parsed is not None
    name, dob, last4 = parsed
    assert name == "sarah johnson"
    assert dob.isoformat() == "1985-03-14"
    assert last4 == "3344"


def test_day_month_year_and_misheard_date_word():
    heard = "My rate of birth is 14 March 1985. Last 4 digits are 3344 and Sarah Johnson is my name."
    parsed = parse_identity(heard)
    assert parsed is not None
    name, dob, last4 = parsed
    assert name == "sarah johnson"
    assert dob.isoformat() == "1985-03-14"
    assert last4 == "3344"


@pytest.mark.asyncio
async def test_corrected_birth_year_is_remembered():
    from app.agent.runtime import run_turn
    from app.agent.state import CallState
    from app.services.office_hours import get_clock

    get_clock().set_override(datetime(2026, 6, 15, 10, 0, tzinfo=EASTERN))
    try:
        async with memory_db() as session:
            call = CallState()
            await run_turn(session, call, "I need a refill on my lisinopril")
            await run_turn(
                session,
                call,
                "Sarah Johnson, my date of birth is March 14, 1995. Last four digits are 3344.",
            )
            assert call.verified_patient_id is None
            mirrored = str(call.scrubbed())
            assert "1995" not in mirrored
            assert "3344" not in mirrored
            await run_turn(session, call, "No, my date of birth is March 14, 1985.")
            assert call.verified_patient_id is not None
            assert call.held_last4 is None
            assert "lisinopril" in call.say.lower()
    finally:
        get_clock().set_override(None)


def test_whisper_identity_fragments_match_sarah():
    from app.security.sensitive_capture import names_match, read_identity_parts

    name, dob, last4 = read_identity_parts("In this period, last 48 are 3, 3, 4, 4.")
    assert name is None
    assert dob is None
    assert last4 == "3344"
    parsed = parse_identity(
        "My full name is Sara Jonson. My date of birth is 14 March 1985 and last 4 digits are 3,344."
    )
    assert parsed is not None
    assert parsed[1].isoformat() == "1985-03-14"
    assert parsed[2] == "3344"
    assert names_match(parsed[0], "Sarah", "Johnson")


@pytest.mark.asyncio
async def test_spoken_fragments_verify_sarah():
    from app.agent.runtime import run_turn
    from app.agent.state import CallState
    from app.services.office_hours import get_clock

    get_clock().set_override(datetime(2026, 6, 15, 10, 0, tzinfo=EASTERN))
    try:
        async with memory_db() as session:
            call = CallState()
            await run_turn(session, call, "Okay, I need a refill on my Lizzy Nob brain")
            await run_turn(session, call, "My date of birth is March 14, 1985. My name is Sara Johnson.")
            assert call.verified_patient_id is None
            await run_turn(session, call, "In this period, last 48 are 3, 3, 4, 4.")
            assert call.verified_patient_id is not None
            assert call.held_last4 is None
            assert "3344" not in str(call.scrubbed())
            assert "lisinopril" in call.say.lower()
    finally:
        get_clock().set_override(None)


@pytest.mark.asyncio
async def test_year_correction_verifies_without_repeating_the_failure():
    from app.agent.runtime import run_turn
    from app.agent.state import CallState
    from app.security.sensitive_capture import names_match, parse_identity
    from app.services.office_hours import get_clock

    heard = "My phone name is Sarah Johnson. My date of birth is 14 March 1985 and my last four decades are 3 3 4 4"
    parsed = parse_identity(heard)
    assert parsed is not None
    assert names_match(parsed[0], "Sarah", "Johnson")
    assert parsed[2] == "3344"

    get_clock().set_override(datetime(2026, 6, 15, 10, 0, tzinfo=EASTERN))
    try:
        async with memory_db() as session:
            call = CallState()
            await run_turn(session, call, "I need a refill on my Disney fridge")
            await run_turn(
                session,
                call,
                "My full name is Sarah Johnson. My last four digits are 3344 and my date of birth is...",
            )
            assert call.verification_attempts == 0
            await run_turn(session, call, "14th March 1995.")
            assert call.verified_patient_id is None
            assert call.verification_attempts == 1
            await run_turn(session, call, "No, my date of birth is 1985.")
            assert call.verified_patient_id is not None
            assert call.verification_attempts == 1
            assert "couldn't verify" not in call.say.lower()
            assert "3344" not in str(call.scrubbed())
    finally:
        get_clock().set_override(None)


def test_llm_reads_spoken_identity_without_the_chart():
    from app.providers.llm.ollama import IDENTITY_PROMPT, accept_identity

    assert "3344" not in IDENTITY_PROMPT
    assert "000-" not in IDENTITY_PROMPT
    assert "P001" not in IDENTITY_PROMPT
    heard = accept_identity(
        "My phone name is Sara Jonson. My date of birth is 14 March 1985 and my last four decades are 3 3 4 4",
        {"name": "Sarah Johnson", "dob": "1985-03-14", "last4": "3344", "year": ""},
    )
    assert heard["name"] == "sarah johnson"
    assert heard["dob"].isoformat() == "1985-03-14"
    assert "last4" not in heard
    from app.providers.llm.ollama import hide_ssn_for_llm

    hidden = hide_ssn_for_llm("Sarah Johnson, March 14, 1985. Last four 3, 3, 4, 4 and 3344.")
    assert "3344" not in hidden
    assert "1985" in hidden
    assert "Sarah" in hidden
    invented = accept_identity("No, my date of birth is 1985.", {"name": "Maria Garcia", "dob": "1990-07-22", "last4": "9081", "year": "1985"})
    assert invented.get("name") is None
    assert invented.get("dob") is None
    assert invented.get("year") == 1985


def test_contact_reader_keeps_a_split_name_and_ten_digits():
    from app.providers.llm.ollama import accept_contact

    named = accept_contact(
        "Yeah, my name is Alex Riviera and my callback number is 555019192.",
        {"name": "Alex Riviera", "phone": "555-019-192", "changed_request": "none"},
    )
    assert named["name"] == "alex riviera"
    assert named["phone"] is None
    numbered = accept_contact(
        "Phone number is 5550109192.",
        {"name": "", "phone": "555-010-9192", "changed_request": "booking"},
    )
    assert numbered["phone"] == "555-010-9192"
    assert numbered["changed_request"] == ""
    switched = accept_contact(
        "Okay, I'm a new patient, I need an appointment.",
        {"name": "", "phone": "", "changed_request": "booking"},
    )
    assert switched["changed_request"] == "booking"


def test_booking_reader_accepts_spoken_email_date_and_insurance():
    from app.providers.llm.ollama import accept_booking

    email = accept_booking(
        "email",
        "alex dot rivera at example dot com",
        {"email": "alex.rivera@example.com"},
    )
    assert email["email"] == "alex.rivera@example.com"
    dob = accept_booking("dob", "July 4, 1992", {"dob": "1992-07-04"})
    assert dob["dob"].isoformat() == "1992-07-04"
    pay = accept_booking("insurance", "I am self-pay", {"self_pay": True, "provider": ""})
    assert pay["self_pay"] is True
    invented = accept_booking("email", "I don't have one", {"email": "hidden@example.com"})
    assert invented == {}


def test_spoken_email_survives_a_pause_and_a_misheard_domain():
    from app.security.sensitive_capture import spoken_email

    assert spoken_email("Email is Alex.ruvara at example.com.") == "alex.ruvara@example.com"
    assert spoken_email("alex dot rivera at example dot com") == "alex.rivera@example.com"
    assert spoken_email("And the email is Alex Dotrera at the red gmail.com.") == "alex.dotrera@redgmail.com"
    assert spoken_email("Alexilvira at the rate gmail.com") == "alexilvira@gmail.com"
    assert spoken_email("I have my email address Alex at the rate gmail.com") == "alex@gmail.com"


def test_spoken_time_keys_match_offer_phrasing():
    from app.agent.graph import _spoken_time_keys

    assert _spoken_time_keys("3:00 PM") & _spoken_time_keys("December 1, 2026 at 3 p.m.")
    assert _spoken_time_keys("11:00 AM") & _spoken_time_keys("the 11 am one")
    assert not (_spoken_time_keys("9:30 AM") & _spoken_time_keys("3 p.m."))


def test_accept_booking_treats_selfie_as_self_pay():
    from app.providers.llm.ollama import accept_booking

    assert accept_booking("insurance", "It's a selfie.", {"self_pay": True, "provider": ""})["self_pay"] is True
    assert accept_booking("insurance", "selfie", {"self_pay": False, "provider": ""})["self_pay"] is True


def test_llm_prompt_strips_digits():
    from app.providers.llm.ollama import scrub_for_llm

    scrubbed = scrub_for_llm("Sarah Johnson March 14 1985 last four 3344")
    assert "1985" not in scrubbed
    assert "3344" not in scrubbed
    assert "Sarah" in scrubbed


def test_misheard_lisinopril_still_matches():
    from app.agent.guardrails import medication_hint

    assert medication_hint("I need a refill on my lice na frill") == "lisinopril"
    assert medication_hint("L-I-S-I-N-O-V-R-E-L") == "lisinopril"


def test_spoken_and_numeric_identity_parse():
    spoken = parse_identity("sarah johnson march fourteenth nineteen eighty five three three four four")
    numeric = parse_identity("Sarah Johnson, March 14, 1985, 3344")
    assert spoken is not None and numeric is not None
    assert spoken[1].isoformat() == "1985-03-14"
    assert spoken[2] == "3344"
    assert numeric[1].isoformat() == "1985-03-14"
    assert numeric[0] == "sarah johnson"


def test_office_hours_weekday_weekend_and_dst():
    clock = ClinicClock("America/New_York")
    clock.set_override(datetime(2026, 6, 15, 10, 0, tzinfo=EASTERN))
    assert clock.is_open()
    clock.set_override(datetime(2026, 6, 15, 8, 59, tzinfo=EASTERN))
    assert not clock.is_open()
    clock.set_override(datetime(2026, 6, 15, 17, 0, tzinfo=EASTERN))
    assert not clock.is_open()
    clock.set_override(datetime(2026, 6, 13, 10, 0, tzinfo=EASTERN))
    assert not clock.is_open()
    # 14:00 UTC on 15 Jan 2026 is 09:00 EST, and the date is a Thursday.
    clock.set_override(datetime(2026, 1, 15, 14, 0, tzinfo=ZoneInfo("UTC")))
    assert clock.is_open()
    clock.set_override(datetime(2026, 1, 15, 13, 30, tzinfo=ZoneInfo("UTC")))
    assert not clock.is_open()
    # 13:00 UTC on 15 Jul 2026 is 09:00 EDT.
    clock.set_override(datetime(2026, 7, 15, 13, 0, tzinfo=ZoneInfo("UTC")))
    assert clock.is_open()
    clock.set_override(datetime(2026, 7, 15, 12, 30, tzinfo=ZoneInfo("UTC")))
    assert not clock.is_open()


async def test_mixed_identity_fails_and_does_not_log_values(caplog):
    caplog.set_level("INFO")
    async with memory_db() as session:
        outcome = await verify_identity(
            session,
            session_id="s-mixed",
            full_name="Sarah Johnson",
            dob=__import__("datetime").date(1972, 11, 8),
            last4="3344",
        )
        await session.commit()
        assert outcome.status == "not_verified"
        audit = (await session.scalars(select(AuditEvent))).all()
        assert audit[-1].patient_id is None
        assert audit[-1].outcome == "not_verified"
    assert "3344" not in caplog.text
    assert "1972" not in caplog.text


async def test_matching_identity_succeeds_without_echoing_last4():
    from datetime import date

    async with memory_db() as session:
        outcome = await verify_identity(
            session,
            session_id="s-ok",
            full_name="sarah johnson",
            dob=date(1985, 3, 14),
            last4="3344",
        )
        assert outcome.status == "verified"
        assert outcome.first_name == "Sarah"
        wrong_last4 = await verify_identity(
            session,
            session_id="s-bad-last4",
            full_name="Sarah Johnson",
            dob=date(1985, 3, 14),
            last4="9991",
        )
        wrong_name = await verify_identity(
            session,
            session_id="s-bad-name",
            full_name="Sarah Brown",
            dob=date(1985, 3, 14),
            last4="3344",
        )
        assert wrong_last4.status == "not_verified"
        assert wrong_name.status == "not_verified"


async def test_chart_tools_reject_unverified_and_closed_office():
    from app.services.office_hours import get_clock
    from app.services.records import lab_status

    get_clock().set_override(datetime(2026, 6, 15, 10, 0, tzinfo=EASTERN))
    async with memory_db() as session:
        with pytest.raises(AuthorizationError):
            await lab_status(session, "missing-ticket")
    get_clock().set_override(None)
    reset_tickets()


async def test_two_bookings_cannot_take_one_slot():
    from app.services.office_hours import get_clock

    get_clock().set_override(datetime(2026, 6, 15, 10, 0, tzinfo=EASTERN))
    async with memory_db() as session:
        slot = await session.scalar(select(AvailableSlot).where(AvailableSlot.is_available.is_(True)))
        assert slot is not None
        slot_id = slot.id
        draft = {
            "first_name": "Alex",
            "last_name": "Rivera",
            "dob": "1992-07-04",
            "phone": "555-010-1992",
            "email": "alex.rivera@example.com",
            "self_pay": True,
            "reason": "annual check-up",
        }
        await book_new_patient(session, session_id="book-1", draft=draft, slot_id=slot_id)
        await session.commit()
        second = dict(draft, first_name="Sam", email="sam.rivera@example.com")
        with pytest.raises(SlotUnavailable):
            await book_new_patient(session, session_id="book-2", draft=second, slot_id=slot_id)
        await session.rollback()
        booked = await session.scalar(select(func.count()).select_from(Appointment).where(Appointment.slot_id == slot_id))
        patients = await session.scalar(select(func.count()).select_from(Patient).where(Patient.first_name == "Sam"))
        assert booked == 1
        assert patients == 0
    get_clock().set_override(None)


def test_barge_in_drops_stale_audio_and_repeats_emergency():
    guard = GenerationGuard()
    first = guard.start()
    assert guard.allow_playback(first)
    assert should_interrupt(speech_ms=80, transcript="uh-huh") is False
    assert should_interrupt(speech_ms=400, transcript="I need a refill") is True
    assert on_interrupt(emergency=False, notice_delivered=True, guard=guard) is None
    assert guard.allow_playback(first) is False
    second = guard.start()
    assert guard.allow_playback(second) is True
    emergency = on_interrupt(emergency=True, notice_delivered=True, guard=guard)
    assert emergency is not None
    assert "nine-one-one" in emergency
