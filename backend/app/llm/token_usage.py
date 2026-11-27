import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _first_count(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _reported_usage(response: Any) -> dict[str, int | None]:
    usage = _field(response, "usage")
    if usage is None:
        return {name: None for name in TOKEN_FIELDS}

    input_details = _field(usage, "input_tokens_details") or _field(
        usage, "prompt_tokens_details"
    )
    output_details = _field(usage, "output_tokens_details") or _field(
        usage, "completion_tokens_details"
    )
    input_tokens = _first_count(
        _field(usage, "input_tokens"),
        _field(usage, "prompt_tokens"),
    )
    output_tokens = _first_count(
        _field(usage, "output_tokens"),
        _field(usage, "completion_tokens"),
    )
    total_tokens = _first_count(_field(usage, "total_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": _first_count(
            _field(input_details, "cached_tokens")
        ),
        "reasoning_tokens": _first_count(
            _field(output_details, "reasoning_tokens")
        ),
    }


class TokenUsageTracker:
    def __init__(self, run_directory: Path, run_id: str) -> None:
        self.path = run_directory / "token_usage.json"
        self.run_id = run_id
        self.calls: list[dict[str, Any]] = []
        self._write()

    def record_response(self, call_type: str, response: Any) -> None:
        usage = _reported_usage(response)
        call = {
            "call_type": call_type,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "usage_reported": any(
                usage[field] is not None
                for field in ("input_tokens", "output_tokens", "total_tokens")
            ),
            **usage,
        }
        self.calls.append(call)
        self._write()
        logger.info(
            "LLM token usage run=%s call_type=%s input_tokens=%s "
            "output_tokens=%s total_tokens=%s usage_reported=%s",
            self.run_id,
            call_type,
            call["input_tokens"],
            call["output_tokens"],
            call["total_tokens"],
            call["usage_reported"],
        )

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "calls": self.calls,
            "totals": self._totals(self.calls),
            "by_call_type": {
                call_type: self._totals(
                    [call for call in self.calls if call["call_type"] == call_type]
                )
                for call_type in sorted({call["call_type"] for call in self.calls})
            },
        }

    @staticmethod
    def _totals(calls: list[dict[str, Any]]) -> dict[str, int]:
        totals = {
            "call_count": len(calls),
            "usage_reported_count": sum(
                1 for call in calls if call["usage_reported"]
            ),
        }
        for field in TOKEN_FIELDS:
            totals[field] = sum(
                call[field] for call in calls if call[field] is not None
            )
        return totals

    def _write(self) -> None:
        self.path.write_text(
            json.dumps(self.summary(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
