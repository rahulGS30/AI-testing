import argparse
import json
import logging
import sys

from app.config import settings
from app.models.test_case import load_test_case
from app.orchestrator.test_runner import CASES_DIRECTORY, run_test_case
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
    results = []
    for persona_id in selected_personas:
        persona_case = test_case.model_copy(update={"persona_id": persona_id})
        result = run_test_case(persona_case, headless=not args.headed)
        results.append(result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        print(
            f"\nPersona {persona_id} — run {result['run_id']}: "
            f"{result['status']} — "
            f"{result.get('result') or result.get('error') or 'No result detail'}"
        )
        run_directory = (
            settings.artifacts_dir / result["test_case_id"] / result["run_id"]
        )
        print(f"Artifacts: {run_directory}")

    if len(results) > 1:
        print("\nPersona run summary:")
        for result in results:
            print(
                f"- {result['persona_id']}: {result['status']} "
                f"(run {result['run_id']})"
            )
    return 0 if all(result["status"] == "completed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
