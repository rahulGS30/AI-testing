import json
import logging
from types import SimpleNamespace

from app.llm.token_usage import TokenUsageTracker


def test_tracker_records_and_aggregates_api_usage(tmp_path, caplog) -> None:
    tracker = TokenUsageTracker(tmp_path, "run-123")
    responses = [
        {
            "usage": {
                "prompt_tokens": 17,
                "completion_tokens": 8,
                "total_tokens": 25,
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 2},
            }
        },
        SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=12,
                output_tokens=4,
                total_tokens=16,
                input_tokens_details=SimpleNamespace(cached_tokens=5),
                output_tokens_details=SimpleNamespace(reasoning_tokens=1),
            )
        ),
    ]

    with caplog.at_level(logging.INFO):
        tracker.record_response("browser_action", responses[0])
        tracker.record_response("ux_analysis", responses[1])

    usage_file = json.loads((tmp_path / "token_usage.json").read_text(encoding="utf-8"))
    assert usage_file["totals"] == {
        "call_count": 2,
        "usage_reported_count": 2,
        "input_tokens": 29,
        "output_tokens": 12,
        "total_tokens": 41,
        "cached_input_tokens": 8,
        "reasoning_tokens": 3,
    }
    assert usage_file["by_call_type"]["browser_action"]["total_tokens"] == 25
    assert usage_file["by_call_type"]["ux_analysis"]["total_tokens"] == 16
    assert "LLM token usage run=run-123" in caplog.text


def test_tracker_keeps_missing_usage_explicit(tmp_path) -> None:
    tracker = TokenUsageTracker(tmp_path, "run-456")
    tracker.record_response("browser_action", SimpleNamespace(output_text="{}"))

    usage_file = json.loads((tmp_path / "token_usage.json").read_text(encoding="utf-8"))
    assert usage_file["calls"][0]["usage_reported"] is False
    assert usage_file["calls"][0]["total_tokens"] is None
    assert usage_file["totals"]["usage_reported_count"] == 0
