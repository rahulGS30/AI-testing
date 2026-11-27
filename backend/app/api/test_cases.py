from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.config import settings
from app.models.test_case import TestCase, load_test_case
from app.orchestrator.run_batch import (
    create_batch_record,
    execute_batch,
    get_batch_record_path,
    new_batch_id,
    new_run_id,
    save_batch_record,
)
from app.orchestrator.test_runner import CASES_DIRECTORY
from app.personas.persona_manager import get_persona

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/test-cases", tags=["test-cases", "runs"])
runs_router = APIRouter(prefix="/api/runs", tags=["runs"])
RUN_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
PERSONA_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_ids: list[str] | None = Field(default=None, min_length=1)
    headed: bool = False


class CustomRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    website: HttpUrl
    task: str = Field(min_length=1, max_length=1000)
    persona_ids: list[str] = Field(min_length=1)
    headed: bool = False

    @field_validator("website")
    @classmethod
    def require_https_website(cls, value: HttpUrl) -> HttpUrl:
        parsed = urlsplit(str(value))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ValueError("Custom test websites must be public HTTPS URLs on port 443.")
        return value

    @field_validator("task")
    @classmethod
    def require_nonempty_task(cls, value: str) -> str:
        task = value.strip()
        if not task:
            raise ValueError("Task must not be empty.")
        return task


