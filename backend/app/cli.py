import argparse
import json
import logging
import sys

from app.config import settings
from app.models.test_case import load_test_case
from app.orchestrator.test_runner import CASES_DIRECTORY, run_test_case


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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        test_case = load_test_case(CASES_DIRECTORY, args.test_case_id)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Test case error: {exc}", file=sys.stderr)
        return 2

    result = run_test_case(test_case, headless=not args.headed)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(
        f"\nRun {result['run_id']}: {result['status']} — "
        f"{result.get('result') or result.get('error') or 'No result detail'}"
    )
    run_directory = settings.artifacts_dir / result["run_id"]
    print(f"Artifacts: {run_directory}")
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
