from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.models.test_case import TestCase
from app.storage.gcs_artifacts import upload_run_artifacts
from app.orchestrator.test_runner import run_test_case
from app.personas.persona_manager import get_persona


def new_batch_id() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )


def new_run_id() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )


def create_batch_record(
    test_case: TestCase,
    persona_ids: list[str],
    run_ids: list[str],
    batch_id: str,
) -> dict[str, Any]:
    runs = []
    for persona_id, run_id in zip(persona_ids, run_ids, strict=True):
        persona = get_persona(persona_id)
        persona_directory = f"{batch_id}/{persona_id}"
        runs.append(
            {
                "run_id": run_id,
                "run_url": f"/api/test-cases/{test_case.test_case_id}/runs/{run_id}",
                "persona_id": persona_id,
                "persona_name": persona.name if persona else persona_id,
                "artifact_directory": (
                    f"artifacts\\{test_case.test_case_id}\\{persona_directory}"
                ),
                "status": "queued",
                "analysis_status": "pending",
                "artifacts": {
                    "directory": (
                        f"artifacts\\{test_case.test_case_id}\\{persona_directory}"
                    ),
                    "screenshots": [],
                    "video": None,
                    "action_history": "action_history.json",
                    "ux_analysis": "ux_analysis.json",
                    "token_usage": "token_usage.json",
                },
                "artifact_urls": {
                    "run": (
                        f"/api/test-cases/{test_case.test_case_id}/runs/{run_id}"
                    ),
                    "result": (
                        f"/api/test-cases/{test_case.test_case_id}/runs/{run_id}"
                        "/artifacts/result.json"
                    ),
                    "action_history": (
                        f"/api/test-cases/{test_case.test_case_id}/runs/{run_id}"
                        "/artifacts/action_history.json"
                    ),
                    "ux_analysis": (
                        f"/api/test-cases/{test_case.test_case_id}/runs/{run_id}"
                        "/artifacts/ux_analysis.json"
                    ),
                    "token_usage": (
                        f"/api/test-cases/{test_case.test_case_id}/runs/{run_id}"
                        "/artifacts/token_usage.json"
                    ),
                    "screenshots": [],
                    "video": None,
                },
            }
        )
    return {
        "run_id": batch_id,
        "batch_id": batch_id,
        "test_case_id": test_case.test_case_id,
        "artifact_directory": f"artifacts\\{test_case.test_case_id}\\{batch_id}",
        "status": "queued",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "persona_count": len(persona_ids),
        "finished_count": 0,
        "succeeded_count": 0,
        "failed_count": 0,
        "runs": runs,
    }


def _batch_path(
    test_case_id: str, batch_id: str, artifacts_root: Path | None = None
) -> Path:
    root = artifacts_root or settings.artifacts_dir
    return root / test_case_id / "batches" / f"{batch_id}.json"


def save_batch_record(
    batch: dict[str, Any], artifacts_root: Path | None = None
) -> Path:
    path = _batch_path(batch["test_case_id"], batch["batch_id"], artifacts_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(batch, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def get_batch_record_path(
    test_case_id: str, batch_id: str, artifacts_root: Path | None = None
) -> Path:
    return _batch_path(test_case_id, batch_id, artifacts_root)


def execute_batch(
    test_case: TestCase,
    batch: dict[str, Any],
    *,
    headed: bool,
    artifacts_root: Path | None = None,
) -> dict[str, Any]:
    batch["status"] = "running"
    save_batch_record(batch, artifacts_root)
    case_artifacts_directory = (
        (artifacts_root or settings.artifacts_dir) / test_case.test_case_id
    )
    batch_directory = case_artifacts_directory / batch["batch_id"]
    batch_directory.mkdir(parents=True, exist_ok=True)

    for run in batch["runs"]:
        persona_case = test_case.model_copy(update={"persona_id": run["persona_id"]})
        run_directory = batch_directory / run["persona_id"]
        try:
            result = run_test_case(
                persona_case,
                headless=not headed,
                run_directory=run_directory,
                run_id=run["run_id"],
                batch_id=batch["batch_id"],
            )
        except Exception as exc:
            result = {
                "run_id": run["run_id"],
                "test_case_id": test_case.test_case_id,
                "persona_id": run["persona_id"],
                "batch_id": batch["batch_id"],
                "status": "failed",
                "completed": False,
                "result": None,
                "error": "Run could not be started.",
                "analysis": {
                    "status": "unavailable",
                    "error": "Analysis was skipped because the run did not start.",
                },
                "artifacts": run["artifacts"],
            }
            run_directory.mkdir(parents=True, exist_ok=True)
            (run_directory / "result.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            (run_directory / "ux_analysis.json").write_text(
                json.dumps(result["analysis"], indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            result["startup_error_type"] = type(exc).__name__
            upload_run_artifacts(run_directory, result)

        run["status"] = result["status"]
        run["analysis_status"] = result.get("analysis", {}).get("status", "unavailable")
        run["analysis_summary"] = result.get("analysis", {}).get("summary")
        run["strength_count"] = len(result.get("analysis", {}).get("strengths", []))
        run["improvement_count"] = len(
            result.get("analysis", {}).get("improvements", [])
        )
        run["completed"] = result.get("completed", False)
        run["result"] = result.get("result")
        run["error"] = result.get("error")
        run["artifacts"] = result.get("artifacts", run["artifacts"])
        run["cloud_storage"] = result.get("cloud_storage")
        run["artifact_urls"]["screenshots"] = [
            f"/api/test-cases/{test_case.test_case_id}/runs/{run['run_id']}/artifacts/"
            + path.replace("\\", "/")
            for path in run["artifacts"].get("screenshots", [])
        ]
        if run["artifacts"].get("video"):
            run["artifact_urls"]["video"] = (
                f"/api/test-cases/{test_case.test_case_id}/runs/{run['run_id']}"
                "/artifacts/video/journey.webm"
            )
        if run["artifacts"].get("token_usage"):
            run["artifact_urls"]["token_usage"] = (
                f"/api/test-cases/{test_case.test_case_id}/runs/{run['run_id']}"
                "/artifacts/token_usage.json"
            )
        batch["finished_count"] = sum(
            item["status"] in {"completed", "blocked", "incomplete", "max_steps", "failed"}
            for item in batch["runs"]
        )
        batch["succeeded_count"] = sum(
            item["status"] == "completed" for item in batch["runs"]
        )
        batch["failed_count"] = sum(
            item["status"] == "failed" for item in batch["runs"]
        )
        batch["status"] = "running"
        save_batch_record(batch, artifacts_root)

    batch["status"] = (
        "completed"
        if all(run["status"] == "completed" for run in batch["runs"])
        else "finished_with_issues"
    )
    batch["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_batch_record(batch, artifacts_root)
    return batch
