"""Many call records stay isolated. This does not start a model for each one."""

from app.agent.state import CallState
from app.infrastructure.sessions import SessionDirectory


def test_one_hundred_sessions_do_not_share_patient_context():
    directory = SessionDirectory()
    states = [directory.create() for _ in range(100)]
    for index, state in enumerate(states):
        state.verified_patient_id = f"patient-{index}"
        state.draft["dob"] = f"1980-01-{(index % 28) + 1:02d}"
    for index, state in enumerate(states):
        public = state.scrubbed()
        assert public["session_id"] == state.session_id
        assert "dob" not in public
        assert state.verified_patient_id == f"patient-{index}"
        assert state.draft["dob"] != states[(index + 1) % 100].draft["dob"] or index % 28 == (index + 1) % 28
    assert len({state.session_id for state in states}) == 100
