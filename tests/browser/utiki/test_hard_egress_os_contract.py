import os
import sys

import pytest

from tools import utiki_egress_canary as canary


pytestmark = pytest.mark.browser


def _isolated_peer():
    if os.environ.get(canary.ACTIVE_ENV) != "1":
        pytest.fail("OS egress probes require the hard-isolation launcher")
    if sys.platform != "linux":
        pytest.fail("OS egress probes require the Linux isolation target")
    assert canary._interfaces() == ["lo"]
    assert canary._ipv4_default_routes() == []
    assert canary._ipv6_default_routes() == []
    host = os.environ.get(canary.PEER_IPV4_ENV, "")
    port = int(os.environ.get(canary.PEER_PORT_ENV, "0"))
    assert host
    assert 1 <= port <= 65535
    return host, port


def test_h02_child_process_cannot_escape_network_namespace():
    _isolated_peer()

    result = canary._run_child_probe(canary._netns_identity())

    assert result["status"] == "PASS"
    assert result["netns"] == canary._netns_identity()
    assert result["interfaces"] == ["lo"]
    assert set(result["peer_probes"]) == {"direct-ipv4", "dns", "udp"}
    assert all(
        probe["status"] == "PASS" and probe["result"] == "blocked"
        for probe in result["peer_probes"].values()
    )


def test_h03_dns_is_blocked():
    host, port = _isolated_peer()

    result = canary._expect_blocked(
        "driver-dns", lambda: canary._dns_probe(host, port)
    )

    assert result["status"] == "PASS"
    assert result["result"] == "blocked"


def test_h03_direct_ipv4_is_blocked():
    host, port = _isolated_peer()

    result = canary._expect_blocked(
        "driver-direct-ipv4",
        lambda: canary._http_probe(host, port, "/driver-http"),
    )

    assert result["status"] == "PASS"
    assert result["result"] == "blocked"


def test_h03_direct_ipv6_is_blocked():
    _, port = _isolated_peer()
    host = os.environ.get(canary.PEER_IPV6_ENV, "")
    assert host

    result = canary._expect_blocked(
        "driver-direct-ipv6",
        lambda: canary._ipv6_probe(host, port),
    )

    assert result["status"] == "PASS"
    assert result["result"] == "blocked"


def test_h03_udp_quic_path_is_blocked():
    host, port = _isolated_peer()

    result = canary._expect_blocked(
        "driver-udp",
        lambda: canary._udp_probe(host, port),
    )

    assert result["status"] == "PASS"
    assert result["result"] == "blocked"
