"""Offline updater feedback and concurrency at the shared viewport sizes."""
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def test_current_and_network_failure_are_distinct(baseline_page):
    page = baseline_page.page
    page.goto(f"{baseline_page.base_url}/admin?tab=overview")
    expect(page.locator("#update-badge")).to_have_text("Update available")
    expect(page.locator("#update-branch")).to_have_text("configured / release")
    expect(page.locator("#update-local")).to_have_text("a" * 40)
    page.locator("#btn-update-check").press("Enter")
    expect(page.locator("#update-badge")).to_have_text("Up to date")
    expect(page.locator("#btn-update-pull")).to_be_disabled()
    baseline_page.payloads["/admin/system/update/check"] = (502, {"state": "failure", "detail": "Fixture network failure"})
    page.locator("#btn-update-check").press("Enter")
    expect(page.locator("#update-result")).to_contain_text("Fixture network failure")
    expect(page.locator("#update-badge")).to_have_text("Failed")
    expect(page.locator("#btn-update-check")).to_be_enabled()
    baseline_page.payloads["/admin/system/update/check"] = (200, {"state": "current", "requires_rebuild": True, "message": "Code is current; a rebuild is still required."})
    page.locator("#btn-update-check").press("Enter")
    expect(page.locator("#update-badge")).to_have_text("Rebuild required")
    expect(page.locator("#btn-update-pull")).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_queued_update_locks_all_actions_until_verified_success(baseline_page):
    page = baseline_page.page
    page.goto(f"{baseline_page.base_url}/admin?tab=overview")
    expect(page.locator("#btn-update-pull")).to_be_enabled()
    baseline_page.payloads["/admin/system/update/status"] = (200, {"state": "updating", "message": "Fixture building images"})
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#btn-update-pull").press("Enter")
    expect(page.locator("#update-badge")).to_have_text("Updating…")
    for name in ("pull", "check", "restart", "recreate"):
        expect(page.locator(f"#btn-update-{name}")).to_be_disabled()
    expect(page.locator("#system-update")).to_have_attribute("aria-busy", "true")
    page.evaluate("document.querySelector('#btn-update-pull').click()")
    assert sum(path.endswith("git-pull") for _, path, _ in baseline_page.requests) == 1
    baseline_page.payloads["/admin/system/update/status"] = (200, {"state": "success", "message": "Deployment completed and core is healthy."})
    expect(page.locator("#update-badge")).to_have_text("Completed", timeout=8000)
    expect(page.locator("#update-result")).to_contain_text("core is healthy")
    expect(page.locator("#btn-update-check")).to_be_enabled()
    expect(page.locator("#btn-update-pull")).to_be_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_cancelled_update_and_restart_send_no_mutation(baseline_page):
    page = baseline_page.page
    page.goto(f"{baseline_page.base_url}/admin?tab=overview")
    expect(page.locator("#btn-update-pull")).to_be_enabled()
    page.on("dialog", lambda dialog: dialog.dismiss())
    page.locator("#btn-update-pull").press("Enter")
    page.locator("#btn-update-restart").press("Enter")
    assert not any(method == "POST" and "/system/update/" in path for method, path, _ in baseline_page.requests)


def test_checking_feedback_blocks_overlapping_checks(baseline_page):
    page = baseline_page.page
    page.goto(f"{baseline_page.base_url}/admin?tab=overview")
    expect(page.locator("#btn-update-pull")).to_be_enabled()
    pending = []
    page.route("**/admin/system/update/check", lambda route: pending.append(route))
    page.locator("#btn-update-check").press("Enter")
    expect(page.locator("#update-badge")).to_have_text("Checking…")
    expect(page.locator("#system-update")).to_have_attribute("aria-busy", "true")
    for name in ("pull", "check", "restart", "recreate"):
        expect(page.locator(f"#btn-update-{name}")).to_be_disabled()
    page.evaluate("document.querySelector('#btn-update-check').click()")
    assert len(pending) == 1
    pending[0].fulfill(json={"state": "current", "message": "The configured branch is current."})
    expect(page.locator("#update-badge")).to_have_text("Up to date")
    expect(page.locator("#btn-update-check")).to_be_enabled()
