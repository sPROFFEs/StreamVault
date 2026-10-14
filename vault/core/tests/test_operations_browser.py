"""Operational panels and opt-in profiles use the retained application shell."""
import json
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect
from starlette.requests import Request

from shared_templates import templates

pytestmark = pytest.mark.browser


def operational_payloads(driver):
    driver.payloads.update({
        "/admin/system/update/config": (200, {"mode": "branch", "provider": "auto", "api_url": "", "repository": "",
            "automatic_checks": True, "check_interval_hours": 24, "has_token": True}),
        "/admin/api/system/monitor": (200, {"version": "1.0.0", "cpu_percent": 25, "memory": {"percent": 30},
            "disk": {"percent": 50}, "process": {"uptime_seconds": 7200}, "services": [{"name": "core", "state": "running", "restarts": 0}]}),
        "/admin/system/update/history": (200, {"items": [{"state": "success", "operation": "update", "message": "Healthy", "updated_at": 1}]}),
        "/admin/system/update/backups": (200, {"items": [{"name": "20261014-120000-abcdef12.zip", "size_bytes": 1024}]}),
        "/admin/api/federation/config": (200, {"enabled": False, "node_name": "Fixture node"}),
        "/admin/api/federation/peers": (200, {"items": [{"id": 1, "name": "Remote node", "status": "online", "base_url": "https://peer.test", "enabled": True}]}),
        "/admin/api/federation/grants": (200, {"items": []}),
    })


def test_monitor_and_federation_tabs_keep_layout_and_feedback(baseline_page):
    operational_payloads(baseline_page)
    page = baseline_page.page
    page.goto(baseline_page.base_url + "/admin?tab=system")
    expect(page.locator("#system-cpu")).to_have_text("25%")
    expect(page.locator("#system-services")).to_contain_text("running")
    expect(page.locator("#system-history")).to_contain_text("Healthy")
    expect(page.locator("#system-backups a")).to_have_attribute("href", "/admin/system/update/backups/20261014-120000-abcdef12.zip")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("tab", name="Federation", exact=True).click()
    expect(page.locator("#federation-config [name=node_name]")).to_have_value("Fixture node")
    expect(page.locator("#federation-peers")).to_contain_text("Remote node")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    baseline_page.payloads["/admin/api/federation/config"] = (503, {"detail": "Node configuration unavailable"})
    page.locator("#federation-config button").click()
    expect(page.locator("#federation-feedback")).to_contain_text("Node configuration unavailable")


def test_release_source_save_keeps_existing_credential_and_escapes_notes(baseline_page):
    operational_payloads(baseline_page)
    page = baseline_page.page
    page.goto(baseline_page.base_url + "/admin?tab=overview")
    expect(page.locator("#update-source-result")).to_contain_text("hidden")
    page.locator("details:has(#update-source-form) summary").click()
    page.locator("#update-source-form [name=mode]").select_option("release")
    page.locator("#update-source-form [name=provider]").select_option("gitea")
    writes = []
    def save(route):
        writes.append(route.request.post_data_json)
        route.fulfill(json={**writes[-1], "has_token": True})
    page.route("**/admin/system/update/config", lambda route: save(route) if route.request.method == "PUT"
               else route.fulfill(json=baseline_page.payloads["/admin/system/update/config"][1]))
    baseline_page.payloads["/admin/system/update/check"] = (200, {"state": "available", "release_tag": "v1.1.0", "installed_version": "1.0.0",
        "release_notes": "<img src=x onerror=window.injected=true>"})
    page.locator("#update-source-form button").click()
    expect(page.locator("#update-release")).to_contain_text("v1.1.0")
    expect(page.locator("#update-notes")).to_contain_text("<img")
    assert "api_token" not in writes[0]
    assert writes[0]["mode"] == "release"
    assert page.evaluate("window.injected === undefined")


def test_grant_token_is_only_shown_once_and_revocation_requires_confirmation(baseline_page):
    operational_payloads(baseline_page)
    page = baseline_page.page
    page.route("**/admin/api/federation/grants", lambda route: route.fulfill(json={"token": "secret-test-token"})
               if route.request.method == "POST" else route.fulfill(json={"items": [{"id": 2, "name": "Peer", "share_media": True}]}))
    page.goto(baseline_page.base_url + "/admin?tab=federation")
    page.locator("#federation-grant [name=name]").fill("Peer")
    page.locator("#federation-grant button").click()
    expect(page.locator("#federation-token")).to_have_value("secret-test-token")
    page.locator("#federation-token-hide").click()
    expect(page.locator("#federation-issued")).to_be_hidden()
    expect(page.locator("#federation-token")).to_have_value("")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#federation-grants button").click()
    assert not any(method == "DELETE" and path.endswith("/grants/2") for method, path, _ in baseline_page.requests)


def test_community_privacy_save_and_remote_metrics_are_not_html(baseline_page):
    page = baseline_page.page
    profile = {"id": 1, "display_name": "Viewer", "bio": "", "visibility": "private", "show_metrics": False,
               "metrics": {"movies_completed": 2, "episodes_completed": 3, "favorites": 1, "entries_started": 5}}
    baseline_page.payloads.update({"/api/profiles/me": (200, profile), "/api/profiles": (200, {"items": []}),
        "/api/federation/peers": (200, {"items": [{"id": 1, "name": "Other node", "status": "online"}]}),
        "/api/federation/peers/1/profiles": (200, {"items": [{"id": "1", "display_name": "<script>bad()</script>", "bio": "Hello",
            "node_name": "Other node", "metrics": {"movies_completed": 4}}]})})
    scope = {"type": "http", "headers": [(b"cookie", b"lang=en")], "state": {"user": SimpleNamespace(username="baseline-viewer", is_admin=False, can_download=False)}, "query_string": b""}
    html = templates.get_template("community.html").render(request=Request(scope))
    page.route("**/community", lambda route: route.fulfill(content_type="text/html", body=html))
    page.goto(baseline_page.base_url + "/community")
    expect(page.locator("#profile-form [name=visibility]")).to_have_value("private")
    expect(page.locator("#my-profile-metrics")).to_contain_text("Movies completed")
    writes = []
    page.route("**/api/profiles/me", lambda route: (writes.append(route.request.post_data_json), route.fulfill(json={**profile, **writes[-1]}))
               if route.request.method == "PUT" else route.fulfill(json=profile))
    page.locator("#profile-form [name=visibility]").select_option("federated")
    page.locator("#profile-form [name=show_metrics]").check()
    page.locator("#profile-form button").click()
    expect(page.locator("#community-feedback")).to_contain_text("No shared profiles")
    assert writes[0]["visibility"] == "federated"
    assert writes[0]["show_metrics"] is True
    page.locator("#community-node").select_option("1")
    expect(page.locator("#community-profiles")).to_contain_text("<script>bad()</script>")
    assert page.locator("#community-profiles script").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
