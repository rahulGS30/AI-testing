import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
PDF_BYTES = b"%PDF-1.7\nfake report"
TEST_CASE_ID = "TC-003"
BATCH_ID = "20261128T000000Z-aabbccdd"
RUN_ID = "20261128T000001Z-bbccddee"


def _configure_upload(monkeypatch, tmp_path):
    from app.api import test_cases

    uploaded = []
    monkeypatch.setattr(
        test_cases,
        "settings",
        SimpleNamespace(artifacts_dir=tmp_path),
    )

    def fake_upload(pdf_bytes: bytes, object_name: str) -> str:
        uploaded.append((pdf_bytes, object_name))
        return f"gs://autotest-screenshot/{object_name}"

    monkeypatch.setattr(test_cases, "send_report_pdf", fake_upload)
    return uploaded


def _write_batch(tmp_path, *, run_id: str = RUN_ID) -> None:
    batch_directory = tmp_path / TEST_CASE_ID / "batches"
    batch_directory.mkdir(parents=True)
    (batch_directory / f"{BATCH_ID}.json").write_text(
        json.dumps(
            {
                "batch_id": BATCH_ID,
                "test_case_id": TEST_CASE_ID,
                "runs": [{"run_id": run_id, "persona_id": "elderly_user"}],
            }
        ),
        encoding="utf-8",
    )


def test_uploads_batch_pdf_to_gcs_and_keeps_local_copy(
    tmp_path, monkeypatch
) -> None:
    uploaded = _configure_upload(monkeypatch, tmp_path)
    _write_batch(tmp_path)

    response = client.post(
        f"/api/test-cases/{TEST_CASE_ID}/reports/{BATCH_ID}/pdf",
        files={"file": ("report.pdf", PDF_BYTES, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["gs_uri"] == (
        f"gs://autotest-screenshot/{TEST_CASE_ID}/{BATCH_ID}/report.pdf"
    )
    assert uploaded == [
        (PDF_BYTES, f"{TEST_CASE_ID}/{BATCH_ID}/report.pdf")
    ]
    assert (
        tmp_path / TEST_CASE_ID / BATCH_ID / "report.pdf"
    ).read_bytes() == PDF_BYTES


def test_uploads_single_run_pdf_to_its_persona_object_path(
    tmp_path, monkeypatch
) -> None:
    uploaded = _configure_upload(monkeypatch, tmp_path)
    _write_batch(tmp_path)
    run_directory = tmp_path / TEST_CASE_ID / BATCH_ID / "elderly_user"
    run_directory.mkdir(parents=True)
    (run_directory / "result.json").write_text(
        json.dumps(
            {
                "run_id": RUN_ID,
                "batch_id": BATCH_ID,
                "test_case_id": TEST_CASE_ID,
                "persona_id": "elderly_user",
                "status": "completed",
                "artifacts": {},
            }
        ),
        encoding="utf-8",
    )

    response = client.post(
        f"/api/test-cases/{TEST_CASE_ID}/reports/{RUN_ID}/pdf",
        files={"file": ("report.pdf", PDF_BYTES, "application/pdf")},
    )

    expected_object = (
        f"{TEST_CASE_ID}/{BATCH_ID}/elderly_user/report.pdf"
    )
    assert response.status_code == 200
    assert response.json()["object_name"] == expected_object
    assert uploaded == [(PDF_BYTES, expected_object)]
    assert (run_directory / "report.pdf").read_bytes() == PDF_BYTES


def test_rejects_non_pdf_report_content(tmp_path, monkeypatch) -> None:
    uploaded = _configure_upload(monkeypatch, tmp_path)
    _write_batch(tmp_path)

    response = client.post(
        f"/api/test-cases/{TEST_CASE_ID}/reports/{BATCH_ID}/pdf",
        files={"file": ("report.pdf", b"not a pdf", "application/pdf")},
    )

    assert response.status_code == 422
    assert uploaded == []
