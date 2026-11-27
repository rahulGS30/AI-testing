from types import SimpleNamespace

from app.agents import browser_agent
from app.config import PROJECT_ROOT
from app.llm.prompts import SYSTEM_PROMPT
from app.models.test_case import load_test_case
from app.personas.persona_manager import get_persona


def test_agent_uses_responses_api_for_responses_endpoint(monkeypatch) -> None:
    calls = []

    class FakeResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output_text=(
                    '{"action":"fill","target":"Search","value":"iPhone 17",'
                    '"reason":"Search for the requested product.",'
                    '"expected_result":"Search results appear.","confidence":0.95}'
                )
            )

    class FakeClient:
        responses = FakeResponses()

    monkeypatch.setattr(browser_agent, "create_client", lambda: (FakeClient(), None))
    monkeypatch.setattr(browser_agent, "uses_responses_api", lambda: True)
    monkeypatch.setattr(
        browser_agent,
        "settings",
        SimpleNamespace(azure_ai_model_deployment="test-deployment"),
    )
    agent = browser_agent.BrowserAgent()
    test_case = load_test_case(PROJECT_ROOT / "test_cases", "TC-002")
    persona = get_persona("first_time_user")
    assert persona is not None

    action = agent.decide(test_case, persona, {"url": "https://www.amazon.com"}, [])

    assert action.action == "fill"
    assert action.value == "iPhone 17"
    assert calls[0]["model"] == "test-deployment"
    assert calls[0]["text"] == {"format": {"type": "json_object"}}
    assert calls[0]["max_output_tokens"] == 500
    assert "JSON object" in calls[0]["input"]


def test_prompt_prefers_direct_search_after_one_blank_page_wait() -> None:
    assert "bounded web UX test" in SYSTEM_PROMPT
