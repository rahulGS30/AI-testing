from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class TestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{1,39}$")
    name: str
    website: HttpUrl
    allowed_domain: str
    persona_id: str
    goal: str
    completion_criteria: list[str] = Field(min_length=1)
    product_keywords: list[str] = Field(default_factory=list)
    cart_confirmation_phrases: list[str] = Field(
        default_factory=lambda: ["added to cart"]
    )
    max_steps: int = Field(default=20, ge=1, le=40)

    @field_validator("allowed_domain")
    @classmethod
    def normalize_allowed_domain(cls, value: str) -> str:
        normalized = value.strip().lower().rstrip(".")
        if not normalized or "/" in normalized or ":" in normalized or "*" in normalized:
            raise ValueError("allowed_domain must be a plain domain name")
        return normalized

    @field_validator("website")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("Test case websites must use HTTPS")
        return value

    @field_validator("product_keywords", "cart_confirmation_phrases")
    @classmethod
    def require_nonempty_terms(cls, value: list[str]) -> list[str]:
        terms = [term.strip() for term in value if term.strip()]
        if not terms:
            raise ValueError("Search and cart confirmation terms cannot be empty")
        return terms


def load_test_case(cases_directory: Path, test_case_id: str) -> TestCase:
    if not test_case_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid test case id.")
    case_path = cases_directory / f"{test_case_id}.json"
    if not case_path.is_file():
        raise FileNotFoundError(f"Test case '{test_case_id}' was not found.")
    return TestCase.model_validate_json(case_path.read_text(encoding="utf-8"))
