from __future__ import annotations

import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

from google.cloud import storage

logger = logging.getLogger(__name__)
BUCKET_NAME = "autotest-screenshot"


def _object_prefix(result: dict[str, Any]) -> str:
    parts = [
        result.get("test_case_id"),
        result.get("batch_id") or result.get("run_id"),
        result.get("persona_id"),
    ]
    if any(
        not isinstance(part, str)
        or not part
        or part in {".", ".."}
        or "/" in part
        or "\\" in part
        for part in parts
    ):
        raise ValueError("Run metadata contains an invalid cloud storage path.")
    return "/".join(parts)


def _upload_file(
    bucket: Any,
    file_path: Path,
    object_name: str,
) -> None:
    content_type, _ = mimetypes.guess_type(file_path.name)
    blob = bucket.blob(object_name)
    if content_type:
        blob.upload_from_filename(str(file_path), content_type=content_type)
    else:
        blob.upload_from_filename(str(file_path))


def _write_result(result_path: Path, result: dict[str, Any]) -> None:
    result_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def upload_run_artifacts(
    run_directory: Path,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Upload all run files and record the cloud result without failing the run."""
    prefix = _object_prefix(result)
    result_path = run_directory / "result.json"
    uploaded_files: list[str] = []
    failed_files: list[dict[str, str]] = []

    try:
        bucket = storage.Client().bucket(BUCKET_NAME)
    except Exception as exc:
        logger.exception("Could not connect to Google Cloud Storage for run %s", result.get("run_id"))
        upload = {
            "status": "failed",
            "bucket": BUCKET_NAME,
            "prefix": prefix,
            "uploaded_files": uploaded_files,
            "failed_files": [{"file": "*", "error": str(exc)}],
        }
        result["cloud_storage"] = upload
        _write_result(result_path, result)
        return upload

    run_files = sorted(
        path
        for path in run_directory.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and path != result_path
    )
    for file_path in run_files:
        relative_path = file_path.relative_to(run_directory).as_posix()
        object_name = f"{prefix}/{relative_path}"
        try:
            _upload_file(bucket, file_path, object_name)
            uploaded_files.append(f"gs://{BUCKET_NAME}/{object_name}")
        except Exception as exc:
            logger.exception(
                "Could not upload run artifact %s for run %s",
                relative_path,
                result.get("run_id"),
            )
            failed_files.append({"file": relative_path, "error": str(exc)})

    result_object = f"{prefix}/result.json"
    upload = {
        "status": "partial" if failed_files else "uploaded",
        "bucket": BUCKET_NAME,
        "prefix": prefix,
        "uploaded_files": [*uploaded_files, f"gs://{BUCKET_NAME}/{result_object}"],
        "failed_files": failed_files,
    }
    result["cloud_storage"] = upload
    _write_result(result_path, result)

    try:
        _upload_file(bucket, result_path, result_object)
    except Exception as exc:
        logger.exception("Could not upload result.json for run %s", result.get("run_id"))
        upload["uploaded_files"].remove(f"gs://{BUCKET_NAME}/{result_object}")
        failed_files.append({"file": "result.json", "error": str(exc)})
        upload["status"] = "partial" if uploaded_files else "failed"
        _write_result(result_path, result)

    return upload