def _load_test_case(test_case_id: str) -> TestCase:
    try:
        return load_test_case(CASES_DIRECTORY, test_case_id)
    except FileNotFoundError:
        return load_test_case(
            settings.artifacts_dir / "custom_test_cases", test_case_id
        )


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
        "ux_analysis": f"{base_url}/ux_analysis.json",
        "token_usage": (
            f"{base_url}/token_usage.json" if artifacts.get("token_usage") else None
        ),
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
    case_run_directory = settings.artifacts_dir / test_case_id / run_id
    legacy_run_directory = settings.artifacts_dir / run_id
    if case_run_directory.is_dir():
        return case_run_directory
    if legacy_run_directory.is_dir():
        result_path = legacy_run_directory / "result.json"
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result = {}
        if result.get("test_case_id") == test_case_id:
            return legacy_run_directory
    case_directory = settings.artifacts_dir / test_case_id
    batch_directory = case_directory / "batches"
    if batch_directory.is_dir():
        for batch_path in batch_directory.glob("*.json"):
            try:
                batch = json.loads(batch_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.exception("Could not read persona batch %s", batch_path.name)
                raise HTTPException(
                    status_code=500, detail="Persona batch record could not be read."
                ) from exc
            for run in batch.get("runs", []):
                if run.get("run_id") != run_id:
                    continue
                batch_id = batch.get("batch_id")
                persona_id = run.get("persona_id")
                if (
                    not isinstance(batch_id, str)
                    or not RUN_ID_PATTERN.fullmatch(batch_id)
                    or not isinstance(persona_id, str)
                    or not PERSONA_ID_PATTERN.fullmatch(persona_id)
                ):
                    continue
                nested_directory = (
                    case_directory / batch_id / persona_id
                )
                result_path = nested_directory / "result.json"
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if (
                    result.get("run_id") == run_id
                    and result.get("test_case_id") == test_case_id
                    and result.get("persona_id") == run.get("persona_id")
                ):
                    return nested_directory
    raise HTTPException(status_code=404, detail="Run not found.")


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
    artifacts = result.get("artifacts", {})
    if "token_usage" not in artifacts and (run_directory / "token_usage.json").is_file():
        artifacts["token_usage"] = "token_usage.json"
    result["artifact_urls"] = _artifact_urls(
        test_case_id, run_id, artifacts
    )
    return result


def _queued_run(
    test_case: TestCase, persona_id: str, run_id: str, batch_id: str
) -> dict[str, Any]:
    run_directory = (
        f"artifacts\\{test_case.test_case_id}\\{batch_id}\\{persona_id}"
    )
    persona = get_persona(persona_id)
    if persona is None:
        raise ValueError(f"Unknown persona id '{persona_id}'.")
    return {
        "run_id": run_id,
        "batch_id": batch_id,
        "run_url": f"/api/test-cases/{test_case.test_case_id}/runs/{run_id}",
        "test_case_id": test_case.test_case_id,
        "name": test_case.name,
        "website": str(test_case.website),
        "persona_id": persona_id,
        "persona_name": persona.name,
        "goal": test_case.goal,
        "status": "queued",
        "completed": False,
        "step_count": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "result": None,
        "error": None,
        "analysis": {"status": "pending"},
        "artifacts": {
            "directory": run_directory,
            "screenshots": [],
            "video": None,
            "action_history": "action_history.json",
            "ux_analysis": "ux_analysis.json",
            "token_usage": "token_usage.json",
        },
        "artifact_urls": _artifact_urls(
            test_case.test_case_id, run_id, {"token_usage": "token_usage.json"}
        ),
    }


def _queue_test_runs(
    test_case: TestCase,
    persona_ids: list[str],
    headed: bool,
    background_tasks: BackgroundTasks,
    response: Response,
) -> dict[str, Any]:
    run_ids = [new_run_id() for _ in persona_ids]
    batch_id = new_batch_id()
    batch = create_batch_record(test_case, persona_ids, run_ids, batch_id)
    batch_directory = settings.artifacts_dir / test_case.test_case_id / batch_id
    queued_runs = []
    for persona_id, run_id in zip(persona_ids, run_ids, strict=True):
        run_directory = batch_directory / persona_id
        run_directory.mkdir(parents=True, exist_ok=False)
        queued_run = _queued_run(test_case, persona_id, run_id, batch_id)
        (run_directory / "result.json").write_text(
            json.dumps(queued_run, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        queued_runs.append(queued_run)
    batch["runs"] = [
        {**batch_run, "status": "queued"} for batch_run in batch["runs"]
    ]
    save_batch_record(batch, settings.artifacts_dir)
    background_tasks.add_task(
        execute_batch,
        test_case,
        batch,
        headed=headed,
        artifacts_root=settings.artifacts_dir,
    )
    response.headers["Location"] = (
        f"/api/test-cases/{test_case.test_case_id}/batches/{batch_id}"
    )
    return {
        "run_id": batch_id,
        "test_case_id": test_case.test_case_id,
        "batch_id": batch_id,
        "batch_url": (
            f"/api/test-cases/{test_case.test_case_id}/batches/{batch_id}"
        ),
        "status": "queued",
        "runs": queued_runs,
    }


@runs_router.post("/custom", status_code=202)
def create_custom_test_runs(
    request: CustomRunRequest,
    background_tasks: BackgroundTasks,
    response: Response,
) -> dict[str, Any]:
    if not settings.azure_ai_configured:
        raise HTTPException(
            status_code=503,
            detail="Azure AI Foundry must be configured before starting a test run.",
        )
    if len(set(request.persona_ids)) != len(request.persona_ids):
        raise HTTPException(status_code=422, detail="Persona IDs must be unique.")
    unknown_personas = [
        persona_id for persona_id in request.persona_ids
        if get_persona(persona_id) is None
    ]
    if unknown_personas:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown persona IDs: {', '.join(unknown_personas)}",
        )

    website = str(request.website)
    hostname = urlsplit(website).hostname
    if hostname is None:
        raise HTTPException(status_code=422, detail="Website URL must include a hostname.")

    custom_cases_directory = settings.artifacts_dir / "custom_test_cases"
    custom_cases_directory.mkdir(parents=True, exist_ok=True)
    test_case_id = f"WEB-{new_batch_id().upper()}"
    test_case = TestCase(
        test_case_id=test_case_id,
        name=f"Custom UX test for {hostname}",
        website=request.website,
        allowed_domain=hostname,
        persona_id=request.persona_ids[0],
        goal=request.task,
        completion_criteria=[request.task],
        completion_mode="agent_reported",
        product_keywords=[],
        max_steps=18,
    )
    case_path = custom_cases_directory / f"{test_case_id}.json"
    case_path.write_text(test_case.model_dump_json(indent=2), encoding="utf-8")
    return _queue_test_runs(
        test_case, request.persona_ids, request.headed, background_tasks, response
    )


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


@runs_router.get("")
def get_all_test_runs() -> list[dict[str, Any]]:
    case_ids = {
        case_path.stem for case_path in CASES_DIRECTORY.glob("*.json")
    }
    custom_cases_directory = settings.artifacts_dir / "custom_test_cases"
    if custom_cases_directory.is_dir():
        case_ids.update(
            case_path.stem for case_path in custom_cases_directory.glob("*.json")
        )
    runs = [
        run
        for test_case_id in sorted(case_ids)
        for run in get_test_case_runs(test_case_id)
    ]
    return sorted(runs, key=lambda run: run.get("started_at") or "", reverse=True)


@router.get("/{test_case_id}")
def get_test_case(test_case_id: str) -> dict[str, Any]:
    try:
        test_case = _load_test_case(test_case_id)
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
        test_case = _load_test_case(test_case_id)
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

    return _queue_test_runs(
        test_case, persona_ids, request.headed, background_tasks, response
    )


@router.get("/{test_case_id}/batches")
def get_test_case_batches(test_case_id: str) -> list[dict[str, Any]]:
    try:
        _load_test_case(test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc
    batch_directory = settings.artifacts_dir / test_case_id / "batches"
    if not batch_directory.is_dir():
        return []
    records = []
    for batch_path in batch_directory.glob("*.json"):
        if not RUN_ID_PATTERN.fullmatch(batch_path.stem):
            continue
        try:
            records.append(json.loads(batch_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Could not read persona batch %s", batch_path.name)
            raise HTTPException(
                status_code=500, detail="Persona batch record could not be read."
            ) from exc
    return sorted(
        records, key=lambda batch: batch.get("started_at") or "", reverse=True
    )


@router.get("/{test_case_id}/batches/{batch_id}")
def get_test_case_batch(test_case_id: str, batch_id: str) -> dict[str, Any]:
    try:
        _load_test_case(test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc
    if not RUN_ID_PATTERN.fullmatch(batch_id):
        raise HTTPException(status_code=404, detail="Persona batch not found.")
    batch_path = get_batch_record_path(
        test_case_id, batch_id, settings.artifacts_dir
    )
    if not batch_path.is_file():
        raise HTTPException(status_code=404, detail="Persona batch not found.")
    try:
        return json.loads(batch_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Could not read persona batch %s", batch_id)
        raise HTTPException(
            status_code=500, detail="Persona batch record could not be read."
        ) from exc


@router.get("/{test_case_id}/runs")
def get_test_case_runs(test_case_id: str) -> list[dict[str, Any]]:
    try:
        _load_test_case(test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc
    case_directory = settings.artifacts_dir / test_case_id
    legacy_directory = settings.artifacts_dir
    direct_runs = [
        _read_run(test_case_id, run_directory.name)
        for run_directory in (
            list(case_directory.iterdir()) if case_directory.is_dir() else []
        )
        if run_directory.is_dir()
        and RUN_ID_PATTERN.fullmatch(run_directory.name)
        and (run_directory / "result.json").is_file()
    ]
    nested_runs = []
    for result_path in (
        case_directory.glob("*/*/result.json") if case_directory.is_dir() else []
    ):
        if not RUN_ID_PATTERN.fullmatch(result_path.parent.parent.name):
            continue
        if not PERSONA_ID_PATTERN.fullmatch(result_path.parent.name):
            continue
        if _nested_run_matches_case(result_path, test_case_id):
            nested_runs.append(
                _read_run(test_case_id, _nested_run_id(result_path))
            )
    runs = direct_runs + nested_runs
    modern_run_ids = {run["run_id"] for run in runs}
    runs.extend(
        _read_run(test_case_id, run_directory.name)
        for run_directory in (
            legacy_directory.iterdir() if legacy_directory.is_dir() else []
        )
        if run_directory.is_dir()
        and run_directory.name not in modern_run_ids
        and RUN_ID_PATTERN.fullmatch(run_directory.name)
        and (run_directory / "result.json").is_file()
        and _legacy_run_matches_case(run_directory, test_case_id)
    )
    return sorted(runs, key=lambda run: run.get("started_at") or "", reverse=True)


def _legacy_run_matches_case(run_directory: Path, test_case_id: str) -> bool:
    try:
        result = json.loads(
            (run_directory / "result.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return result.get("test_case_id") == test_case_id


def _nested_run_matches_case(result_path: Path, test_case_id: str) -> bool:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        result.get("test_case_id") == test_case_id
        and result.get("persona_id") == result_path.parent.name
        and isinstance(result.get("run_id"), str)
        and RUN_ID_PATTERN.fullmatch(result["run_id"]) is not None
    )


def _nested_run_id(result_path: Path) -> str:
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Could not read nested run result %s", result_path)
        raise HTTPException(
            status_code=500, detail="Run result could not be read."
        ) from exc
    return result["run_id"]


@router.get("/{test_case_id}/runs/{run_id}")
def get_test_run(test_case_id: str, run_id: str) -> dict[str, Any]:
    try:
        _load_test_case(test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc
    return _read_run(test_case_id, run_id)


@router.get("/{test_case_id}/runs/{run_id}/artifacts/{artifact_path:path}")
def get_test_run_artifact(test_case_id: str, run_id: str, artifact_path: str):
    try:
        _load_test_case(test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Test case not found.") from exc
    run_directory = _case_run_directory(test_case_id, run_id).resolve()
    requested_path = (run_directory / artifact_path).resolve()
    if not requested_path.is_relative_to(run_directory) or not requested_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileResponse(requested_path)
