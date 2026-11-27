from urllib.parse import urlsplit, urlunsplit

from openai import AzureOpenAI, OpenAI

from app.config import settings


def _normalize_openai_v1_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint.rstrip("/"))
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/", "", ""))


def uses_responses_api() -> bool:
    return urlsplit(settings.azure_ai_endpoint.rstrip("/")).path.endswith("/responses")


def create_client() -> tuple[OpenAI, dict[str, str] | None]:
    if not settings.azure_ai_configured:
        raise ValueError(
            "Azure AI Foundry is not configured. Set credentials in "
            "settings.local.json or environment variables."
        )

    if settings.azure_ai_api_mode == "azure_openai":
        client: OpenAI = AzureOpenAI(
            azure_endpoint=settings.azure_ai_endpoint,
            api_key=settings.azure_ai_api_key,
            api_version=settings.azure_ai_api_version,
            timeout=30.0,
            max_retries=1,
        )
        return client, None
    if settings.azure_ai_api_mode == "openai_v1":
        return OpenAI(
            base_url=_normalize_openai_v1_endpoint(settings.azure_ai_endpoint),
            api_key=settings.azure_ai_api_key,
            timeout=30.0,
            max_retries=1,
        ), None

    return OpenAI(
        base_url=settings.azure_ai_endpoint.rstrip("/") + "/",
        api_key=settings.azure_ai_api_key,
        default_headers={"api-key": settings.azure_ai_api_key},
        timeout=30.0,
        max_retries=1,
    ), {"api-version": settings.azure_ai_api_version}


def check_connection() -> None:
    client, extra_query = create_client()
    request_options = {"extra_query": extra_query} if extra_query else {}
    if uses_responses_api():
        client.responses.create(
            model=settings.azure_ai_model_deployment,
            input="Reply with OK.",
            max_output_tokens=16,
            **request_options,
        )
    else:
        client.chat.completions.create(
            model=settings.azure_ai_model_deployment,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=5,
            **request_options,
        )
