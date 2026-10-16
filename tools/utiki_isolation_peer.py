#!/usr/bin/env python3
"""Controlled TCP/UDP peer for the uTiki hard-egress canary.

The peer runs only on a Docker ``--internal`` network.  It is not an egress
proxy and never handles production traffic.  Stable log markers let the outer
launcher distinguish positive controls from forbidden isolated-process hits.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import socketserver
import threading


DNS_POSITIVE = b"UTIKI-DNS-POSITIVE"
DNS_QUERY_MARKER = b"\x0cutiki-canary\x08internal"
QUIC_MARKER = b"UTIKI-QUIC-CANARY"


class CanaryHttpHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        print(f"PEER_EVENT tcp path={self.path}", flush=True)
        if self.path == "/browser-redirect":
            self.send_response(302)
            self.send_header("Location", "/browser-redirect-target")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", "1")
        self.end_headers()
        self.wfile.write(b"x")

    def log_message(self, format_string: str, *args: object) -> None:
        return


class Ipv6HttpServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


class CanaryUdpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data, connection = self.request
        if data == DNS_POSITIVE:
            kind = "dns-positive"
            response = b"DNS-POSITIVE-ACK"
        elif DNS_QUERY_MARKER in data:
            kind = "dns-query"
            response = b"DNS-QUERY-ACK"
        elif data == QUIC_MARKER:
            kind = "quic"
            response = b"QUIC-ACK"
        else:
            kind = "unknown"
            response = b"UNKNOWN"
        print(f"PEER_EVENT udp kind={kind}", flush=True)
        connection.sendto(response, self.client_address)


class ThreadingUdpServer(socketserver.ThreadingUDPServer):
    allow_reuse_address = True


def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("port must be between 1 and 65535")

    ipv4 = ThreadingHTTPServer(("0.0.0.0", args.port), CanaryHttpHandler)
    ipv6 = Ipv6HttpServer(("::", args.port), CanaryHttpHandler)
    udp = ThreadingUdpServer(("0.0.0.0", args.port), CanaryUdpHandler)
    servers = (ipv4, ipv6, udp)
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in servers[:-1]
    ]
    for thread in threads:
        thread.start()
    print("PEER_READY", flush=True)
    try:
        udp.serve_forever()
    finally:
        for server in servers[:-1]:
            server.shutdown()
            server.server_close()
        udp.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
