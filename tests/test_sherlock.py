"""/Sherlock service: site filtering, result parsing, honest disclaimers."""

from __future__ import annotations

from src.commands.base import dispatch
from src.services.sherlock import DEFAULT_SITES, SherlockError, SherlockMatch, SherlockService
from tests.conftest import StubSherlock


class FakeQueryStatus:
    CLAIMED = "claimed"
    AVAILABLE = "available"


class FakeQueryResult:
    def __init__(self, status):
        self.status = status


class FakeSiteInfo:
    def __init__(self, name, information, is_nsfw=False):
        self.name = name
        self.information = information
        self.is_nsfw = is_nsfw


class FakeSitesInformation:
    def __init__(self, data_file_path=None, honor_exclusions=True, do_not_exclude=()):
        self._sites = {
            "GitHub": FakeSiteInfo("GitHub", {"urlMain": "https://github.com/", "url": "https://github.com/{}"}),
            "X": FakeSiteInfo("X", {"urlMain": "https://x.com/", "url": "https://x.com/{}"}),
            "NSFWPlace": FakeSiteInfo("NSFWPlace", {"urlMain": "https://nsfw.example/"}, is_nsfw=True),
        }

    def remove_nsfw_sites(self, do_not_remove=()):
        self._sites = {k: v for k, v in self._sites.items() if not v.is_nsfw}

    def __iter__(self):
        return iter(self._sites.values())


def test_sites_filtered_to_configured_subset():
    svc = SherlockService(enabled=True, sites=("GitHub",), timeout=5)
    svc._load = lambda: (
        lambda *a, **k: {},
        FakeSitesInformation,
        object,
        FakeQueryStatus,
    )
    data = svc._sites(FakeSitesInformation)
    assert set(data) == {"GitHub"}


def test_unknown_site_names_fall_back_to_defaults():
    svc = SherlockService(enabled=True, sites=("NotASite",), timeout=5)
    data = svc._sites(FakeSitesInformation)
    assert set(data) == {"GitHub", "X"}  # nsfw excluded, full (filtered) list


def test_nsfw_sites_removed():
    svc = SherlockService(enabled=True, sites=(), timeout=5)
    data = svc._sites(FakeSitesInformation)
    assert "NSFWPlace" not in data


def test_default_sites_is_nonempty_subset():
    assert len(DEFAULT_SITES) >= 10


def test_lookup_parses_claimed_results():
    svc = SherlockService(enabled=True, sites=("GitHub",), timeout=5)
    svc._load = lambda: (
        lambda username, site_data, notify, **kw: {
            "GitHub": {
                "url_main": "https://github.com/",
                "url_user": "https://github.com/torvalds",
                "status": FakeQueryResult(FakeQueryStatus.CLAIMED),
            },
            "X": {
                "url_main": "https://x.com/",
                "url_user": "https://x.com/torvalds",
                "status": FakeQueryResult(FakeQueryStatus.AVAILABLE),
            },
        },
        FakeSitesInformation,
        object,
        FakeQueryStatus,
    )
    matches = svc.lookup("torvalds")
    assert matches == [SherlockMatch("GitHub", "https://github.com/torvalds")]


def test_disabled_service_raises_clear_error():
    svc = SherlockService(enabled=False)
    try:
        svc.lookup("x")
        assert False, "expected SherlockError"
    except SherlockError as exc:
        assert "disabled" in exc.message.lower()


def test_command_disclaimer_present(make_ctx):
    stub = StubSherlock(matches=[SherlockMatch("GitHub", "https://github.com/torvalds")])
    result = dispatch(make_ctx(sherlock=stub), "/Sherlock torvalds")
    assert "https://github.com/torvalds" in result.text
    assert "do not prove" in result.text.lower() or "not prove" in result.text.lower()


def test_command_no_results_message(make_ctx):
    result = dispatch(make_ctx(sherlock=StubSherlock(matches=[])), "/Sherlock nobodyhere")
    assert "no public profiles" in result.text.lower()


def test_command_error_reported(make_ctx):
    stub = StubSherlock(error=SherlockError("sweep exceeded 90s"))
    result = dispatch(make_ctx(sherlock=stub), "/Sherlock someone")
    assert "90s" in result.text
