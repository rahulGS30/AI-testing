import argparse
import json
import logging
import sys
from app.config import settings
from app.models.test_case import load_test_case
from app.orchestrator.run_batch import (
    create_batch_record,
    execute_batch,
    new_batch_id,
    new_run_id,
    save_batch_record,
)
from app.orchestrator.test_runner import CASES_DIRECTORY
from app.personas.persona_manager import list_personas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a JSON-defined persona UX test in an isolated browser."
    )
    parser.add_argument("test_case_id", help="Test-case id, for example TC-002")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the Chromium window while recording the run.",
    )
    persona_group = parser.add_mutually_exclusive_group()
    persona_group.add_argument(
        "--persona",
        nargs="+",
        choices=[persona.persona_id for persona in list_personas()],
        metavar="PERSONA_ID",
        help="Run once for each selected persona. May be used with multiple IDs.",
    )
    persona_group.add_argument(
        "--all-personas",
        action="store_true",
        help="Run once for every configured persona.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        test_case = load_test_case(CASES_DIRECTORY, args.test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Test case error: {exc}", file=sys.stderr)
        return 2

    selected_personas = (
        [persona.persona_id for persona in list_personas()]
        if args.all_personas
        else args.persona or [test_case.persona_id]
    )
    run_ids = [new_run_id() for _ in selected_personas]
    batch = create_batch_record(
        test_case, selected_personas, run_ids, new_batch_id()
    )
    save_batch_record(batch, settings.artifacts_dir)
    batch = execute_batch(
        test_case,
        batch,
        headed=args.headed,
        artifacts_root=settings.artifacts_dir,
    )
    print(json.dumps(batch, indent=2, ensure_ascii=False))
    print(
        f"\nPersona run {batch['run_id']}: {batch['status']} "
        f"({batch['finished_count']}/{batch['persona_count']} runs finished)"
    )
    print(
        "Batch manifest: "
        f"{settings.artifacts_dir / test_case.test_case_id / 'batches' / (batch['batch_id'] + '.json')}"
    )
    for run in batch["runs"]:
        run_directory = (
            settings.artifacts_dir
            / test_case.test_case_id
            / batch["batch_id"]
            / run["persona_id"]
        )
        print(
            f"- {run['persona_id']}: {run['status']}; "
            f"analysis={run['analysis_status']}; artifacts={run_directory}"
        )
    return 0 if batch["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
