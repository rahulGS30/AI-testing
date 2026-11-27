from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    Route,
    sync_playwright,
)

from app.models.browser_action import BrowserAction, BrowserActionType
from app.models.test_case import TestCase

BLOCKED_TERMS = (
    "checkout",
    "buy now",
    "purchase",
    "buy",
    "place order",
    "payment",
    "sign in",
    "sign-in",
    "log in",
    "login",
    "create account",
    "register",
)
BLOCKED_PATH_SEGMENTS = {
    "signin",
    "login",
    "register",
    "checkout",
    "buy",
    "buynow",
    "purchase",
    "payment",
    "order",
    "placeorder",
}
SENSITIVE_INPUT_TERMS = (
    "password",
    "email",
    "phone",
    "address",
    "postal",
    "zip",
    "credit",
    "payment",
    "card",
    "cvv",
)


class UnsafeNavigationError(ValueError):
    pass


def _resolve_addresses(hostname: str) -> frozenset[str]:
    return frozenset(
        item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    )


def validate_public_target(url: str, allowed_domain: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeNavigationError("The target URL has an invalid port.") from exc
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise UnsafeNavigationError("Only public HTTPS URLs on port 443 are allowed.")
    if hostname != allowed_domain and not hostname.endswith("." + allowed_domain):
        raise UnsafeNavigationError(
            f"Navigation to '{hostname}' is outside the test case's allowed domain."
        )
    path_segments = [
        "".join(re.findall(r"[a-z0-9]+", segment))
        for segment in unquote(parsed.path).lower().split("/")
    ]
    if any(segment in BLOCKED_PATH_SEGMENTS for segment in path_segments):
        raise UnsafeNavigationError("Sign-in and checkout pages are outside this test.")
    try:
        addresses = _resolve_addresses(hostname)
    except socket.gaierror as exc:
        raise UnsafeNavigationError(f"Could not resolve allowed host '{hostname}'.") from exc
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise UnsafeNavigationError("Private and reserved network destinations are blocked.")
    return hostname


def _validate_public_resource(url: str) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeNavigationError("The resource URL has an invalid port.") from exc
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise UnsafeNavigationError("Only public HTTPS resources on port 443 are allowed.")
    try:
        addresses = _resolve_addresses(hostname)
    except socket.gaierror as exc:
        raise UnsafeNavigationError(
            f"Could not resolve public resource host '{hostname}'."
        ) from exc
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise UnsafeNavigationError("Private and reserved network destinations are blocked.")


def _route_allowed_domain(
    route: Route,
    allowed_domain: str,
    on_blocked_navigation,
) -> None:
    try:
        if route.request.is_navigation_request():
            validate_public_target(route.request.url, allowed_domain)
        else:
            _validate_public_resource(route.request.url)
    except UnsafeNavigationError as exc:
        if route.request.is_navigation_request():
            on_blocked_navigation(route.request.url, str(exc), route.request)
        route.abort("blockedbyclient")
        return
    except ValueError:
        route.abort("blockedbyclient")
        return
    route.continue_()


def _visible_locator(
    page: Page,
    target: str,
    action: BrowserActionType | None = None,
):
    normalized_target = target.casefold()
    if action in {BrowserActionType.FILL, BrowserActionType.TYPE}:
        candidates = [
            page.get_by_role("textbox", name=target, exact=False),
            page.get_by_role("combobox", name=target, exact=False),
            page.get_by_label(target, exact=False),
            page.get_by_placeholder(target, exact=False),
        ]
    else:
        candidates = [
            page.get_by_role("button", name=target, exact=False),
            page.get_by_role("link", name=target, exact=False),
            page.get_by_label(target, exact=False),
            page.get_by_placeholder(target, exact=False),
        ]
        if "add to cart" in normalized_target:
            candidates.append(page.get_by_text(target, exact=False))
        else:
            candidates.append(page.get_by_text(target, exact=True))
    for locator in candidates:
        try:
            count = locator.count()
            visible_matches = [
                locator.nth(index)
                for index in range(count)
                if locator.nth(index).is_visible()
            ]
            if len(visible_matches) == 1:
                return visible_matches[0]
            if len(visible_matches) > 1:
                hrefs = {
                    item.get_attribute("href") for item in visible_matches
                }
                if len(hrefs) == 1 and None not in hrefs:
                    return visible_matches[0]
                if "add to cart" in normalized_target:
                    return visible_matches[0]
        except PlaywrightError:
            continue
    raise ValueError(f"Could not identify one visible control matching '{target}'.")


def _ensure_safe_action(action: BrowserAction) -> None:
    if action.action == BrowserActionType.CLICK:
        target = (action.target or "").casefold()
        if any(term in target for term in BLOCKED_TERMS):
            raise UnsafeNavigationError("This test is not allowed to start sign-in or checkout.")


def _ensure_safe_input(action: BrowserAction, locator) -> None:
    if action.action not in {BrowserActionType.FILL, BrowserActionType.TYPE}:
        return
    metadata = " ".join(
        filter(
            None,
            (
                action.target,
                locator.get_attribute("type"),
                locator.get_attribute("name"),
                locator.get_attribute("id"),
                locator.get_attribute("autocomplete"),
                locator.get_attribute("aria-label"),
                locator.get_attribute("placeholder"),
            ),
        )
    ).casefold()
    if any(term in metadata for term in SENSITIVE_INPUT_TERMS):
        raise UnsafeNavigationError(
            "Entering personal, sign-in, or payment information is outside this test."
        )


class PlaywrightController:
    def __init__(
        self,
        test_case: TestCase,
        run_directory: Path,
        timeout_ms: int,
        headless: bool = True,
    ) -> None:
        self.test_case = test_case
        self.run_directory = run_directory
        self.timeout_ms = timeout_ms
        self.headless = headless
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.blocked_navigation: str | None = None
        self.video_directory = run_directory / "video"
        self.screenshot_directory = run_directory / "screenshots"

    def __enter__(self) -> "PlaywrightController":
        validate_public_target(str(self.test_case.website), self.test_case.allowed_domain)
        self.screenshot_directory.mkdir(parents=True, exist_ok=True)
        self.video_directory.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.launch(headless=self.headless)
            self.context = self.browser.new_context(
                viewport={"width": 1365, "height": 900},
                record_video_dir=str(self.video_directory),
                record_video_size={"width": 1365, "height": 900},
                accept_downloads=False,
            )
            self.page = self.context.new_page()
            self.context.route(
                "**/*",
                lambda route: _route_allowed_domain(
                    route,
                    self.test_case.allowed_domain,
                    self._record_blocked_navigation,
                ),
            )
            self.context.set_default_timeout(self.timeout_ms)
        except Exception:
            self.close()
            raise
        return self

    def open(self) -> None:
        assert self.page is not None
        try:
            self.page.goto(
                str(self.test_case.website),
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
        except PlaywrightError:
            if self.blocked_navigation is None:
                raise

    def _record_blocked_navigation(self, url: str, reason: str, request) -> None:
        try:
            is_main_frame = (
                self.page is not None and request.frame == self.page.main_frame
            )
        except PlaywrightError:
            is_main_frame = False
        if is_main_frame:
            hostname = urlparse(url).hostname or "unknown host"
            self.blocked_navigation = f"{reason} (host: {hostname})"

    def observe(self) -> dict:
        from app.browser.page_observer import observe_page

        assert self.page is not None
        return observe_page(self.page)

    def execute(self, action: BrowserAction) -> dict[str, str]:
        _ensure_safe_action(action)
        assert self.page is not None
        target = action.target or ""
        if action.action == BrowserActionType.NAVIGATE:
            validate_public_target(action.value or "", self.test_case.allowed_domain)
            self.page.goto(
                action.value or "",
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
        elif action.action == BrowserActionType.CLICK:
            _visible_locator(self.page, target, action.action).click()
        elif action.action in {BrowserActionType.FILL, BrowserActionType.TYPE}:
            locator = _visible_locator(self.page, target, action.action)
            _ensure_safe_input(action, locator)
            locator.fill(action.value or "")
        elif action.action == BrowserActionType.SELECT:
            _visible_locator(self.page, target, action.action).select_option(
                label=action.value or ""
            )
        elif action.action == BrowserActionType.PRESS_KEY:
            _visible_locator(self.page, target, action.action).press(
                action.value or "Enter"
            )
        elif action.action == BrowserActionType.SCROLL:
            self.page.mouse.wheel(0, 650)
            self.page.wait_for_timeout(300)
        elif action.action == BrowserActionType.WAIT:
            self.page.wait_for_timeout(1000)
        elif action.action == BrowserActionType.FINISH:
            pass
        else:
            raise ValueError(f"Unsupported action: {action.action.value}")
        return {"url": self.page.url, "title": self.page.title()}

    def save_screenshot(self, step_number: int) -> str:
        assert self.page is not None
        filename = f"step_{step_number:03d}.png"
        path = self.screenshot_directory / filename
        self.page.screenshot(path=str(path), full_page=False, animations="disabled")
        return str(path.relative_to(self.run_directory))

    def close(self) -> None:
        try:
            if self.context is not None:
                self.context.close()
        finally:
            self.context = None
            try:
                if self.browser is not None:
                    self.browser.close()
            finally:
                self.browser = None
                if self.playwright is not None:
                    self.playwright.stop()
                    self.playwright = None
        if self.video_directory.is_dir():
            videos = sorted(self.video_directory.glob("*.webm"))
            if videos:
                target = self.video_directory / "journey.webm"
                if videos[0] != target:
                    videos[0].replace(target)
                for duplicate in videos[1:]:
                    duplicate.unlink(missing_ok=True)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
