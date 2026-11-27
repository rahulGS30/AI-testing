from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_does_not_expose_secrets() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "azure_ai_ready" in body["configuration"]
    assert "api_key" not in response.text.lower()


def test_personas_include_expected_initial_set() -> None:
    response = client.get("/api/personas")

    assert response.status_code == 200
    personas = response.json()
    persona_ids = {persona["persona_id"] for persona in personas}
    assert {
        "first_time_user",
        "elderly_user",
        "low_vision_user",
        "non_native_english_speaker",
        "impatient_power_user",
    } <= persona_ids


def test_llm_health_reports_missing_configuration(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.api import health

    monkeypatch.setattr(health, "settings", SimpleNamespace(azure_ai_configured=False))
    response = client.get("/api/health/llm")

    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]
