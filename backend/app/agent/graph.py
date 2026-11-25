"""LangGraph clinic workflow. Audio never flows through this graph.

The compiled graph only carries a branch name. A partial name, date of birth,
or last four stays on the call until verification succeeds or three attempts
fail. That hold is not copied onto the graph or into Redis.
"""

import re
from contextvars import ContextVar
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.guardrails import (
    classify,
    day_part,
    detect_intent,
    doctor_preference,
    is_no,
    is_yes,
    medication_hint,
    word_count,
)
from app.agent.phrases import (
    ADVICE,
    ARRIVE,
    CALLBACK_CLOSE,
    CLOSED_ASK,
    FIRST_VISIT,
    OFF_TOPIC,
    VERIFY_ASK,
    VERIFY_FAIL,
    VERIFY_STOP,
    with_notice,
)
from app.agent.state import CallState
from app.database.models import AvailableSlot
from app.security.authorization import AuthorizationError
from app.security.sensitive_capture import (
    parse_date_only,
    parse_email,
    parse_identity,
    parse_person_name,
    parse_phone,
    phone_digit_count,
    read_identity_parts,
    spoken_year,
)
from app.security.verification import verify_identity
from app.services.records import (
    SlotUnavailable,
    balance_due,
    book_existing,
    book_new_patient,
    create_refill,
    lab_status,
    list_medications,
    money_spoken,
    next_appointment,
    open_slots,
    save_callback,
    save_doctor_message,
    when_spoken,
)

call_ctx: ContextVar[CallState] = ContextVar("call_ctx")
db_ctx: ContextVar[AsyncSession] = ContextVar("db_ctx")
text_ctx: ContextVar[str] = ContextVar("text_ctx")

PHASE_NODE = {
    "verify": "verify",
    "refill_confirm": "refill",
    "refill_pick": "refill",
    "book_new": "booking",
    "book_contact": "booking",
    "book_name": "booking",
    "book_dob": "booking",
    "book_phone": "booking",
    "book_email": "booking",
    "book_insurance": "booking",
    "book_reason": "booking",
    "book_pref": "booking",
    "book_offer": "booking",
    "book_confirm": "booking",
    "callback_name": "after_hours",
    "callback_number": "after_hours",
    "callback_reason": "after_hours",
    "callback_confirm": "after_hours",
    "handoff_contact": "handoff",
    "handoff_reason": "handoff",
    "handoff_confirm": "handoff",
    "advice_confirm": "advice",
    "advice_reason": "advice",
    "advice_phone": "advice",
}
INTENT_NODE = {
    "refill": "refill",
    "labs": "labs",
    "billing": "billing",
    "booking": "booking",
    "appointment": "appointment",
    "handoff": "handoff",
    "unknown": "off_topic",
}


class GraphState(TypedDict, total=False):
    branch: str


def _end() -> dict:
    return {"branch": "end"}


def _speak(call: CallState, text: str) -> None:
    delivered, spoken = with_notice(call.recording_notice_delivered, text)
    call.recording_notice_delivered = delivered
    call.say = spoken


_ACK = {"thank", "you", "thanks", "uh", "um", "uhh", "hmm", "okay", "ok", "hello", "hi", "hey"}


async def _answer(text: str) -> str:
    if is_yes(text) and not is_no(text):
        return "yes"
    if is_no(text) and not is_yes(text):
        return "no"
    words = set(re.findall(r"[a-z']+", text.lower()))
    # "Thanks." is not a yes/no — do not burn an LLM call or hang the turn.
    if not words or words <= _ACK:
        return "unsure"
    from app.providers.llm.ollama import confirm_answer

    return await confirm_answer(text)


def _thanks(call: CallState) -> str:
    if call.just_verified and call.verified_first_name:
        return f"Thank you, {call.verified_first_name}. "
    return ""


def _match_meds(meds, text: str):
    if not text:
        return None
    hint = medication_hint(text)
    if hint:
        hinted = [med for med in meds if hint in med.name.lower()]
        if len(hinted) == 1:
            return hinted[0]
    lowered = text.lower()
    hits = [med for med in meds if med.name.lower() in lowered or med.name.split()[0].lower() in lowered]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return hits
    return None


