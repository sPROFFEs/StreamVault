"""Register shared test options before pytest discovers nested test suites."""
import os


def pytest_addoption(parser):
    parser.addoption("--browser", action="store_true", help="Enable opt-in Chromium tests")
    parser.addoption("--viewport", choices=("all", "mobile", "desktop", "tv"), default="all",
                     help="Viewport selection for shared baseline browser fixtures")


def pytest_configure(config):
    if config.getoption("--browser"):
        os.environ["STREAMVAULT_BROWSER_TESTS"] = "1"
