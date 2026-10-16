#!/usr/bin/env python3
"""Install debug APK, exercise offline feedback, then render the real hosted UI.

Requires a running server and booted emulator/device. Clears only the debug app's
test data. Optional authenticated checks use an existing test account; this runner
does not create accounts or invoke deployment updates. Requires Playwright for
those checks and a playable movie for --media-id.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default="http://10.0.2.2:8000")
    parser.add_argument("--serial")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--expect-text", default="StreamVault")
    parser.add_argument("--username", help="Optional existing test account for authenticated flow checks")
    parser.add_argument("--password-env", default="STREAMVAULT_SMOKE_PASSWORD")
    parser.add_argument("--media-id", help="Optional playable movie ID on the selected server")
    parser.add_argument("--search", default="", help="Catalog query used by authenticated checks")
    parser.add_argument("--disconnect-port", type=int, help="Optional adb reverse port to disconnect and restore (use 127.0.0.1 server URL)")
    args = parser.parse_args()
    if args.username and not os.environ.get(args.password_env):
        parser.error("Set the password environment variable for authenticated checks.")
    if args.disconnect_port and urlsplit(args.server_url).hostname != "127.0.0.1":
        parser.error("Disconnection checks require a 127.0.0.1 server URL and adb reverse.")
    url = urlsplit(args.server_url)
    if (url.scheme not in {"http", "https"} or not url.hostname
            or not re.fullmatch(r"[A-Za-z0-9.-]+", url.hostname) or url.username
            or url.password or url.path not in {"", "/"} or url.query or url.fragment):
        parser.error("Use a server origin without credentials, a path, query or fragment.")
    adb = [args.adb] + (["-s", args.serial] if args.serial else [])
    project = Path(__file__).resolve().parents[1]
    report_dir = project / "app/build/reports/device-smoke"
    report_dir.mkdir(parents=True, exist_ok=True)

    def run(*command):
        return subprocess.run(adb + list(command), check=True, capture_output=True, timeout=30).stdout

    def hierarchy():
        run("shell", "uiautomator", "dump", "/sdcard/streamvault-smoke.xml")
        return ET.fromstring(run("shell", "cat", "/sdcard/streamvault-smoke.xml"))

    def wait_for(predicate, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            tree = hierarchy()
            result = predicate(tree)
            if result is not None and result is not False:
                return result
            time.sleep(.5)
        raise AssertionError("Expected UI did not appear; inspect device and server connectivity.")

    def node_with_text(tree, text):
        return next((node for node in tree.iter("node")
                     if node.get("text", "").casefold() == text.casefold()), None)

    def tap(node):
        left, top, right, bottom = map(int, re.findall(r"\d+", node.get("bounds")))
        run("shell", "input", "tap", str((left + right) // 2), str((top + bottom) // 2))

    def enter_server(address):
        field = wait_for(lambda tree: next((node for node in tree.iter("node")
                                          if node.get("class") == "android.widget.EditText"), None))
        tap(field)
        run("shell", "input", "keyevent", "KEYCODE_MOVE_END")
        # Clear the previous non-secret origin without clipboard dependencies.
        for _ in range(len(field.get("text", ""))):
            run("shell", "input", "keyevent", "KEYCODE_DEL")
        run("shell", "input", "text", address)
        tap(wait_for(lambda tree: node_with_text(tree, "Connect")))

    package = "com.streamvault.app.debug"
    provider_state = run("shell", "dumpsys", "webviewupdate").decode()
    if "Current WebView package is null" in provider_state:
        raise RuntimeError("The device has no WebView provider. Enable Android System WebView or use a Google APIs emulator image.")
    version = re.search(r"Current WebView package.*?\(.*?, (\d+)\.", provider_state)
    if version and int(version.group(1)) < 108:
        raise RuntimeError("Update the device WebView provider to Chromium 108 or newer before exercising the hosted UI.")
    def open_settings(tree):
        return (node_with_text(tree, "Server")
                or next((node for node in tree.iter("node")
                         if "Configure server" in node.get("content-desc", "")), None))

    run("install", "-r", str(project / "app/build/outputs/apk/debug/app-debug.apk"))
    run("shell", "pm", "clear", package)
    run("shell", "am", "start", "-W", "-n", package + "/com.streamvault.app.MainActivity")
    enter_server("http://127.0.0.1:9")
    wait_for(lambda tree: node_with_text(tree, "Try again"))
    tap(wait_for(open_settings))
    enter_server(args.server_url)

    def has_application(tree):
        for node in tree.iter("node"):
            if node.get("class") == "android.webkit.WebView":
                rendered = " ".join(child.get("text", "") + " " + child.get("content-desc", "")
                                    for child in node.iter("node"))
                if args.expect_text.casefold() in rendered.casefold():
                    return tree
        return None

    tree = wait_for(has_application)
    authenticated = {}
    if args.username:
        # Debug WebView inspection drives real DOM/forms; native Back and toolbar
        # controls still go through adb. Never write the password to reports.
        from playwright.sync_api import sync_playwright, expect

        def attach(playwright):
            pid = run("shell", "pidof", package).decode().strip().split()[0]
            run("forward", "tcp:9223", "localabstract:webview_devtools_remote_" + pid)
            browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9223")
            return browser, browser.contexts[0].pages[0]

        with sync_playwright() as playwright:
            browser, page = attach(playwright)
            page.locator("#username").click()
            page.locator("#username").fill(args.username)
            page.locator("#password").click()
            page.locator("#password").fill(os.environ[args.password_env])
            run("shell", "input", "keyevent", "KEYCODE_BACK")  # IME first.
            page.locator('button[type="submit"]').click()
            page.wait_for_url(lambda target: "/login" not in str(target))
            authenticated["login_and_keyboard_back"] = "passed"
            page.goto(args.server_url.rstrip("/") + "/browse?type=movie")
            expect(page.locator("#browse-grid")).to_have_attribute("aria-busy", "false")
            if args.search:
                page.locator("#browse-search").click()
                page.locator("#browse-search").fill(args.search)
                expect(page.locator("#browse-grid a").first).to_be_visible()
                run("shell", "input", "keyevent", "KEYCODE_BACK")
            authenticated["browse_and_search"] = "passed" if args.search else "browse passed; search not requested"
            catalog_url = page.url
            page.locator("#search-trigger").click()
            run("shell", "input", "keyevent", "KEYCODE_BACK")  # IME may own the first Back.
            if page.get_by_role("dialog", name="Search").is_visible():
                run("shell", "input", "keyevent", "KEYCODE_BACK")
            expect(page.get_by_role("dialog", name="Search")).not_to_be_visible()
            assert page.url == catalog_url
            page.goto(args.server_url.rstrip("/") + "/settings")
            run("shell", "input", "keyevent", "KEYCODE_BACK")
            page.wait_for_url(catalog_url)
            authenticated["overlay_and_navigation_back"] = "passed"
            page.reload()
            page.wait_for_load_state("load")
            assert page.url == catalog_url
            authenticated["reload_preserves_route"] = "passed"
            if args.media_id:
                page.goto(args.server_url.rstrip("/") + "/media/" + args.media_id + "?type=movie")
                page.locator("#play-button").click()
                page.wait_for_function("document.querySelector('video')?.currentTime > 1", timeout=60000)
                page.locator("#fullscreen-button").click()
                page.wait_for_function("innerWidth > innerHeight", timeout=20000)
                run("shell", "input", "keyevent", "KEYCODE_BACK")
                page.wait_for_function("!document.fullscreenElement && !document.querySelector('.player-box--pseudo-fs')")
                assert "/media/" in page.url
                authenticated["playback_fullscreen_and_back"] = "passed"
            # Native confirmations are required by the web deployment updater.
            page.evaluate("""() => {
                const button = document.createElement('button'); button.id = 'native-confirm-test';
                button.textContent = 'Check confirmation';
                button.onclick = () => { document.body.dataset.confirmed = String(confirm('Device confirmation check')); };
                document.body.prepend(button);
            }""")
            page.locator("#native-confirm-test").click(no_wait_after=True)
            tap(wait_for(lambda tree: node_with_text(tree, "Cancel")))
            page.wait_for_function("document.body.dataset.confirmed === 'false'")
            page.locator("#native-confirm-test").click(no_wait_after=True)
            tap(wait_for(lambda tree: node_with_text(tree, "Continue")))
            page.wait_for_function("document.body.dataset.confirmed === 'true'")
            authenticated["native_confirmation"] = "passed"
            if args.disconnect_port:
                route = page.url
                port = "tcp:" + str(args.disconnect_port)
                run("reverse", "--remove", port)
                try:
                    page.reload()
                    wait_for(lambda tree: node_with_text(tree, "Try again"))
                finally:
                    run("reverse", port, port)
                tap(wait_for(lambda tree: node_with_text(tree, "Try again")))
                wait_for(has_application)
                assert page.url == route
                authenticated["offline_and_retry_preserves_route"] = "passed"
            browser.close()
            run("shell", "am", "force-stop", package)
            run("shell", "am", "start", "-W", "-n", package + "/com.streamvault.app.MainActivity")
            wait_for(has_application)
            browser, page = attach(playwright)
            assert "/login" not in page.url
            assert page.locator("#username").count() == 0
            authenticated["authentication_survives_process_restart"] = "passed"
            browser.close()
        tree = hierarchy()
    ET.ElementTree(tree).write(report_dir / "hierarchy.xml", encoding="utf-8")
    (report_dir / "launch.png").write_bytes(run("exec-out", "screencap", "-p"))
    result = {"package": package, "server": args.server_url, "offline_feedback": "passed",
              "hosted_application_rendered": "passed", "expected_text": args.expect_text,
              "authenticated_flows": authenticated,
              "android_api": run("shell", "getprop", "ro.build.version.sdk").decode().strip()}
    (report_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