def spell_email(email: str) -> str:
    local, _, domain = email.partition("@")
    pieces = ["dot" if char == "." else char for char in local]
    return f"{' '.join(pieces)} at {domain}"


async def safety_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    text = text_ctx.get()
    if call.emergency:
        _speak(call, "This sounds like an emergency. Please hang up and call nine-one-one, or go to the nearest emergency room.")
        return _end()
    kind = classify(text)
    if kind == "emergency":
        call.emergency = True
        call.pending = None
        call.phase = "emergency"
        _speak(call, "This sounds like an emergency. Please hang up and call nine-one-one, or go to the nearest emergency room.")
        return _end()
    if kind == "injection":
        call.pending = None
        call.phase = "listen"
        _speak(
            call,
            "I can't change my rules or read out clinic records that way. "
            "I can help the caller with their own appointment, refill, lab status, or billing.",
        )
        return _end()
    if kind == "third_party":
        call.pending = None
        call.phase = "listen"
        _speak(call, "I can only speak with the patient about their own care. I can't share someone else's information.")
        return _end()
    if kind == "advice":
        call.pending = None
        return {"branch": "advice"}
    return {"branch": "office"}


async def office_node(_state: GraphState) -> GraphState:
    from app.services.office_hours import get_clock

    call = call_ctx.get()
    call.office_open = get_clock().is_open()
    if not call.office_open:
        return {"branch": "after_hours"}
    return {"branch": "route"}


_FORM_INTENT = {
    "verify": "verify",
    "refill_confirm": "refill",
    "refill_pick": "refill",
    "book_new": "booking",
    "book_contact": "booking",
    "book_name": "booking",
    "book_dob": "booking",
    "book_phone": "booking",
    "book_email": "booking",
    "book_insurance": "booking",
    "book_reason": "booking",
    "book_pref": "booking",
    "book_offer": "booking",
    "book_confirm": "booking",
    "callback_name": "after_hours",
    "callback_number": "after_hours",
    "callback_reason": "after_hours",
    "callback_confirm": "after_hours",
    "handoff_contact": "handoff",
    "handoff_reason": "handoff",
    "handoff_confirm": "handoff",
    "advice_confirm": "advice",
    "advice_reason": "advice",
}

BOOK_CONTACT_ASK = (
    "Please say your full name, your date of birth, and a 10-digit phone number."
)


def _wants_booking(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in ("new patient", "new appointment", "book", "schedule", "first visit"))


async def route_node(_state: GraphState) -> GraphState:
    from app.providers.llm.ollama import understand_turn

    call = call_ctx.get()
    text = text_ctx.get()
    if call.phase in PHASE_NODE:
        # Form replies (doctor note, yes/no, contact fields) are content — never re-route.
        # "Call me as soon as you can" must stay on advice_reason and be saved as the note.
        return {"branch": PHASE_NODE[call.phase]}
    if not call.verified_patient_id and parse_identity(text):
        call.phase = "verify"
        call.redact_user = True
        return {"branch": "verify"}
    # Prefer deterministic phrases ("balance", "refill", …) over Qwen guesses.
    keyword = detect_intent(text)
    if keyword in INTENT_NODE:
        call.intent = keyword
        hint = medication_hint(text)
        if hint:
            call.med_hint = hint
        return {"branch": INTENT_NODE[keyword]}
    decision = await understand_turn(text)
    intent = "booking" if _wants_booking(text) else (decision or {}).get("intent") or ""
    if intent not in INTENT_NODE:
        intent = "unknown"
    call.intent = None if intent in {"", "unknown"} else intent
    hint = (decision or {}).get("medication") or medication_hint(text)
    if hint:
        call.med_hint = hint
    return {"branch": INTENT_NODE.get(intent, "off_topic")}


