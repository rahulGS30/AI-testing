from types import SimpleNamespace

from app import cli
from app.models.test_case import load_test_case
from app.orchestrator.test_runner import CASES_DIRECTORY


def test_cli_runs_selected_personas_and_returns_failure_if_any_run_fails(
    monkeypatch, capsys, tmp_path
) -> None:
    monkeypatch.setattr(cli, "settings", SimpleNamespace(artifacts_dir=tmp_path))

    def execute_batch(test_case, batch, *, headed, artifacts_root):
        assert headed
        assert artifacts_root == tmp_path
        for run in batch["runs"]:
            run["status"] = (
                "blocked" if run["persona_id"] == "low_vision_user" else "completed"
            )
        batch["status"] = "finished_with_issues"
        batch["finished_count"] = 3
        return batch

    monkeypatch.setattr(cli, "execute_batch", execute_batch)

    exit_code = cli.main(
        [
            "TC-003",
            "--headed",
            "--persona",
            "elderly_user",
            "low_vision_user",
            "non_native_english_speaker",
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Persona run " in output
    assert "artifacts=" in output
    assert "low_vision_user" in output


def test_cli_all_personas_uses_every_configured_persona(
    monkeypatch, capsys, tmp_path
) -> None:
    monkeypatch.setattr(cli, "settings", SimpleNamespace(artifacts_dir=tmp_path))

    def execute_batch(test_case, batch, *, headed, artifacts_root):
        assert not headed
        assert artifacts_root == tmp_path
        for run in batch["runs"]:
            run["status"] = "completed"
        batch["status"] = "completed"
        batch["finished_count"] = len(batch["runs"])
        return batch

    monkeypatch.setattr(cli, "execute_batch", execute_batch)

    exit_code = cli.main(["TC-003", "--all-personas"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Persona run " in output
    assert all(
        persona.persona_id in output for persona in cli.list_personas()
    )


def test_cli_without_persona_uses_test_case_default(
    monkeypatch, capsys, tmp_path
) -> None:
    monkeypatch.setattr(cli, "settings", SimpleNamespace(artifacts_dir=tmp_path))
    default_persona_id = load_test_case(CASES_DIRECTORY, "TC-003").persona_id

    def execute_batch(test_case, batch, *, headed, artifacts_root):
        assert [run["persona_id"] for run in batch["runs"]] == [default_persona_id]
        batch["runs"][0]["status"] = "completed"
        batch["status"] = "completed"
        batch["completed_count"] = 1
        return batch

    monkeypatch.setattr(cli, "execute_batch", execute_batch)

    assert cli.main(["TC-003"]) == 0
    capsys.readouterr()


def test_cli_rejects_combining_persona_selection_modes(capsys) -> None:
    try:
        cli.main(
            [
                "TC-003",
                "--all-personas",
                "--persona",
                "low_vision_user",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected argparse to reject conflicting options.")
    assert "not allowed with argument" in capsys.readouterr().err
