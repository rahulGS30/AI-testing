from dataclasses import dataclass
import os
import json
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
LOCAL_SETTINGS_PATH = PROJECT_ROOT / "settings.local.json"


def _load_local_settings() -> dict[str, object]:
    if not LOCAL_SETTINGS_PATH.exists():
        return {}
    try:
        with LOCAL_SETTINGS_PATH.open(encoding="utf-8") as settings_file:
            data = json.load(settings_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not read {LOCAL_SETTINGS_PATH.name}; check that it is valid JSON."
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{LOCAL_SETTINGS_PATH.name} must contain a JSON object.")
    azure_ai = data.get("azure_ai", {})
    if not isinstance(azure_ai, dict):
        raise RuntimeError(
            f"The 'azure_ai' setting in {LOCAL_SETTINGS_PATH.name} must be an object."
        )
    return azure_ai


_local_azure_settings = _load_local_settings()


def _setting(environment_name: str, json_name: str, default: str = "") -> str:
    value = os.getenv(environment_name)
    if value is not None:
        return value.strip()
    json_value = _local_azure_settings.get(json_name, default)
    return str(json_value).strip()


@dataclass(frozen=True)
class Settings:
    azure_ai_endpoint: str
    azure_ai_api_key: str
    azure_ai_model_deployment: str
    azure_ai_api_mode: str
    azure_ai_api_version: str
    app_url: str
    backend_host: str
    backend_port: int
    max_agent_steps: int
    browser_timeout: int
    artifacts_dir: Path

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            azure_ai_endpoint=_setting("AZURE_AI_ENDPOINT", "endpoint"),
            azure_ai_api_key=os.getenv(
                "AZURE_AI_API_KEY",
                str(
                    _local_azure_settings.get(
                        "token", _local_azure_settings.get("api_key", "")
                    )
                ),
            ).strip(),
            azure_ai_model_deployment=_setting(
                "AZURE_AI_MODEL_DEPLOYMENT", "model_deployment"
            ),
            azure_ai_api_mode=_setting(
                "AZURE_AI_API_MODE", "api_mode", "azure_openai"
            ).lower(),
            azure_ai_api_version=_setting(
                "AZURE_AI_API_VERSION", "api_version"
            ),
            app_url=os.getenv("APP_URL", "http://localhost:3000").strip(),
            backend_host=os.getenv("BACKEND_HOST", "127.0.0.1").strip(),
            backend_port=int(os.getenv("BACKEND_PORT", "8000")),
            max_agent_steps=int(os.getenv("MAX_AGENT_STEPS", "20")),
            browser_timeout=int(os.getenv("BROWSER_TIMEOUT", "30000")),
            artifacts_dir=Path(
                os.getenv("ARTIFACTS_DIR", str(PROJECT_ROOT / "artifacts"))
            ),
        )

    @property
    def azure_ai_configured(self) -> bool:
        required_values = (
            self.azure_ai_endpoint,
            self.azure_ai_api_key,
            self.azure_ai_model_deployment,
        )
        supported_modes = {"azure_openai", "openai_v1", "foundry_models"}
        needs_api_version = self.azure_ai_api_mode in {
            "azure_openai",
            "foundry_models",
        }
        return (
            all(required_values)
            and self.azure_ai_api_mode in supported_modes
            and (not needs_api_version or bool(self.azure_ai_api_version))
        )


settings = Settings.from_environment()