async def verify_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    text = text_ctx.get()
    db = db_ctx.get()
    if call.phase != "verify":
        call.phase = "verify"
        _speak(call, VERIFY_ASK)
        return _end()
    call.redact_user = True
    from app.providers.llm.ollama import extract_identity

    heard = await extract_identity(text)
    parsed_name, parsed_dob, parsed_last4 = read_identity_parts(text)
    name = heard.get("name") or parsed_name
    dob = heard.get("dob") or parsed_dob
    last4 = parsed_last4
    updated = False
    if name:
        call.held_name = name
        updated = True
    if dob is not None:
        call.held_dob = dob
        updated = True
    elif call.held_dob is not None:
        year = heard.get("year") or spoken_year(text)
        if year and year != call.held_dob.year:
            call.held_dob = call.held_dob.replace(year=year)
            updated = True
    if last4:
        call.held_last4 = last4
        updated = True
    if not call.held_name or call.held_dob is None or not call.held_last4:
        _speak(call, f"I didn't catch all three details. {VERIFY_ASK}")
        return _end()
    signature = f"{call.held_name}|{call.held_dob.isoformat()}|{call.held_last4}"
    if signature == call.held_signature and not updated:
        _speak(call, f"I didn't catch all three details. {VERIFY_ASK}")
        return _end()
    call.held_signature = signature
    full_name = call.held_name
    dob = call.held_dob
    last4 = call.held_last4
    try:
        outcome = await verify_identity(
            db, session_id=call.session_id, full_name=full_name, dob=dob, last4=last4
        )
    finally:
        last4 = ""
        dob = None
    if outcome.status != "verified" or outcome.patient_id is None:
        call.verification_attempts += 1
        if call.verification_attempts >= 3:
            call.clear_identity_hold()
            call.phase = "handoff_contact"
            _speak(call, VERIFY_STOP)
            return _end()
        _speak(call, f"{VERIFY_FAIL} {VERIFY_ASK}")
        return _end()
    call.clear_identity_hold()
    call.verified_patient_id = outcome.patient_id
    call.verified_first_name = outcome.first_name
    nxt = INTENT_NODE.get(call.intent or "", "end")
    if nxt in {"end", "off_topic"}:
        call.phase = "listen"
        call.just_verified = False
        _speak(call, f"Thank you, {outcome.first_name}. How can I help you today?")
        return _end()
    call.phase = "listen"
    call.just_verified = True
    return {"branch": nxt}


async def refill_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    text = text_ctx.get()
    db = db_ctx.get()
    if not call.verified_patient_id:
        hint = medication_hint(text)
        if hint:
            call.med_hint = hint
        call.intent = "refill"
        call.phase = "verify"
        _speak(call, VERIFY_ASK)
        return _end()
    prefix = _thanks(call)
    if call.phase == "refill_confirm":
        answer = await _answer(text)
        if answer == "no":
            call.pending = None
            call.phase = "listen"
            _speak(call, "Okay, I will not submit that request.")
            return _end()
        if answer != "yes":
            _speak(call, "Please say yes to submit the request, or no to cancel.")
            return _end()
        pending = call.pending or {}
        row = await create_refill(
            db,
            session_id=call.session_id,
            medication_name=pending["medication"],
            pharmacy=pending["pharmacy"],
            refills_remaining=int(pending["refills_remaining"]),
        )
        call.pending = None
        call.phase = "listen"
        if row.status == "pending_doctor_approval":
            _speak(call, f"I sent a request to the doctor to approve {row.medication_name}. It is not an approval.")
        else:
            _speak(
                call,
                f"I submitted a refill request for {row.medication_name} at {row.pharmacy}. "
                "It still has to be processed. It is not an approval.",
            )
        return _end()
    try:
        meds = await list_medications(db, call.session_id)
    except AuthorizationError:
        _speak(call, "I can't look at medications until your identity is verified.")
        return _end()
    if not call.med_hint:
        from app.providers.llm.ollama import understand_turn

        decision = await understand_turn(text)
        if decision and decision.get("medication"):
            call.med_hint = decision["medication"]
    match = _match_meds(meds, text)
    if match is None and call.med_hint:
        match = _match_meds(meds, call.med_hint)
    if match is None:
        named = medication_hint(text)
        if named:
            _speak(call, "I don't have that medication on the chart. I can have a staff member call you back.")
            call.phase = "listen"
            return _end()
        call.phase = "refill_pick"
        _speak(call, f"{prefix}Which medication should I request?")
        return _end()
    if isinstance(match, list):
        call.phase = "refill_pick"
        _speak(call, "Please say the name of the one medication you need.")
        return _end()
    call.pending = {
        "medication": match.name,
        "pharmacy": match.pharmacy,
        "refills_remaining": match.refills_remaining,
    }
    call.phase = "refill_confirm"
    if match.refills_remaining <= 0:
        question = (
            f"There are no refills left for {match.name}. I can send a request to the doctor for approval. "
            "It is not an approval. Should I send it?"
        )
    else:
        question = (
            f"I can request {match.name} for pickup at {match.pharmacy}. "
            "Submitting this creates a refill request. It is not an approval. Should I submit it?"
        )
    _speak(call, f"{prefix}{question}")
    return _end()


