import json
from types import SimpleNamespace

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


def test_test_case_api_lists_cases_and_returns_case_details() -> None:
    response = client.get("/api/test-cases")

    assert response.status_code == 200
    cases = response.json()
    assert {"TC-002", "TC-003"} <= {case["test_case_id"] for case in cases}

    detail = client.get("/api/test-cases/TC-003")
    assert detail.status_code == 200
    assert detail.json()["persona_id"] == "first_time_user"


def test_run_api_queues_persona_runs_in_case_scoped_directories(
    tmp_path, monkeypatch
) -> None:
    from app.api import test_cases

    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(azure_ai_configured=True, artifacts_dir=tmp_path),
    )
    scheduled = []
    monkeypatch.setattr(
        test_cases,
        "_run_batch",
        lambda test_case, personas, run_ids, headed: scheduled.append(
            (test_case.test_case_id, personas, run_ids, headed)
        ),
    )

    response = client.post(
        "/api/test-cases/TC-003/runs",
        json={
            "persona_ids": ["elderly_user", "low_vision_user"],
            "headed": True,
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert [run["persona_id"] for run in body["runs"]] == [
        "elderly_user",
        "low_vision_user",
    ]
    assert body["runs"][0]["run_url"].endswith(body["runs"][0]["run_id"])
    assert body["runs"][0]["artifact_urls"]["result"].endswith("/artifacts/result.json")
    assert len(scheduled) == 1
    case_id, personas, run_ids, headed = scheduled[0]
    assert case_id == "TC-003"
    assert personas == ["elderly_user", "low_vision_user"]
    assert headed is True
    assert response.headers["location"].endswith(f"/runs/{run_ids[0]}")

    for run in body["runs"]:
        run_path = tmp_path / "TC-003" / run["run_id"] / "result.json"
        assert run_path.is_file()
        assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "queued"

    listed_runs = client.get("/api/test-cases/TC-003/runs")
    assert listed_runs.status_code == 200
    assert {run["run_id"] for run in listed_runs.json()} == set(run_ids)


def test_run_api_exposes_structured_result_and_artifacts(
    tmp_path, monkeypatch
) -> None:
    from app.api import test_cases

    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(azure_ai_configured=True, artifacts_dir=tmp_path),
    )
    monkeypatch.setattr(test_cases, "_run_batch", lambda *args: None)
    queued = client.post(
        "/api/test-cases/TC-003/runs",
        json={"persona_ids": ["low_vision_user"]},
    ).json()["runs"][0]
    run_directory = tmp_path / "TC-003" / queued["run_id"]
    (run_directory / "screenshots").mkdir()
    (run_directory / "screenshots" / "step_000.png").write_bytes(b"image")
    (run_directory / "video").mkdir()
    (run_directory / "video" / "journey.webm").write_bytes(b"video")
    result = {
        **queued,
        "status": "completed",
        "completed": True,
        "artifacts": {
            **queued["artifacts"],
            "screenshots": ["screenshots/step_000.png"],
            "video": "video/journey.webm",
        },
    }
    (run_directory / "result.json").write_text(
        json.dumps(result), encoding="utf-8"
    )
    (run_directory / "action_history.json").write_text(
        json.dumps({"actions": []}), encoding="utf-8"
    )

    detail = client.get(
        f"/api/test-cases/TC-003/runs/{queued['run_id']}"
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "completed"
    assert body["artifact_urls"]["screenshots"] == [
        f"/api/test-cases/TC-003/runs/{queued['run_id']}/artifacts/screenshots/step_000.png"
    ]

    image = client.get(body["artifact_urls"]["screenshots"][0])
    assert image.status_code == 200
    assert image.content == b"image"
    video = client.get(body["artifact_urls"]["video"])
    assert video.status_code == 200
    assert video.content == b"video"


def test_run_api_validates_case_and_persona(monkeypatch) -> None:
    from app.api import test_cases

    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(azure_ai_configured=True),
    )
    missing_case = client.get("/api/test-cases/TC-404")
    assert missing_case.status_code == 404

    unknown_persona = client.post(
        "/api/test-cases/TC-003/runs",
        json={"persona_ids": ["not_a_persona"]},
    )
    assert unknown_persona.status_code == 422


def test_run_api_requires_azure_configuration(monkeypatch) -> None:
    from app.api import test_cases

    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(azure_ai_configured=False),
    )
    response = client.post(
        "/api/test-cases/TC-003/runs",
        json={"persona_ids": ["elderly_user"]},
    )

    assert response.status_code == 503
