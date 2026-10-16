import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import tempfile

import pytest
import zendriver as uc
from zendriver import cdp


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from platforms.utiki.profiles import PageKind, classify_utiki_page


pytestmark = pytest.mark.browser

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "ctbc" / "UTK0201_date.html"
)
PRODUCTION_FIXTURE_URL = (
    "https://tix.ctbcsports.com/BROTHERS/UTK0201_?PRODUCT_ID=PRODUCT_FIXTURE"
)


def _write_report(payload):
    raw_path = os.environ.get("TICKETS_HUNTER_PRODUCTION_ORIGIN_REPORT", "")
    if not raw_path:
        pytest.fail("production-origin report path is absent")
    path = Path(raw_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def test_h04_production_origin_route_is_unpatched_and_locally_fulfilled():
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.fail("production-origin fixture requires hard-egress isolation")
    asyncio.run(_run_production_origin_fixture())


async def _run_production_origin_fixture():
    browser = None
    profile = tempfile.TemporaryDirectory(prefix="tickets-hunter-origin-fixture-")
    intercepted = []
    rejected = []
    handler_errors = []
    fixture = FIXTURE_PATH.read_text(encoding="utf-8").replace(
        "<head>", '<head><link rel="icon" href="data:,">', 1
    )
    fixture_body = base64.b64encode(fixture.encode("utf-8")).decode("ascii")
    try:
        browser = await uc.start(
            user_data_dir=profile.name,
            headless=True,
            browser_args=[
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--no-default-browser-check",
                "--no-first-run",
            ],
        )
        tab = browser.main_tab

        async def handle_request(event):
            try:
                request_url = event.request.url
                intercepted.append(request_url)
                if request_url == PRODUCTION_FIXTURE_URL:
                    await tab.send(
                        cdp.fetch.fulfill_request(
                            request_id=event.request_id,
                            response_code=200,
                            response_headers=[
                                cdp.fetch.HeaderEntry(
                                    name="Content-Type",
                                    value="text/html; charset=utf-8",
                                ),
                                cdp.fetch.HeaderEntry(
                                    name="Cache-Control",
                                    value="no-store",
                                ),
                            ],
                            body=fixture_body,
                        )
                    )
                else:
                    rejected.append(request_url)
                    await tab.send(
                        cdp.fetch.fail_request(
                            request_id=event.request_id,
                            error_reason=cdp.network.ErrorReason.ACCESS_DENIED,
                        )
                    )
            except Exception as exc:
                handler_errors.append(type(exc).__name__)

        tab.add_handler(cdp.fetch.RequestPaused, handle_request)
        await tab.send(
            cdp.fetch.enable(
                [
                    cdp.fetch.RequestPattern(
                        url_pattern="*",
                        request_stage=cdp.fetch.RequestStage.REQUEST,
                    )
                ]
            )
        )
        await asyncio.wait_for(tab.get(PRODUCTION_FIXTURE_URL), timeout=10.0)
        actual_url = await tab.evaluate("location.href")
        actual_origin = await tab.evaluate("location.origin")
        title = await tab.evaluate("document.title")
        table_present = await tab.evaluate(
            "Boolean(document.querySelector('#PerformanceListTable'))"
        )

        assert actual_url == PRODUCTION_FIXTURE_URL
        assert actual_origin == "https://tix.ctbcsports.com"
        assert classify_utiki_page(actual_url) is PageKind.DATE
        assert title == "CTBC date fixture"
        assert table_present is True
        assert intercepted.count(PRODUCTION_FIXTURE_URL) == 1
        assert rejected == []
        assert handler_errors == []
        _write_report(
            {
                "schema_version": 1,
                "report_kind": "utiki-production-origin-fixture",
                "status": "PASS",
                "run_id": os.environ.get("TICKETS_HUNTER_ISOLATION_RUN_ID", ""),
                "candidate_sha": os.environ.get(
                    "TICKETS_HUNTER_CANDIDATE_SHA", ""
                ),
                "origin": actual_origin,
                "path": "/BROTHERS/UTK0201_",
                "classifier": "DATE",
                "interception_stage": "Request",
                "interception_installed": True,
                "classifier_patched": False,
                "allowlist_entries": 1,
                "fulfilled_requests": 1,
                "rejected_unknown_requests": 0,
                "live_authorization_used": False,
            }
        )
    finally:
        try:
            if browser is not None:
                tab = browser.main_tab
                try:
                    tab.remove_handlers(cdp.fetch.RequestPaused)
                    await tab.send(cdp.fetch.disable())
                except Exception:
                    pass
                await browser.stop()
        finally:
            profile.cleanup()