async def appointment_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    db = db_ctx.get()
    if not call.verified_patient_id:
        call.intent = "appointment"
        call.phase = "verify"
        _speak(call, VERIFY_ASK)
        return _end()
    prefix = _thanks(call)
    try:
        found = await next_appointment(db, call.session_id)
    except AuthorizationError:
        _speak(call, "I can't look up an appointment until your identity is verified.")
        return _end()
    call.phase = "listen"
    if found is None:
        _speak(call, f"{prefix}I don't see an upcoming appointment. I can book one, or have a staff member call you back.")
        return _end()
    when = when_spoken(found.slot_date, found.slot_time)
    _speak(call, f"{prefix}Your next appointment is {when} with {found.doctor}.")
    return _end()


async def labs_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    db = db_ctx.get()
    if not call.verified_patient_id:
        call.intent = "labs"
        call.phase = "verify"
        _speak(call, VERIFY_ASK)
        return _end()
    prefix = _thanks(call)
    try:
        status = await lab_status(db, call.session_id)
    except AuthorizationError:
        _speak(call, "I can't check lab status until your identity is verified.")
        return _end()
    call.phase = "listen"
    _speak(call, f"{prefix}Your lab status is {status}. I can't read or interpret the result.")
    return _end()


async def billing_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    db = db_ctx.get()
    if not call.verified_patient_id:
        call.intent = "billing"
        call.phase = "verify"
        _speak(call, VERIFY_ASK)
        return _end()
    prefix = _thanks(call)
    try:
        amount = await balance_due(db, call.session_id)
    except AuthorizationError:
        _speak(call, "I can't check a balance until your identity is verified.")
        return _end()
    call.phase = "listen"
    _speak(call, f"{prefix}Your balance due is {money_spoken(amount)}.")
    return _end()


def _split_name(value: str) -> tuple[str, str]:
    parts = value.split()
    return parts[0].title(), " ".join(part.title() for part in parts[1:])


async def _offer(call: CallState, db: AsyncSession) -> None:
    slots = await open_slots(db, doctor=call.draft.get("doctor"), part_of_day=call.draft.get("part_of_day"))
    chosen = slots[:3]
    if not chosen:
        call.phase = "listen"
        _speak(call, "I don't have an open time that matches. I can have a staff member call you back.")
        return
    call.draft["offered_ids"] = [slot.id for slot in chosen]
    call.phase = "book_offer"
    labels = [f"{when_spoken(slot.slot_date, slot.slot_time)} with {slot.doctor}" for slot in chosen]
    if len(labels) == 1:
        spoken = labels[0]
    elif len(labels) == 2:
        spoken = f"{labels[0]}, or {labels[1]}"
    else:
        spoken = f"{labels[0]}, {labels[1]}, or {labels[2]}"
    _speak(call, f"I can offer {spoken}. Which one should I book?")


def _spoken_time_keys(text: str) -> set[str]:
    """Map '3:00 PM', '3 p.m.', '3pm' to shared keys like {'3pm', '3:00pm'}."""
    keys: set[str] = set()
    for match in re.finditer(
        r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?",
        text.lower(),
    ):
        hour = int(match.group(1))
        minute = int(match.group(2) or "0")
        period = f"{match.group(3)}m"
        keys.add(f"{hour}{period}")
        keys.add(f"{hour}:{minute:02d}{period}")
    return keys


