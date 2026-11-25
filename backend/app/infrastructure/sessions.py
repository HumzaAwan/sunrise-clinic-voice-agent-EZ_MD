"""In-process call directory. Scrubbed fields are what the API is allowed to return."""

from app.agent.state import CallState


class SessionDirectory:
    def __init__(self) -> None:
        self.live: dict[str, CallState] = {}

    def create(self) -> CallState:
        state = CallState()
        self.live[state.session_id] = state
        return state

    def drop(self, session_id: str) -> None:
        self.live.pop(session_id, None)


directory = SessionDirectory()
