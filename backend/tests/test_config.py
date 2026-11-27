from app.config import Settings


def test_local_settings_json_can_configure_azure_ai(monkeypatch) -> None:
    import app.config as config

    for name in (
        "AZURE_AI_ENDPOINT",
        "AZURE_AI_API_KEY",
        "AZURE_AI_MODEL_DEPLOYMENT",
        "AZURE_AI_API_MODE",
        "AZURE_AI_API_VERSION",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        config,
        "_local_azure_settings",
        {
            "endpoint": "https://example.openai.azure.com",
            "token": "local-token",
            "model_deployment": "deployment",
            "api_mode": "azure_openai",
            "api_version": "2025-01-01-preview",
        },
    )

    settings = Settings.from_environment()

    assert settings.azure_ai_configured
    assert settings.azure_ai_api_key == "local-token"


def test_v1_api_mode_does_not_require_an_api_version(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_AI_ENDPOINT", "https://example.openai.azure.com/openai/v1/")
    monkeypatch.setenv("AZURE_AI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT", "test-deployment")
    monkeypatch.setenv("AZURE_AI_API_MODE", "openai_v1")
    monkeypatch.delenv("AZURE_AI_API_VERSION", raising=False)

    assert Settings.from_environment().azure_ai_configured


def test_openai_v1_response_endpoint_is_normalized_to_api_base() -> None:
    from app.llm.azure_client import _normalize_openai_v1_endpoint

    assert (
        _normalize_openai_v1_endpoint(
            "https://example.services.ai.azure.com/openai/v1/responses"
        )
        == "https://example.services.ai.azure.com/openai/v1/"
    )


def test_openai_v1_chat_endpoint_is_normalized_to_api_base() -> None:
    from app.llm.azure_client import _normalize_openai_v1_endpoint

    assert (
        _normalize_openai_v1_endpoint(
            "https://example.services.ai.azure.com/openai/v1/chat/completions"
        )
        == "https://example.services.ai.azure.com/openai/v1/"
    )


def test_responses_endpoint_health_check_uses_responses_api_and_minimum_tokens(
    monkeypatch,
) -> None:
    from app.llm import azure_client

    calls = []

    class FakeResponses:
        def create(self, **kwargs):
            calls.append(kwargs)

    fake_client = type("FakeClient", (), {"responses": FakeResponses()})()
    monkeypatch.setattr(azure_client, "create_client", lambda: (fake_client, None))
    monkeypatch.setattr(azure_client, "uses_responses_api", lambda: True)
    monkeypatch.setattr(
        azure_client,
        "settings",
        type("FakeSettings", (), {"azure_ai_model_deployment": "deployment"})(),
    )

    azure_client.check_connection()

    assert calls[0]["max_output_tokens"] == 16
    assert calls[0]["model"] == "deployment"


def test_legacy_api_mode_requires_an_api_version(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_AI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_AI_API_KEY", "test-key")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT", "test-deployment")
    monkeypatch.setenv("AZURE_AI_API_MODE", "azure_openai")
    monkeypatch.delenv("AZURE_AI_API_VERSION", raising=False)

    assert not Settings.from_environment().azure_ai_configured