async def _select_slot(db: AsyncSession, text: str, offered_ids: list[str]) -> AvailableSlot | None:
    slots = []
    for slot_id in offered_ids:
        slot = await db.get(AvailableSlot, slot_id)
        if slot is not None and slot.is_available:
            slots.append(slot)
    if not slots:
        return None
    lowered = text.lower()
    words = set(re.findall(r"[a-z]+", lowered))
    if words & {"first", "one"}:
        return slots[0]
    if words & {"second", "two"} and len(slots) > 1:
        return slots[1]
    if words & {"third", "three"} and len(slots) > 2:
        return slots[2]
    heard_times = _spoken_time_keys(lowered)
    for slot in slots:
        if heard_times and heard_times & _spoken_time_keys(slot.slot_time):
            return slot
        if slot.slot_time.lower() in lowered or slot.slot_time.split()[0] in lowered:
            return slot
    return None


async def booking_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    text = text_ctx.get()
    db = db_ctx.get()
    prefix = _thanks(call)

    if call.phase == "listen":
        if not call.verified_patient_id:
            call.phase = "book_new"
            _speak(call, FIRST_VISIT)
            return _end()
        call.phase = "book_reason"
        _speak(call, f"{prefix}What is the reason for the visit, in a few words?")
        return _end()

    if call.phase == "book_new":
        answer = await _answer(text)
        if answer == "yes":
            call.draft["new_patient"] = True
            call.phase = "book_contact"
            _speak(call, BOOK_CONTACT_ASK)
            return _end()
        if answer == "no":
            call.intent = "booking"
            call.phase = "verify"
            _speak(call, VERIFY_ASK)
            return _end()
        _speak(call, FIRST_VISIT)
        return _end()

    if call.phase in {"book_contact", "book_name", "book_dob", "book_phone"}:
        call.phase = "book_contact"
        call.redact_user = True

        digits = phone_digit_count(text)
        phone = parse_phone(text)
        phone_attempt = digits >= 7
        if phone:
            call.draft["phone"] = phone
        elif phone_attempt and not call.draft.get("phone"):
            # Digits look like a phone — validate in code only, never send to the LLM.
            _speak(call, "I need a 10-digit phone number. Please say all ten digits.")
            return _end()

        # Phone-only turns already handled above; skip LLM when the line is mostly digits.
        mostly_digits = digits >= 7 and len(text.split()) <= 4
        if not mostly_digits:
            from app.providers.llm.ollama import extract_booking

            if not call.draft.get("first_name"):
                name = (await extract_booking(text, "name")).get("name") or parse_person_name(text)
                if name:
                    first, last = _split_name(name)
                    call.draft["first_name"] = first
                    call.draft["last_name"] = last

            if not call.draft.get("dob"):
                dob = (await extract_booking(text, "dob")).get("dob") or parse_date_only(text)
                if dob is not None:
                    call.draft["dob"] = dob.isoformat()
        else:
            if not call.draft.get("first_name"):
                name = parse_person_name(text)
                if name:
                    first, last = _split_name(name)
                    call.draft["first_name"] = first
                    call.draft["last_name"] = last
            if not call.draft.get("dob"):
                dob = parse_date_only(text)
                if dob is not None:
                    call.draft["dob"] = dob.isoformat()

        missing = []
        if not call.draft.get("first_name"):
            missing.append("your full name")
        if not call.draft.get("dob"):
            missing.append("your date of birth")
        if not call.draft.get("phone"):
            missing.append("a 10-digit phone number")
        if missing:
            if len(missing) == 1:
                _speak(call, f"Please say {missing[0]}.")
            elif len(missing) == 2:
                _speak(call, f"Please say {missing[0]} and {missing[1]}.")
            else:
                _speak(call, BOOK_CONTACT_ASK)
            return _end()

        call.phase = "book_email"
        _speak(call, "What is your email address?")
        return _end()

    if call.phase == "book_email":
        from app.providers.llm.ollama import extract_booking

        from app.security.sensitive_capture import spoken_email

        email = spoken_email(text) or (await extract_booking(text, "email")).get("email") or parse_email(text)
        if email is None:
            _speak(call, "Please say the email address, including the at sign.")
            return _end()
        call.draft["email"] = email
        if call.draft.get("new_patient"):
            call.phase = "book_insurance"
            _speak(call, "Say your insurance provider and member ID, or say self-pay.")
        else:
            call.phase = "book_pref"
            _speak(call, "Do you prefer Dr. Patel, Dr. Lee, a morning, or an afternoon? You can also say no preference.")
        return _end()

    if call.phase == "book_insurance":
        from app.providers.llm.ollama import extract_booking

        heard = await extract_booking(text, "insurance")
        lowered = text.lower()
        self_pay = bool(
            heard.get("self_pay")
            or any(
                token in lowered
                for token in (
                    "self-pay",
                    "self pay",
                    "selfpay",
                    "self-p",
                    "selfie",
                    "selfy",
                    "self payed",
                    "paying myself",
                    "no insurance",
                )
            )
        )
        provider = (heard.get("provider") or "").strip()
        filler = {"yes", "yeah", "yep", "yup", "no", "nope", "ok", "okay", "sure", "um", "uh", "alp", "please"}
        provider_words = [w for w in re.findall(r"[a-z']+", (provider or text).lower()) if w not in filler]
        if self_pay:
            call.draft["self_pay"] = True
            call.draft["insurance_provider"] = None
            call.draft["member_id"] = None
        elif provider_words and (provider or len(provider_words) >= 1 and len(provider_words[0]) > 3):
            call.draft["self_pay"] = False
            call.draft["insurance_provider"] = (provider or text.strip())[:120]
            call.draft["member_id"] = None
        else:
            _speak(call, "Say your insurance provider and member ID, or say self-pay.")
            return _end()
        call.phase = "book_reason"
        _speak(call, "What is the reason for the visit, in a few words?")
        return _end()

    if call.phase == "book_reason":
        from app.providers.llm.ollama import extract_booking

        reason = (await extract_booking(text, "reason")).get("reason")
        if reason:
            text = reason
        if word_count(text) == 0 or word_count(text) > 12:
            _speak(call, "Please give the reason in just a few words, like annual check-up.")
            return _end()
        call.draft["reason"] = " ".join(text.split())
        if call.verified_patient_id and not call.draft.get("email"):
            from app.services.records import get_patient

            patient = await get_patient(db, call.verified_patient_id)
            if patient and patient.email:
                call.draft["email"] = patient.email
                call.phase = "book_pref"
                _speak(call, "Do you prefer Dr. Patel, Dr. Lee, a morning, or an afternoon? You can also say no preference.")
            else:
                call.phase = "book_email"
                _speak(call, "What email should the confirmation use?")
            return _end()
        call.phase = "book_pref"
        _speak(call, "Do you prefer Dr. Patel, Dr. Lee, a morning, or an afternoon? You can also say no preference.")
        return _end()

    if call.phase == "book_pref":
        from app.providers.llm.ollama import extract_booking

        heard = await extract_booking(text, "pref")
        call.draft["doctor"] = heard.get("doctor") or doctor_preference(text)
        call.draft["part_of_day"] = heard.get("part") or day_part(text)
        await _offer(call, db)
        return _end()

    if call.phase == "book_offer":
        from app.providers.llm.ollama import extract_booking

        choice = (await extract_booking(text, "slot")).get("choice") or text
        slot = await _select_slot(db, choice, list(call.draft.get("offered_ids") or []))
        if slot is None:
            _speak(call, "Please choose one of the times I offered, such as the first one.")
            return _end()
        call.draft["slot_id"] = slot.id
        call.phase = "book_confirm"
        if call.draft.get("new_patient"):
            who = f"{call.draft['first_name']} {call.draft['last_name']}"
        else:
            who = call.verified_first_name or "you"
        email = call.draft.get("email") or ""
        when = when_spoken(slot.slot_date, slot.slot_time)
        _speak(
            call,
            f"I have {who} with {slot.doctor} on {when} for {call.draft.get('reason', 'a visit')}. "
            f"The email is {spell_email(email)}. Should I book that?",
        )
        return _end()

    if call.phase == "book_confirm":
        answer = await _answer(text)
        if answer == "no":
            call.phase = "book_offer"
            _speak(call, "Okay, I will not book that. Which other time should I use?")
            return _end()
        if answer != "yes":
            _speak(call, "Please say yes to book it, or no to pick a different time.")
            return _end()
        try:
            if call.draft.get("new_patient"):
                appointment = await book_new_patient(db, session_id=call.session_id, draft=call.draft, slot_id=call.draft["slot_id"])
            else:
                appointment = await book_existing(
                    db,
                    session_id=call.session_id,
                    email=call.draft["email"],
                    reason=call.draft["reason"],
                    slot_id=call.draft["slot_id"],
                )
        except SlotUnavailable:
            await db.rollback()
            call.phase = "book_pref"
            _speak(call, "That time was just taken. Let's pick another preference.")
            return _end()
        except AuthorizationError:
            _speak(call, "I can't book that right now.")
            return _end()
        call.draft.pop("dob", None)
        call.phase = "listen"
        when = when_spoken(appointment.slot_date, appointment.slot_time)
        _speak(call, f"You're booked with {appointment.doctor} on {when}. {ARRIVE}")
        return _end()

    _speak(call, OFF_TOPIC)
    return _end()


