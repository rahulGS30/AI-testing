import pytest
from pydantic import ValidationError

from app.agents import ux_analyzer
from app.agents.ux_analyzer import UXAnalyzer
from app.config import PROJECT_ROOT
from app.models.browser_action import BrowserAction
from app.models.test_case import load_test_case
from app.personas.persona_manager import get_persona
from app.models.ux_analysis import UXAnalysis
from app.orchestrator import test_runner


def _analysis(quote: str) -> UXAnalysis:
    return UXAnalysis.model_validate(
        {
            "summary": "The search was completed before the run was blocked.",
            "strengths": [
                {
                    "observation": "The search returned results.",
                    "persona_impact": "The requested products were discoverable.",
                    "recommendation": "Keep the visible search entry point.",
                    "priority": "low",
                    "evidence": [
                        {
                            "step": 2,
                            "stage": "after",
                            "source": "page_text",
                            "quote": quote,
                        }
                    ],
                }
            ],
            "improvements": [],
            "not_assessed": ["Product detail and cart flow."],
            "evidence_scope": "The run reached search results but not product details.",
        }
    )


def test_analysis_requires_cited_exact_observation_text() -> None:
    analysis = _analysis("23 results found")
    history = {
        "initial_observation": {
            "visible_text": "Home page",
            "interactive_elements": [],
        },
        "actions": [
            {
                "step": 2,
                "observation_after": {
                    "visible_text": 'Search: 23 results found for "jacket argentina"',
                    "interactive_elements": [],
                },
            }
        ],
    }

    UXAnalyzer._validate_evidence(
        analysis,
        history,
        {"status": "blocked", "result": "Verification"},
    )


