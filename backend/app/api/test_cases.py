from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.models.test_case import TestCase, load_test_case
from app.orchestrator.test_runner import CASES_DIRECTORY, run_test_case
from app.personas.persona_manager import get_persona

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/test-cases", tags=["test-cases", "runs"])
RUN_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_ids: list[str] | None = Field(default=None, min_length=1)
    headed: bool = False


def _case_summary(test_case: TestCase) -> dict[str, Any]:
    return test_case.model_dump(mode="json")


def _url_path(path: str) -> str:
    return path.replace("\\", "/")


def _artifact_urls(
    test_case_id: str, run_id: str, artifacts: dict[str, Any]
) -> dict[str, Any]:
    base_url = f"/api/test-cases/{test_case_id}/runs/{run_id}/artifacts"
    return {
        "result": f"{base_url}/result.json",
        "action_history": f"{base_url}/action_history.json",
        "video": (
            f"{base_url}/video/journey.webm" if artifacts.get("video") else None
        ),
        "screenshots": [
            f"{base_url}/{_url_path(path)}"
            for path in artifacts.get("screenshots", [])
        ],
    }


def _case_run_directory(test_case_id: str, run_id: str) -> Path:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="Run not found.")
    return settings.artifacts_dir / test_case_id / run_id


def _read_run(test_case_id: str, run_id: str) -> dict[str, Any]:
    run_directory = _case_run_directory(test_case_id, run_id)
    result_path = run_directory / "result.json"
    if not result_path.is_file():
        raise HTTPException(status_code=404, detail="Run not found.")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Could not read run result %s/%s", test_case_id, run_id)
        raise HTTPException(status_code=500, detail="Run result could not be read.") from exc
    result["run_url"] = f"/api/test-cases/{test_case_id}/runs/{run_id}"
    result["artifact_urls"] = _artifact_urls(
        test_case_id, run_id, result.get("artifacts", {})
    )
    return result


def _queued_run(
    test_case: TestCase, persona_id: str, run_id: str
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "run_url": f"/api/test-cases/{test_case.test_case_id}/runs/{run_id}",
        "test_case_id": test_case.test_case_id,
        "name": test_case.name,
        "website": str(test_case.website),
        "persona_id": persona_id,
        "goal": test_case.goal,
        "status": "queued",
        "completed": False,
        "step_count": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "result": None,
        "error": None,
        "artifacts": {
            "directory": f"{test_case.test_case_id}/{run_id}",
            "screenshots": [],
            "video": None,
            "action_history": "action_history.json",
        },
        "artifact_urls": _artifact_urls(test_case.test_case_id, run_id, {}),
    }


def _run_batch(
    test_case: TestCase,
    personas: list[str],
    run_ids: list[str],
    headed: bool,
) -> None:
    case_artifacts_directory = settings.artifacts_dir / test_case.test_case_id
    for persona_id, run_id in zip(personas, run_ids, strict=True):
        persona_case = test_case.model_copy(update={"persona_id": persona_id})
        try:
            run_test_case(
                persona_case,
                headless=not headed,
                artifacts_directory=case_artifacts_directory,
                run_id=run_id,
            )
        except Exception as exc:
            logger.exception("Queued run %s failed before runner completion", run_id)
            run_directory = case_artifacts_directory / run_id
            failed_result = _queued_run(test_case, persona_id, run_id)
            failed_result["status"] = "failed"
            failed_result["error"] = "Run could not be started."
            failed_result["finished_at"] = datetime.now(timezone.utc).isoformat()
            run_directory.mkdir(parents=True, exist_ok=True)
            (run_directory / "result.json").write_text(
                json.dumps(failed_result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.error("Run startup failure type: %s", type(exc).__name__)


@router.get("")
def get_test_cases() -> list[dict[str, Any]]:
    cases = []
    for case_path in sorted(CASES_DIRECTORY.glob("*.json")):
        cases.append(
            _case_summary(
                TestCase.model_validate_json(case_path.read_text(encoding="utf-8"))
            )
        )
    return cases


@router.get("/{test_case_id}")
def get_test_case(test_case_id: str) -> dict[str, Any]:
    try:
        test_case = load_test_case(CASES_DIRECTORY, test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc
    return _case_summary(test_case)


@router.post("/{test_case_id}/runs", status_code=202)
def create_test_runs(
    test_case_id: str,
    request: RunRequest,
    background_tasks: BackgroundTasks,
    response: Response,
) -> dict[str, Any]:
    try:
        test_case = load_test_case(CASES_DIRECTORY, test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc

    persona_ids = request.persona_ids or [test_case.persona_id]
    if len(set(persona_ids)) != len(persona_ids):
        raise HTTPException(status_code=422, detail="Persona IDs must be unique.")
    unknown_personas = [
        persona_id for persona_id in persona_ids if get_persona(persona_id) is None
    ]
    if unknown_personas:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown persona IDs: {', '.join(unknown_personas)}",
        )
    if not settings.azure_ai_configured:
        raise HTTPException(
            status_code=503,
            detail="Azure AI Foundry must be configured before starting a test run.",
        )

    run_ids = [
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
        for _ in persona_ids
    ]
    case_artifacts_directory = settings.artifacts_dir / test_case.test_case_id
    queued_runs = []
    for persona_id, run_id in zip(persona_ids, run_ids, strict=True):
        run_directory = case_artifacts_directory / run_id
        run_directory.mkdir(parents=True, exist_ok=False)
        queued_run = _queued_run(test_case, persona_id, run_id)
        (run_directory / "result.json").write_text(
            json.dumps(queued_run, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        queued_runs.append(queued_run)

    background_tasks.add_task(
        _run_batch, test_case, persona_ids, run_ids, request.headed
    )
    response.headers["Location"] = (
        f"/api/test-cases/{test_case_id}/runs/{run_ids[0]}"
    )
    return {
        "test_case_id": test_case_id,
        "status": "queued",
        "runs": queued_runs,
    }


@router.get("/{test_case_id}/runs")
def get_test_case_runs(test_case_id: str) -> list[dict[str, Any]]:
    try:
        load_test_case(CASES_DIRECTORY, test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc
    case_directory = settings.artifacts_dir / test_case_id
    if not case_directory.is_dir():
        return []
    runs = [
        _read_run(test_case_id, run_directory.name)
        for run_directory in case_directory.iterdir()
        if run_directory.is_dir()
        and RUN_ID_PATTERN.fullmatch(run_directory.name)
        and (run_directory / "result.json").is_file()
    ]
    return sorted(runs, key=lambda run: run.get("started_at") or "", reverse=True)


@router.get("/{test_case_id}/runs/{run_id}")
def get_test_run(test_case_id: str, run_id: str) -> dict[str, Any]:
    try:
        load_test_case(CASES_DIRECTORY, test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc
    return _read_run(test_case_id, run_id)


@router.get("/{test_case_id}/runs/{run_id}/artifacts/{artifact_path:path}")
def get_test_run_artifact(test_case_id: str, run_id: str, artifact_path: str):
    try:
        load_test_case(CASES_DIRECTORY, test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc
    run_directory = _case_run_directory(test_case_id, run_id).resolve()
    requested_path = (run_directory / artifact_path).resolve()
    if not requested_path.is_relative_to(run_directory) or not requested_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileResponse(requested_path)