async def after_hours_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    text = text_ctx.get()
    db = db_ctx.get()
    if call.phase not in {"callback_name", "callback_number", "callback_reason", "callback_confirm"}:
        call.phase = "callback_name"
        call.pending = None
        _speak(call, CLOSED_ASK)
        return _end()
    if call.phase == "callback_name":
        name = parse_person_name(text)
        if name is None:
            _speak(call, "Please say the name for the callback.")
            return _end()
        call.draft["callback_name"] = name.title()
        call.phase = "callback_number"
        _speak(call, "What number should we call?")
        return _end()
    if call.phase == "callback_number":
        phone = parse_phone(text)
        if phone is None:
            _speak(call, "Please say a 10-digit callback number.")
            return _end()
        call.draft["callback_number"] = phone
        call.phase = "callback_reason"
        _speak(call, "What is the reason for the call, in a few words?")
        return _end()
    if call.phase == "callback_reason":
        if word_count(text) == 0 or word_count(text) > 12:
            _speak(call, "Please keep the reason to a few words, without medical details.")
            return _end()
        call.draft["callback_reason"] = " ".join(text.split())
        call.phase = "callback_confirm"
        _speak(
            call,
            f"I have {call.draft['callback_name']}, number {call.draft['callback_number']}, "
            f"about {call.draft['callback_reason']}. Should I save that message?",
        )
        return _end()
    answer = await _answer(text)
    if answer == "no":
        call.phase = "listen"
        call.draft.pop("callback_reason", None)
        _speak(call, "Okay, I won't save that message.")
        return _end()
    if answer != "yes":
        _speak(call, "Please say yes to save the message, or no to cancel it.")
        return _end()
    await save_callback(
        db,
        session_id=call.session_id,
        caller_name=call.draft["callback_name"],
        callback_number=call.draft["callback_number"],
        reason=call.draft["callback_reason"],
        kind="after_hours",
        patient_id=None,
    )
    name = call.draft["callback_name"]
    call.phase = "listen"
    _speak(call, CALLBACK_CLOSE.format(name=name))
    return _end()