def test_analysis_rejects_claims_without_evidence_in_the_run() -> None:
    analysis = _analysis("The products have excellent contrast")
    history = {
        "initial_observation": {
            "visible_text": "Home page",
            "interactive_elements": [],
        },
        "actions": [
            {
                "step": 2,
                "observation_after": {
                    "visible_text": "Search: 23 results found",
                    "interactive_elements": [],
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="not found"):
        UXAnalyzer._validate_evidence(
            analysis,
            history,
            {"status": "blocked", "result": "Verification"},
        )


def test_analysis_rejects_citation_to_missing_action_step() -> None:
    analysis = _analysis("23 results")
    with pytest.raises(ValueError, match="unknown action step"):
        UXAnalyzer._validate_evidence(
            analysis,
            {"initial_observation": {}, "actions": []},
            {"status": "blocked"},
        )


def test_action_citation_cannot_use_model_generated_reason_as_evidence() -> None:
    analysis = _analysis("The results are easy to find.")
    analysis.strengths[0].evidence[0].source = "action"
    history = {
        "initial_observation": {},
        "actions": [
            {
                "step": 2,
                "decision": {
                    "action": "click",
                    "target": "Search",
                    "reason": "The results are easy to find.",
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="not found"):
        UXAnalyzer._validate_evidence(
            analysis,
            history,
            {"status": "blocked"},
        )


def test_legacy_action_records_can_support_action_citations() -> None:
    analysis = _analysis("Search")
    evidence = analysis.strengths[0].evidence[0]
    evidence.source = "action"
    evidence.step = 1
    history = {
        "actions": [
            {
                "step": 1,
                "decision": {"action": "click", "target": "Search"},
                "before": {"url": "https://example.test/", "title": "Store"},
                "after": {"url": "https://example.test/", "title": "Store"},
            }
        ]
    }

    UXAnalyzer._validate_evidence(
        analysis,
        history,
        {"status": "completed", "result": "The requested product was confirmed in the cart."},
    )


def test_analyzer_requests_persona_review_and_accepts_grounded_findings(
    monkeypatch,
) -> None:
    calls = []

    class FakeResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type(
                "Response",
                (),
                {"output_text": _analysis("23 results found").model_dump_json()},
            )()

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(ux_analyzer, "create_client", lambda: (FakeClient(), None))
    monkeypatch.setattr(ux_analyzer, "uses_responses_api", lambda: True)
    monkeypatch.setattr(
        ux_analyzer,
        "settings",
        type("Settings", (), {"azure_ai_model_deployment": "test-deployment"})(),
    )
    test_case = load_test_case(PROJECT_ROOT / "test_cases", "TC-003")
    persona = get_persona("low_vision_user")
    assert persona is not None
    history = {
        "initial_observation": {
            "visible_text": "Home page",
            "interactive_elements": [],
        },
        "actions": [
            {
                "step": 2,
                "observation_after": {
                    "visible_text": 'Search: 23 results found for "jacket argentina"',
                    "interactive_elements": [],
                },
            }
        ],
    }

    result = UXAnalyzer().analyze(
        test_case,
        persona,
        {"status": "blocked", "result": "Verification"},
        history,
    )

    assert result.status == "completed"
    assert result.strengths[0].evidence[0].quote == "23 results found"
    assert calls[0]["model"] == "test-deployment"
    assert "low_vision_user" in calls[0]["input"]
    assert "JSON object" in calls[0]["input"]
    assert "Do not infer contrast" in calls[0]["instructions"]
    assert "Some older runs contain only action records" in calls[0]["instructions"]


def test_analysis_findings_require_evidence() -> None:
    with pytest.raises(ValidationError):
        UXAnalysis.model_validate(
            {
                "summary": "Analysis",
                "strengths": [
                    {
                        "observation": "Search works.",
                        "persona_impact": "Helpful.",
                        "recommendation": "Keep it.",
                        "priority": "low",
                        "evidence": [],
                    }
                ],
                "improvements": [],
                "not_assessed": [],
                "evidence_scope": "Search",
            }
        )


def test_run_persists_persona_analysis_with_evidence(
    tmp_path, monkeypatch
) -> None:
    case = load_test_case(PROJECT_ROOT / "test_cases", "TC-003")
    observation = {
        "url": str(case.website),
        "title": "Store",
        "visible_text": "Search",
        "interactive_elements": [
            {
                "tag": "button",
                "label": "Search",
                "visible": True,
            }
        ],
    }

    class FakeController:
        def __init__(self, *args, **kwargs):
            self.blocked_navigation = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def open(self):
            pass

        def observe(self):
            return observation

        def save_screenshot(self, step):
            return f"screenshots/step_{step:03d}.png"

    class FakeAgent:
        def decide(self, *args):
            return BrowserAction(
                action="finish",
                reason="Evidence collection test.",
                expected_result="The run stops.",
                confidence=1.0,
            )

    analysis = UXAnalysis(
        summary="The search control was available.",
        strengths=[
            {
                "observation": "A Search control was available.",
                "persona_impact": "It offers a clear entry point to search.",
                "recommendation": "Keep the visible search control.",
                "priority": "low",
                "evidence": [
                    {
                        "step": 0,
                        "stage": "initial",
                        "source": "control",
                        "quote": "Search",
                    }
                ],
            }
        ],
        improvements=[],
        not_assessed=["Search results and cart journey."],
        evidence_scope="The initial page only.",
    )
    analyzer_calls = []

    class FakeAnalyzer:
        def analyze(self, test_case, persona, run_result, action_history):
            analyzer_calls.append((persona.persona_id, action_history))
            return analysis

    monkeypatch.setattr(
        test_runner,
        "settings",
        type(
            "TestSettings",
            (),
            {
                "artifacts_dir": tmp_path,
                "browser_timeout": 1000,
                "max_agent_steps": 10,
                "azure_ai_configured": True,
            },
        )(),
    )
    monkeypatch.setattr(test_runner, "PlaywrightController", FakeController)
    monkeypatch.setattr(test_runner, "UXAnalyzer", FakeAnalyzer)

    result = test_runner.run_test_case(
        case,
        agent=FakeAgent(),
        artifacts_directory=tmp_path / case.test_case_id,
    )

    run_directory = tmp_path / case.test_case_id / result["run_id"]
    persisted_result = (run_directory / "result.json").read_text(encoding="utf-8")
    persisted_analysis = (run_directory / "ux_analysis.json").read_text(
        encoding="utf-8"
    )
    persisted_history = (run_directory / "action_history.json").read_text(
        encoding="utf-8"
    )

    assert result["status"] == "incomplete"
    assert result["analysis"]["status"] == "completed"
    assert "strengths" in persisted_result
    assert '"quote": "Search"' in persisted_analysis
    assert '"initial_observation"' in persisted_history
    assert analyzer_calls[0][0] == case.persona_id
