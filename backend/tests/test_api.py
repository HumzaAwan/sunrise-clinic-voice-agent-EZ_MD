"""The local API boots and does not expose verification secrets."""

from fastapi.testclient import TestClient

from app.main import app


def test_overview_hides_verification_secrets():
    with TestClient(app) as client:
        health = client.get("/api/health")
        overview = client.get("/api/overview")
        assert health.status_code == 200
        assert overview.status_code == 200
        body = overview.text
        assert "3344" not in body
        assert "1020" not in body
        assert "9081" not in body
        assert "000-" not in body
        assert "1985-03-14" not in body
        assert "ssn" not in body.lower()
        names = {row["name"] for row in overview.json()["patients"]}
        assert names == {"Sarah Johnson", "Michael Brown", "Maria Garcia"}