async def handoff_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    text = text_ctx.get()
    db = db_ctx.get()
    if call.phase not in {"handoff_contact", "handoff_reason", "handoff_confirm"}:
        call.phase = "handoff_contact"
        _speak(call, "I can have a staff member follow up. Please say your name and a callback number.")
        return _end()
    if call.phase == "handoff_contact":
        from app.providers.llm.ollama import extract_contact

        heard = await extract_contact(text)
        name = heard.get("name") or parse_person_name(text)
        phone = heard.get("phone") or parse_phone(text)
        if name:
            call.draft["handoff_name"] = name.title()
        if phone:
            call.draft["handoff_number"] = phone
        if not call.draft.get("handoff_name") or not call.draft.get("handoff_number"):
            if call.draft.get("handoff_name") and not call.draft.get("handoff_number"):
                _speak(call, f"Thank you, {call.draft['handoff_name']}. What number should the staff member call?")
            elif call.draft.get("handoff_number"):
                _speak(call, "What name should I put on that callback?")
            else:
                _speak(call, "Please say your name and a callback number. You can say them one at a time.")
            return _end()
        call.phase = "handoff_reason"
        _speak(call, "What is the short reason for the callback?")
        return _end()
    if call.phase == "handoff_reason":
        if word_count(text) == 0 or word_count(text) > 12:
            _speak(call, "Please keep that to a few words.")
            return _end()
        call.draft["handoff_reason"] = " ".join(text.split())
        call.phase = "handoff_confirm"
        _speak(
            call,
            f"I have {call.draft['handoff_name']} at {call.draft['handoff_number']} about {call.draft['handoff_reason']}. "
            "Should I save that for staff?",
        )
        return _end()
    answer = await _answer(text)
    if answer != "yes":
        if answer == "no":
            call.phase = "listen"
            _speak(call, "Okay, I won't send that to staff.")
            return _end()
        _speak(call, "Please say yes to save it, or no to cancel.")
        return _end()
    await save_callback(
        db,
        session_id=call.session_id,
        caller_name=call.draft["handoff_name"],
        callback_number=call.draft["handoff_number"],
        reason=call.draft["handoff_reason"],
        kind="staff_handoff",
        patient_id=call.verified_patient_id,
    )
    call.phase = "listen"
    _speak(call, f"Thank you, {call.draft['handoff_name']}. A staff member will follow up at that number.")
    return _end()


