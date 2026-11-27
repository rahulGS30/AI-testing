import pytest
from types import SimpleNamespace
from pathlib import Path

from app.browser.playwright_controller import (
    PlaywrightController,
    UnsafeNavigationError,
    _ensure_safe_action,
    _ensure_safe_input,
    validate_public_target,
)
from app.models.browser_action import BrowserAction
from app.models.test_case import TestCase as TestCaseModel
from app.models.test_case import load_test_case
from app.orchestrator.test_runner import CASES_DIRECTORY, _is_cart_confirmed, _obstacle

TestCaseModel.__test__ = False


def test_amazon_case_is_loaded_from_json() -> None:
    case = load_test_case(CASES_DIRECTORY, "TC-002")

    assert case.test_case_id == "TC-002"
    assert str(case.website).startswith("https://www.amazon.com")
    assert case.persona_id == "first_time_user"
    assert case.max_steps == 15


def test_thayyil_sports_case_is_loaded_from_separate_json() -> None:
    case = load_test_case(CASES_DIRECTORY, "TC-003")

    assert case.test_case_id == "TC-003"
    assert str(case.website).startswith("https://www.thayyilsports.in")
    assert case.allowed_domain == "thayyilsports.in"
    assert case.product_keywords == ["jacket", "argentina"]
    assert case.max_steps == 18


def test_cart_confirmation_uses_the_selected_test_case_product() -> None:
    case = load_test_case(CASES_DIRECTORY, "TC-003")

    assert _is_cart_confirmed(
        {
            "url": "https://www.thayyilsports.in/cart",
            "title": "Shopping Cart",
            "visible_text": "Argentina National Team Jacket",
        },
        case,
    )
    assert not _is_cart_confirmed(
        {
            "url": "https://www.thayyilsports.in/cart",
            "title": "Shopping Cart",
            "visible_text": "France National Team Jacket",
        },
        case,
    )
    assert _is_cart_confirmed(
        {
            "url": "https://www.thayyilsports.in/products/jacket-argentina",
            "title": "Argentina Jacket",
            "visible_text": "Argentina National Team Jacket",
            "interactive_elements": [
                {
                    "tag": "a",
                    "label": "1 1 items My Cart",
                    "href": "/cart",
                    "visible": True,
                }
            ],
        },
        case,
    )


def test_browser_verification_is_recorded_as_a_blocker() -> None:
    assert _obstacle(
        {
            "visible_text": "Please verify your browser before continuing.",
            "title": "Browser verification",
            "url": "https://www.thayyilsports.in/",
        }
    )


def test_test_case_rejects_non_https_websites() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        TestCaseModel.model_validate(
            {
                "test_case_id": "TC-003",
                "name": "Bad URL",
                "website": "http://example.com",
                "allowed_domain": "example.com",
                "persona_id": "first_time_user",
                "goal": "Test",
                "completion_criteria": ["Done"],
            }
        )


def test_navigation_rejects_a_different_domain(monkeypatch) -> None:
    with pytest.raises(UnsafeNavigationError, match="outside"):
        validate_public_target("https://example.net", "amazon.com")


@pytest.mark.parametrize(
    "path",
    ["/account/sign-in", "/login", "/checkout", "/purchase", "/buy-now"],
)
def test_navigation_rejects_generic_purchase_and_auth_paths(monkeypatch, path) -> None:
    from app.browser import playwright_controller

    monkeypatch.setattr(
        playwright_controller,
        "_resolve_addresses",
        lambda hostname: frozenset({"93.184.216.34"}),
    )
    with pytest.raises(UnsafeNavigationError, match="Sign-in and checkout"):
        validate_public_target(f"https://store.example{path}", "store.example")


@pytest.mark.parametrize("target", ["Buy", "Proceed to checkout", "Sign in"])
def test_purchase_and_signin_clicks_are_blocked(target: str) -> None:
    action = BrowserAction(
        action="click",
        target=target,
        reason="test",
        expected_result="test",
        confidence=0.9,
    )

    with pytest.raises(UnsafeNavigationError):
        _ensure_safe_action(action)


def test_personal_information_fields_are_blocked() -> None:
    class Input:
        def get_attribute(self, name: str) -> str | None:
            return {"type": "email", "name": "email"}.get(name)

    action = BrowserAction(
        action="fill",
        target="Email",
        value="someone@example.com",
        reason="test",
        expected_result="test",
        confidence=0.9,
    )

    with pytest.raises(UnsafeNavigationError, match="personal"):
        _ensure_safe_input(action, Input())


def test_blocked_subframe_does_not_mark_main_page_blocked(tmp_path: Path) -> None:
    case = load_test_case(CASES_DIRECTORY, "TC-002")
    controller = PlaywrightController(case, tmp_path, timeout_ms=1000)
    main_frame = object()
    controller.page = SimpleNamespace(main_frame=main_frame)

    controller._record_blocked_navigation(
        "https://accounts.example.net/signin",
        "Navigation is outside the allowed domain.",
        SimpleNamespace(frame=object()),
    )
    assert controller.blocked_navigation is None

    controller._record_blocked_navigation(
        "https://accounts.example.net/signin",
        "Navigation is outside the allowed domain.",
        SimpleNamespace(frame=main_frame),
    )
    assert controller.blocked_navigation is not None
    assert "accounts.example.net" in controller.blocked_navigation
    assert "/signin" not in controller.blocked_navigation
