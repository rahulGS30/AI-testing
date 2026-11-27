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


def test_run_api_queues_persona_runs_under_one_batch_with_persona_directories(
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
        "execute_batch",
        lambda test_case, batch, *, headed, artifacts_root: scheduled.append(
            (test_case.test_case_id, [run["persona_id"] for run in batch["runs"]], [
                run["run_id"] for run in batch["runs"]
            ], headed, artifacts_root)
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
    assert body["batch_id"]
    assert body["run_id"] == body["batch_id"]
    assert body["batch_url"].endswith(body["batch_id"])
    assert [run["persona_id"] for run in body["runs"]] == [
        "elderly_user",
        "low_vision_user",
    ]
    assert body["runs"][0]["run_url"].endswith(body["runs"][0]["run_id"])
    assert body["runs"][0]["artifact_urls"]["result"].endswith("/artifacts/result.json")
    assert len(scheduled) == 1
    case_id, personas, run_ids, headed, artifacts_root = scheduled[0]
    assert case_id == "TC-003"
    assert personas == ["elderly_user", "low_vision_user"]
    assert headed is True
    assert artifacts_root == tmp_path
    assert response.headers["location"].endswith(f"/batches/{body['batch_id']}")

    for run in body["runs"]:
        run_path = (
            tmp_path
            / "TC-003"
            / body["batch_id"]
            / run["persona_id"]
            / "result.json"
        )
        assert run_path.is_file()
        queued_result = json.loads(run_path.read_text(encoding="utf-8"))
        assert queued_result["status"] == "queued"
        assert queued_result["persona_id"] == run["persona_id"]
        assert queued_result["persona_name"] == run["persona_name"]
        assert queued_result["batch_id"] == body["batch_id"]
        assert queued_result["artifacts"]["directory"].endswith(
            f"{body['batch_id']}\\{run['persona_id']}"
        )

    listed_runs = client.get("/api/test-cases/TC-003/runs")
    assert listed_runs.status_code == 200
    assert {run["run_id"] for run in listed_runs.json()} == set(run_ids)
    batch_record = client.get(body["batch_url"])
    assert batch_record.status_code == 200
    assert batch_record.json()["batch_id"] == body["batch_id"]


def test_custom_run_api_persists_case_and_lists_run(tmp_path, monkeypatch) -> None:
    from app.api import test_cases

    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(azure_ai_configured=True, artifacts_dir=tmp_path),
    )
    monkeypatch.setattr(
        test_cases,
        "execute_batch",
        lambda test_case, batch, *, headed, artifacts_root: None,
    )

    response = client.post(
        "/api/runs/custom",
        json={
            "website": "https://example.com/",
            "task": "Find the help page.",
            "persona_ids": ["first_time_user"],
        },
    )

    assert response.status_code == 202
    body = response.json()
    custom_case_id = body["test_case_id"]
    assert custom_case_id.startswith("WEB-")
    assert (tmp_path / "custom_test_cases" / f"{custom_case_id}.json").is_file()
    assert body["runs"][0]["goal"] == "Find the help page."
    listed_runs = client.get("/api/runs")
    assert listed_runs.status_code == 200
    assert any(run["run_id"] == body["runs"][0]["run_id"] for run in listed_runs.json())


def test_custom_run_api_rejects_non_https_website() -> None:
    response = client.post(
        "/api/runs/custom",
        json={
            "website": "http://example.com/",
            "task": "Find the help page.",
            "persona_ids": ["first_time_user"],
        },
    )

    assert response.status_code == 422


def test_all_runs_endpoint_handles_missing_artifacts_directory(
    tmp_path, monkeypatch
) -> None:
    from app.api import test_cases

    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(artifacts_dir=tmp_path / "not-created"),
    )

    response = client.get("/api/runs")

    assert response.status_code == 200
    assert response.json() == []


def test_run_api_exposes_structured_result_and_artifacts(
    tmp_path, monkeypatch
) -> None:
    from app.api import test_cases

    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(azure_ai_configured=True, artifacts_dir=tmp_path),
    )
    monkeypatch.setattr(
        test_cases,
        "execute_batch",
        lambda test_case, batch, *, headed, artifacts_root: None,
    )
    queued = client.post(
        "/api/test-cases/TC-003/runs",
        json={"persona_ids": ["low_vision_user"]},
    ).json()["runs"][0]
    run_directory = (
        tmp_path
        / "TC-003"
        / queued["batch_id"]
        / queued["persona_id"]
    )
    (run_directory / "screenshots").mkdir()
    (run_directory / "screenshots" / "step_000.png").write_bytes(b"image")
    (run_directory / "video").mkdir()
    (run_directory / "video" / "journey.webm").write_bytes(b"video")
    (run_directory / "token_usage.json").write_text(
        json.dumps({"totals": {"total_tokens": 40}}),
        encoding="utf-8",
    )
    result = {
        **queued,
        "status": "completed",
        "completed": True,
        "artifacts": {
            **queued["artifacts"],
            "screenshots": ["screenshots/step_000.png"],
            "video": "video/journey.webm",
            "token_usage": "token_usage.json",
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
    assert body["artifact_urls"]["ux_analysis"].endswith(
        "/artifacts/ux_analysis.json"
    )

    image = client.get(body["artifact_urls"]["screenshots"][0])
    assert image.status_code == 200
    assert image.content == b"image"
    video = client.get(body["artifact_urls"]["video"])
    assert video.status_code == 200
    assert video.content == b"video"
    token_usage = client.get(body["artifact_urls"]["token_usage"])
    assert token_usage.status_code == 200
    assert token_usage.json()["totals"]["total_tokens"] == 40

    listed_runs = client.get("/api/test-cases/TC-003/runs")
    assert listed_runs.status_code == 200
    assert any(run["run_id"] == queued["run_id"] for run in listed_runs.json())


def test_run_api_validates_case_and_persona(monkeypatch, tmp_path) -> None:
    from app.api import test_cases

    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(azure_ai_configured=True, artifacts_dir=tmp_path),
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


def test_run_api_lists_and_serves_legacy_artifact_directories(
    tmp_path, monkeypatch
) -> None:
    from app.api import test_cases

    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(artifacts_dir=tmp_path),
    )
    run_id = "20261127T175603Z-db2477f0"
    run_directory = tmp_path / run_id
    (run_directory / "screenshots").mkdir(parents=True)
    (run_directory / "screenshots" / "step_000.png").write_bytes(b"legacy-image")
    (run_directory / "result.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "test_case_id": "TC-003",
                "persona_id": "elderly_user",
                "status": "completed",
                "artifacts": {
                    "screenshots": ["screenshots\\step_000.png"],
                    "video": None,
                    "action_history": "action_history.json",
                },
            }
        ),
        encoding="utf-8",
    )

    listed = client.get("/api/test-cases/TC-003/runs")
    assert listed.status_code == 200
    legacy_run = next(run for run in listed.json() if run["run_id"] == run_id)
    image_url = legacy_run["artifact_urls"]["screenshots"][0]
    image = client.get(image_url)
    assert image.status_code == 200
    assert image.content == b"legacy-image"
