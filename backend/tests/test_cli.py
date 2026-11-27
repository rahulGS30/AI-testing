from app import cli
from app.models.test_case import load_test_case
from app.orchestrator.test_runner import CASES_DIRECTORY


def test_cli_runs_selected_personas_and_returns_failure_if_any_run_fails(
    monkeypatch, capsys
) -> None:
    calls = []

    def run_test_case(test_case, *, headless):
        calls.append((test_case.persona_id, headless))
        status = "blocked" if test_case.persona_id == "low_vision_user" else "completed"
        return {
            "run_id": f"run-{test_case.persona_id}",
            "test_case_id": test_case.test_case_id,
            "persona_id": test_case.persona_id,
            "status": status,
            "result": "result",
            "error": None,
        }

    monkeypatch.setattr(cli, "run_test_case", run_test_case)

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
    assert calls == [
        ("elderly_user", False),
        ("low_vision_user", False),
        ("non_native_english_speaker", False),
    ]
    assert "Persona run summary:" in capsys.readouterr().out


def test_cli_all_personas_uses_every_configured_persona(monkeypatch, capsys) -> None:
    calls = []

    def run_test_case(test_case, *, headless):
        calls.append(test_case.persona_id)
        return {
            "run_id": f"run-{test_case.persona_id}",
            "test_case_id": test_case.test_case_id,
            "persona_id": test_case.persona_id,
            "status": "completed",
            "result": "completed",
            "error": None,
        }

    monkeypatch.setattr(cli, "run_test_case", run_test_case)

    exit_code = cli.main(["TC-003", "--all-personas"])

    assert exit_code == 0
    assert calls == [persona.persona_id for persona in cli.list_personas()]
    assert "Persona run summary:" in capsys.readouterr().out


def test_cli_without_persona_uses_test_case_default(monkeypatch, capsys) -> None:
    calls = []

    def run_test_case(test_case, *, headless):
        calls.append(test_case.persona_id)
        return {
            "run_id": "run-default",
            "test_case_id": test_case.test_case_id,
            "persona_id": test_case.persona_id,
            "status": "completed",
            "result": "completed",
            "error": None,
        }

    monkeypatch.setattr(cli, "run_test_case", run_test_case)

    assert cli.main(["TC-003"]) == 0
    assert calls == [load_test_case(CASES_DIRECTORY, "TC-003").persona_id]
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
