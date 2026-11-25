"""Fixed safety lines. These are spoken as written so the model cannot soften them."""

GREETING_OPEN = (
    "Thank you for calling Sunrise Family Clinic. This call may be recorded. How can I help you today?"
)
NOTICE = "This call may be recorded."
VERIFY_FAIL = "I'm sorry, I couldn't verify those details."
VERIFY_ASK = (
    "Please say your full name, your date of birth, and the last four digits of your social security number."
)
VERIFY_STOP = (
    "I'm sorry, I couldn't verify those details. I can't try verification again on this call. "
    "I can have a staff member follow up. Please say your name and a callback number."
)
EMERGENCY = (
    "This sounds like an emergency. Please hang up and call nine-one-one, or go to the nearest emergency room."
)
ADVICE = (
    "I can't diagnose, recommend a medicine, change a dose, or interpret results. "
    "I can send a short message to the doctor instead. Would you like me to do that?"
)
THIRD_PARTY = "I can only speak with the patient about their own care. I can't share someone else's information."
INJECTION = (
    "I can't change my rules or read out clinic records that way. "
    "I can help the caller with their own appointment, refill, lab status, or billing."
)
OFF_TOPIC = "I can help with appointments, prescription refills, lab status, and billing for Sunrise Family Clinic."
CLOSED = (
    "The office is closed right now. Our hours are Monday through Friday, 9:00 AM to 5:00 PM Eastern Time. "
    "If this is an emergency, please hang up and call nine-one-one."
)
CLOSED_ASK = CLOSED + " I can take a message for the next business day. Please say your name."
FIRST_VISIT = "Is this your first visit with Sunrise Family Clinic?"
ARRIVE = (
    "Please arrive 15 minutes early and bring a photo ID and your insurance card. "
    "You'll receive a confirmation by email."
)
CALLBACK_CLOSE = (
    "Thank you, {name}. Your message has been recorded, and our team will get back to you on the next business day. "
    "If this is an emergency, please hang up and call nine-one-one. Have a good night."
)


def with_notice(delivered: bool, text: str) -> tuple[bool, str]:
    if delivered:
        return True, text
    if "recorded" in text.lower():
        return True, text
    return True, f"{NOTICE} {text}"
