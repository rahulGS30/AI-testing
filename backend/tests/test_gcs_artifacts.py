import json
from pathlib import Path

import pytest

from app.storage import gcs_artifacts


class FakeBlob:
    def __init__(self, name: str, uploaded: dict[str, bytes], fail_on: str | None):
        self.name = name
        self.uploaded = uploaded
        self.fail_on = fail_on

    def upload_from_filename(self, filename: str, content_type: str | None = None):
        if self.name == self.fail_on:
            raise RuntimeError("simulated upload failure")
        self.uploaded[self.name] = Path(filename).read_bytes()


class FakeBucket:
    def __init__(self, uploaded: dict[str, bytes], fail_on: str | None = None):
        self.uploaded = uploaded
        self.fail_on = fail_on

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self.uploaded, self.fail_on)


class FakeClient:
    def __init__(self, bucket: FakeBucket):
        self.fake_bucket = bucket

    def bucket(self, name: str) -> FakeBucket:
        assert name == "autotest-screenshot"
        return self.fake_bucket


@pytest.mark.parametrize("run_status", ["completed", "failed"])
def test_uploads_run_artifacts_for_successful_and_failed_runs(
    tmp_path, monkeypatch, run_status
) -> None:
    run_directory = tmp_path / "TC-003" / "batch-001" / "elderly_user"
    run_directory.mkdir(parents=True)
    (run_directory / "result.json").write_text("{}", encoding="utf-8")
    (run_directory / "ux_analysis.json").write_text("{}", encoding="utf-8")
    screenshot_directory = run_directory / "screenshots"
    screenshot_directory.mkdir()
    (screenshot_directory / "step_000.png").write_bytes(b"image")

    uploaded: dict[str, bytes] = {}
    fake_client = FakeClient(FakeBucket(uploaded))
    monkeypatch.setattr(gcs_artifacts.storage, "Client", lambda: fake_client)
    result = {
        "run_id": "run-001",
        "batch_id": "batch-001",
        "test_case_id": "TC-003",
        "persona_id": "elderly_user",
        "status": run_status,
    }

    upload = gcs_artifacts.upload_run_artifacts(run_directory, result)

    assert upload["status"] == "uploaded"
    assert result["status"] == run_status
    assert "gs://autotest-screenshot/TC-003/batch-001/elderly_user/result.json" in upload["uploaded_files"]
    assert "TC-003/batch-001/elderly_user/screenshots/step_000.png" in uploaded
    uploaded_result = json.loads(
        uploaded["TC-003/batch-001/elderly_user/result.json"].decode("utf-8")
    )
    assert uploaded_result["cloud_storage"]["status"] == "uploaded"


def test_records_partial_upload_failure_without_changing_run_status(
    tmp_path, monkeypatch
) -> None:
    run_directory = tmp_path / "TC-003" / "batch-002" / "first_time_user"
    run_directory.mkdir(parents=True)
    (run_directory / "result.json").write_text("{}", encoding="utf-8")
    (run_directory / "action_history.json").write_text("{}", encoding="utf-8")

    uploaded: dict[str, bytes] = {}
    failed_object = "TC-003/batch-002/first_time_user/action_history.json"
    fake_client = FakeClient(FakeBucket(uploaded, fail_on=failed_object))
    monkeypatch.setattr(gcs_artifacts.storage, "Client", lambda: fake_client)
    result = {
        "run_id": "run-002",
        "batch_id": "batch-002",
        "test_case_id": "TC-003",
        "persona_id": "first_time_user",
        "status": "blocked",
    }

    upload = gcs_artifacts.upload_run_artifacts(run_directory, result)

    assert upload["status"] == "partial"
    assert result["status"] == "blocked"
    assert upload["failed_files"] == [
        {"file": "action_history.json", "error": "simulated upload failure"}
    ]
    assert "TC-003/batch-002/first_time_user/result.json" in uploaded
    saved_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
    assert saved_result["cloud_storage"]["status"] == "partial"


def test_records_cloud_connection_failure_without_changing_run_status(
    tmp_path, monkeypatch
) -> None:
    run_directory = tmp_path / "TC-003" / "batch-003" / "low_vision_user"
    run_directory.mkdir(parents=True)
    result = {
        "run_id": "run-003",
        "batch_id": "batch-003",
        "test_case_id": "TC-003",
        "persona_id": "low_vision_user",
        "status": "completed",
    }

    def fail_client_creation():
        raise RuntimeError("application credentials unavailable")

    monkeypatch.setattr(gcs_artifacts.storage, "Client", fail_client_creation)

    upload = gcs_artifacts.upload_run_artifacts(run_directory, result)

    assert upload["status"] == "failed"
    assert result["status"] == "completed"
    saved_result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
    assert saved_result["cloud_storage"]["failed_files"][0]["error"] == (
        "application credentials unavailable"
    )
