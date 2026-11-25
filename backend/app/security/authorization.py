"""Session tickets. Chart tools require a ticket issued by verification."""

from dataclasses import dataclass


class AuthorizationError(Exception):
    def __init__(self) -> None:
        super().__init__("not authorized")


@dataclass(frozen=True)
class Ticket:
    session_id: str
    patient_id: str


_tickets: dict[str, Ticket] = {}


def issue_ticket(session_id: str, patient_id: str) -> Ticket:
    ticket = Ticket(session_id=session_id, patient_id=patient_id)
    _tickets[session_id] = ticket
    return ticket


def require_ticket(session_id: str) -> Ticket:
    ticket = _tickets.get(session_id)
    if ticket is None:
        raise AuthorizationError()
    return ticket


def clear_ticket(session_id: str) -> None:
    _tickets.pop(session_id, None)


def reset_tickets() -> None:
    _tickets.clear()