async def advice_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    text = text_ctx.get()
    db = db_ctx.get()
    if call.phase == "advice_reason":
        if word_count(text) == 0 or word_count(text) > 12:
            _speak(call, "Please keep that note to a few words.")
            return _end()
        call.draft["doctor_note"] = " ".join(text.split())
        call.phase = "advice_phone"
        _speak(call, "What 10-digit phone number should the doctor use to reach you?")
        return _end()
    if call.phase == "advice_phone":
        digits = phone_digit_count(text)
        phone = parse_phone(text)
        if digits >= 7 and not phone:
            _speak(call, "I need a 10-digit phone number. Please say all ten digits.")
            return _end()
        if phone is None:
            _speak(call, "Please say a 10-digit phone number.")
            return _end()
        note = call.draft.get("doctor_note") or "Caller note"
        await save_doctor_message(
            db,
            session_id=call.session_id,
            patient_id=call.verified_patient_id,
            summary=note,
            phone=phone,
        )
        call.draft.pop("doctor_note", None)
        call.phase = "listen"
        _speak(
            call,
            f"I'll pass that short note to the doctor and the number {phone}. "
            "I still can't interpret it or give medical advice.",
        )
        return _end()
    if call.phase == "advice_confirm":
        answer = await _answer(text)
        if answer == "yes":
            call.phase = "advice_reason"
            _speak(call, "What short note should I pass to the doctor?")
            return _end()
        if answer == "no":
            call.phase = "listen"
            _speak(call, "Okay. I won't send a message.")
            return _end()
        _speak(call, ADVICE)
        return _end()
    call.phase = "advice_confirm"
    _speak(call, ADVICE)
    return _end()


async def off_topic_node(_state: GraphState) -> GraphState:
    call = call_ctx.get()
    call.phase = "listen"
    _speak(call, OFF_TOPIC)
    return _end()


def _edge(state: GraphState) -> str:
    return state.get("branch", "end")


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("safety", safety_node)
    graph.add_node("office", office_node)
    graph.add_node("route", route_node)
    graph.add_node("verify", verify_node)
    graph.add_node("refill", refill_node)
    graph.add_node("appointment", appointment_node)
    graph.add_node("booking", booking_node)
    graph.add_node("labs", labs_node)
    graph.add_node("billing", billing_node)
    graph.add_node("after_hours", after_hours_node)
    graph.add_node("handoff", handoff_node)
    graph.add_node("advice", advice_node)
    graph.add_node("off_topic", off_topic_node)
    graph.add_edge(START, "safety")
    destinations = {
        "office": "office",
        "route": "route",
        "verify": "verify",
        "refill": "refill",
        "appointment": "appointment",
        "booking": "booking",
        "labs": "labs",
        "billing": "billing",
        "after_hours": "after_hours",
        "handoff": "handoff",
        "advice": "advice",
        "off_topic": "off_topic",
        "end": END,
    }
    for name in ("safety", "office", "route", "verify"):
        graph.add_conditional_edges(name, _edge, destinations)
    for name in ("refill", "appointment", "booking", "labs", "billing", "after_hours", "handoff", "advice", "off_topic"):
        graph.add_edge(name, END)
    return graph.compile()


GRAPH = build_graph()
