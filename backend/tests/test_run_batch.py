import json

from app.config import PROJECT_ROOT
from app.models.test_case import load_test_case
from app.orchestrator import run_batch


def test_batch_manifest_links_each_persona_to_its_run_artifacts(
    tmp_path, monkeypatch
) -> None:
    test_case = load_test_case(PROJECT_ROOT / "test_cases", "TC-003")
    persona_ids = ["elderly_user", "low_vision_user"]
    run_ids = ["20261128T000000Z-aabbccdd", "20261128T000001Z-eeff0011"]
    batch = run_batch.create_batch_record(
        test_case, persona_ids, run_ids, "20261128T000000Z-11223344"
    )
    run_calls = []

    def fake_run_test_case(
        persona_case,
        *,
        headless,
        run_directory,
        run_id,
        batch_id,
    ):
        run_calls.append(
            (persona_case.persona_id, headless, run_directory, run_id, batch_id)
        )
        return {
            "run_id": run_id,
            "batch_id": batch_id,
            "test_case_id": test_case.test_case_id,
            "persona_id": persona_case.persona_id,
            "status": (
                "blocked" if persona_case.persona_id == "low_vision_user" else "completed"
            ),
            "completed": persona_case.persona_id == "elderly_user",
            "result": "result",
            "error": None,
            "analysis": {
                "status": "completed",
                "summary": f"Analysis for {persona_case.persona_id}",
                "strengths": [{"observation": "strength"}],
                "improvements": [{"observation": "improvement"}],
            },
            "artifacts": {
                "directory": f"artifacts\\TC-003\\{batch_id}\\{persona_case.persona_id}",
                "screenshots": ["screenshots/step_000.png", "screenshots/step_001.png"],
                "video": "video/journey.webm",
                "action_history": "action_history.json",
                "ux_analysis": "ux_analysis.json",
            },
        }

    monkeypatch.setattr(run_batch, "run_test_case", fake_run_test_case)

    completed = run_batch.execute_batch(
        test_case, batch, headed=False, artifacts_root=tmp_path
    )

    assert completed["status"] == "finished_with_issues"
    assert completed["finished_count"] == 2
    assert completed["succeeded_count"] == 1
    assert completed["failed_count"] == 0
    assert run_calls == [
        (
            persona_ids[0],
            True,
            tmp_path / "TC-003" / batch["batch_id"] / persona_ids[0],
            run_ids[0],
            batch["batch_id"],
        ),
        (
            persona_ids[1],
            True,
            tmp_path / "TC-003" / batch["batch_id"] / persona_ids[1],
            run_ids[1],
            batch["batch_id"],
        ),
    ]
    for run in completed["runs"]:
        assert run["analysis_status"] == "completed"
        assert run["improvement_count"] == 1
        assert len(run["artifact_urls"]["screenshots"]) == 2
        assert run["artifact_urls"]["video"].endswith("/video/journey.webm")

    manifest_path = (
        tmp_path
        / "TC-003"
        / "batches"
        / f"{batch['batch_id']}.json"
    )
    assert manifest_path.is_file()
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [run["persona_id"] for run in persisted["runs"]] == persona_ids
    assert persisted["runs"][1]["status"] == "blocked"
    assert persisted["artifact_directory"] == f"artifacts\\TC-003\\{batch['batch_id']}"
    assert persisted["run_id"] == persisted["batch_id"]
    assert [
        run["artifacts"]["directory"] for run in persisted["runs"]
    ] == [
        f"artifacts\\TC-003\\{batch['batch_id']}\\{persona_id}"
        for persona_id in persona_ids
    ]
