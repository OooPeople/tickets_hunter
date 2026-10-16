import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading

import pytest
import zendriver as uc


pytestmark = pytest.mark.browser

_RESULTS = None
_RESULTS_LOCK = threading.Lock()
_PROBES = (
    "browser-subresource",
    "browser-redirect",
    "browser-websocket",
    "browser-worker",
)


def _require_isolated_launcher():
    if os.environ.get("TICKETS_HUNTER_HARD_EGRESS_ACTIVE") != "1":
        pytest.fail("browser egress canary must run through the hard-isolation launcher")
    if os.environ.get("TICKETS_HUNTER_RUN_BROWSER_FIXTURES") != "1":
        pytest.fail("browser fixture enable flag is absent")
    peer = os.environ.get("TICKETS_HUNTER_EGRESS_CANARY_IPV4", "")
    port = os.environ.get("TICKETS_HUNTER_EGRESS_CANARY_PORT", "")
    if not peer or not port.isdecimal():
        pytest.fail("controlled peer endpoint is absent")
    return peer, int(port)


def _all_results():
    global _RESULTS
    with _RESULTS_LOCK:
        if _RESULTS is None:
            peer, port = _require_isolated_launcher()
            _RESULTS = asyncio.run(_run_browser_probes(peer, port))
            _write_probe_report(_RESULTS)
    return _RESULTS


def _write_probe_report(results):
    raw_path = os.environ.get("TICKETS_HUNTER_BROWSER_PROBE_REPORT", "")
    if not raw_path:
        pytest.fail("browser probe report path is absent")
    path = Path(raw_path)
    payload = {
        "schema_version": 1,
        "report_kind": "utiki-browser-network-probes",
        "run_id": os.environ.get("TICKETS_HUNTER_ISOLATION_RUN_ID", ""),
        "candidate_sha": os.environ.get("TICKETS_HUNTER_CANDIDATE_SHA", ""),
        "interception_installed": False,
        "probes": {
            name: {"status": "PASS", "browser_result": results[name]}
            for name in _PROBES
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


async def _run_browser_probes(peer, port):
    browser = None
    profile = tempfile.TemporaryDirectory(prefix="tickets-hunter-egress-canary-")
    target = f"http://{peer}:{port}"
    try:
        browser = await uc.start(
            user_data_dir=profile.name,
            headless=True,
            sandbox=False,
            browser_args=[
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--no-default-browser-check",
                "--no-first-run",
            ],
        )
        tab = await browser.get("data:text/html,<title>uTiki isolation canary</title>")
        script = f"""(async () => {{
            const target = {json.dumps(target)};
            const bounded = (promise) => Promise.race([
                promise,
                new Promise((resolve) => setTimeout(
                    () => resolve('blocked-timeout'), 1500
                )),
            ]);
            const imageProbe = () => bounded(new Promise((resolve) => {{
                const image = new Image();
                image.onload = () => resolve('unexpected-success');
                image.onerror = () => resolve('blocked-error');
                image.src = target + '/browser-subresource';
            }}));
            const redirectProbe = () => bounded(
                fetch(target + '/browser-redirect', {{redirect: 'follow'}})
                    .then(() => 'unexpected-success')
                    .catch(() => 'blocked-error')
            );
            const websocketProbe = () => bounded(new Promise((resolve) => {{
                const socket = new WebSocket(
                    target.replace('http://', 'ws://') + '/browser-websocket'
                );
                socket.onopen = () => {{
                    socket.close();
                    resolve('unexpected-success');
                }};
                socket.onerror = () => resolve('blocked-error');
                socket.onclose = () => resolve('blocked-close');
            }}));
            const workerProbe = () => bounded(new Promise((resolve) => {{
                const source = `onmessage = async (event) => {{
                    try {{
                        await fetch(event.data + '/browser-worker');
                        postMessage('unexpected-success');
                    }} catch (error) {{
                        postMessage('blocked-error');
                    }}
                }}`;
                const worker = new Worker(URL.createObjectURL(
                    new Blob([source], {{type: 'text/javascript'}})
                ));
                worker.onmessage = (event) => {{
                    worker.terminate();
                    resolve(event.data);
                }};
                worker.onerror = () => {{
                    worker.terminate();
                    resolve('blocked-worker-error');
                }};
                worker.postMessage(target);
            }}));
            const values = await Promise.all([
                imageProbe(), redirectProbe(), websocketProbe(), workerProbe()
            ]);
            return {{
                'browser-subresource': values[0],
                'browser-redirect': values[1],
                'browser-websocket': values[2],
                'browser-worker': values[3],
            }};
        }})()"""
        results = await tab.evaluate(script, await_promise=True, return_by_value=True)
        if not isinstance(results, dict):
            raise AssertionError("browser isolation probe returned an invalid result")
        return results
    finally:
        try:
            if browser is not None:
                await browser.stop()
        finally:
            profile.cleanup()


def test_h01_interception_disabled_is_blocked():
    results = _all_results()

    assert all(results[name].startswith("blocked") for name in _PROBES)


@pytest.mark.parametrize("probe", _PROBES, ids=_PROBES)
def test_h02_browser_protocol_is_blocked(probe):
    result = _all_results()[probe]
    assert result.startswith("blocked"), result
