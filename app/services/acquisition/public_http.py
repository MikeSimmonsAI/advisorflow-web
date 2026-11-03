"""HTTP for untrusted public websites, with a boundary around private networks.

Adapted from Harvey (https://github.com/ethanplusai/harvey, harvey/public_http.py)
for the EvoSys research worker: synchronous, used from a worker thread.

    MIT License - Copyright (c) 2026 Ethan Rogers
    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions: The above copyright
    notice and this permission notice shall be included in all copies or
    substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
    WITHOUT WARRANTY OF ANY KIND.

Each request (including every redirect) resolves the host and checks every
address, then connects to a checked numeric IP while Host and TLS SNI keep the
original name - so a prospect's website can never point the worker at the
backend's own network (SSRF / DNS rebinding). Environment proxies are not used.
"""
import ipaddress
import socket

import httpx

MAX_BYTES = 600_000


class UnsafeURLError(httpx.RequestError):
    """The requested destination is outside the public website boundary."""


_TRANSLATION_NETWORKS = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def public_address(value: str) -> str:
    if "%" in value:
        raise ValueError("scoped address")
    a = ipaddress.ip_address(value)
    if not a.is_global or a.is_multicast or a.is_reserved or a.is_unspecified:
        raise ValueError("not public")
    if isinstance(a, ipaddress.IPv6Address):
        if (a.is_site_local or a.ipv4_mapped or a.sixtofour or a.teredo
                or any(a in n for n in _TRANSLATION_NETWORKS)):
            raise ValueError("translated IPv6")
    return str(a)


class PublicHTTPTransport(httpx.BaseTransport):
    def __init__(self):
        self._t = httpx.HTTPTransport(trust_env=False, limits=httpx.Limits(max_keepalive_connections=0))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        host = url.raw_host.decode("ascii")
        low = host.rstrip(".").lower()
        if (url.scheme not in ("http", "https") or not host or url.username or url.password
                or "%" in host or low == "localhost" or low.endswith(".localhost")):
            raise UnsafeURLError("Only public HTTP(S) websites are allowed", request=request)
        port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
        try:
            try:
                addrs = [public_address(host)]
            except ValueError:
                if _is_ip(host):
                    raise
                rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                addrs = list(dict.fromkeys(public_address(r[4][0]) for r in rows))
                if not addrs:
                    raise ValueError("no addresses")
                addrs.sort(key=lambda v: ipaddress.ip_address(v).version)
        except ValueError as exc:
            raise UnsafeURLError("Website resolves to a non-public destination", request=request) from exc
        except OSError as exc:
            raise httpx.ConnectError("Website DNS resolution failed", request=request) from exc
        headers = request.headers.copy()
        headers["Host"] = url.netloc.decode("ascii")
        for i, addr in enumerate(addrs):
            ext = dict(request.extensions)
            ext["sni_hostname"] = host
            pinned = httpx.Request(method=request.method, url=url.copy_with(host=addr), headers=headers,
                                   stream=request.stream, extensions=ext)
            try:
                return self._t.handle_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if i == len(addrs) - 1:
                    raise
        raise httpx.ConnectError("no address connected", request=request)

    def close(self) -> None:
        self._t.close()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def client(timeout: float = 12.0) -> httpx.Client:
    return httpx.Client(transport=PublicHTTPTransport(), trust_env=False, timeout=timeout,
                        follow_redirects=True, max_redirects=5,
                        headers={"User-Agent": "EvoSysResearch/1.0 (+https://evosyspro.live)"})


def get_text(c: httpx.Client, url: str):
    """(final_url, html) or (None, reason). Never more than MAX_BYTES read."""
    try:
        with c.stream("GET", url) as r:
            if r.status_code >= 400:
                return None, "HTTP %d" % r.status_code
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "text" not in ctype:
                return None, "not a web page"
            buf = b""
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) >= MAX_BYTES:
                    break
            return str(r.url), buf.decode(r.encoding or "utf-8", errors="replace")
    except UnsafeURLError:
        return None, "blocked: not a public website"
    except httpx.HTTPError as exc:
        return None, type(exc).__name__
