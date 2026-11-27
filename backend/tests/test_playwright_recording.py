from pathlib import Path

from app.browser.playwright_controller import PlaywrightController, _visible_locator
from app.models.browser_action import BrowserAction, BrowserActionType
from app.models.test_case import TestCase as TestCaseModel


TestCaseModel.__test__ = False


def test_controller_captures_screenshot_and_named_video(
    tmp_path: Path, monkeypatch
) -> None:
    from app.browser import playwright_controller

    monkeypatch.setattr(
        playwright_controller,
        "_resolve_addresses",
        lambda hostname: frozenset({"93.184.216.34"}),
    )
    test_case = TestCaseModel.model_validate(
        {
            "test_case_id": "TC-TEST",
            "name": "Local recording check",
            "website": "https://store.example",
            "allowed_domain": "store.example",
            "persona_id": "first_time_user",
            "goal": "Check browser recording",
            "completion_criteria": ["Capture evidence"],
        }
    )

    with PlaywrightController(test_case, tmp_path, timeout_ms=10000) as browser:
        assert browser.page is not None
        browser.page.set_content(
            "<main><h1>Local recording check</h1>"
            "<button type='button'>Search</button>"
            "<input type='search' placeholder='Search'>"
            "<button type='button'>ADD TO CART</button></main>"
        )
        browser.execute(
            BrowserAction(
                action="fill",
                target="Search",
                value="jacket argentina",
                reason="Test that fill targets the search input.",
                expected_result="The search query is entered.",
                confidence=1.0,
            )
        )
        assert (
            browser.page.locator("input[type=search]").input_value()
            == "jacket argentina"
        )
        add_to_cart = _visible_locator(
            browser.page, "ADD TO CART", BrowserActionType.CLICK
        )
        assert add_to_cart.inner_text() == "ADD TO CART"
        screenshot = browser.save_screenshot(0)

    assert (tmp_path / screenshot).is_file()
    assert (tmp_path / "video" / "journey.webm").is_file()
