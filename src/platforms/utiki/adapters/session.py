"""Browser-observed session boundary for the isolated uTiki owner.

An observed scoped cookie identifies a browser session generation.  It does not
claim that the server accepted an inline login or a purchase submission.
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlsplit

from zendriver import cdp

from ..auth import (
    cookie_domain_is_in_scope, get_tenant_auth_profile,
    parse_ctbc_cookie_string,
)
from ..application.ports import InlineLoginCredentials, PurchaseAuthority, VerifiedLoginAcceptance
from ..domain.identity import AuthEpoch, TabKey
from ..profiles import PageKind, classify_utiki_page, get_utiki_tenant_profile


def _cookie_matches_page(cookie: object, host: str, path: str) -> bool:
    raw_domain = getattr(cookie, "domain", None)
    raw_path = getattr(cookie, "path", None)
    if not isinstance(raw_domain, str) or not isinstance(raw_path, str):
        return False
    domain = raw_domain.lstrip(".").lower()
    domain_cookie = raw_domain.startswith(".") or domain == "ctbcsports.com"
    if host != domain and not (domain_cookie and host.endswith("." + domain)):
        return False
    cookie_path = raw_path or "/"
    return path == cookie_path or path.startswith(cookie_path.rstrip("/") + "/")


def _session_fingerprint(url: str, cookies: object) -> tuple[str, str] | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    tenant = get_utiki_tenant_profile(url)
    auth = get_tenant_auth_profile(url)
    if parsed.scheme != "https" or tenant is None or auth is None or not auth.supports_cookie_login:
        return None
    if not isinstance(cookies, (list, tuple)):
        return None
    scoped: list[tuple[str, str, str, str]] = []
    for cookie in cookies:
        name = getattr(cookie, "name", None)
        value = getattr(cookie, "value", None)
        domain = getattr(cookie, "domain", None)
        path = getattr(cookie, "path", None)
        if (
            isinstance(name, str) and name.startswith(auth.cookie_name_prefix)
            and "|" in name and isinstance(value, str) and value
            and isinstance(domain, str)
            and cookie_domain_is_in_scope(auth, domain)
            and _cookie_matches_page(cookie, host, parsed.path or "/")
            and isinstance(path, str)
        ):
            scoped.append((name, value, domain.lower(), path))
    if not scoped:
        return None
    digest = hashlib.sha256()
    for fields in sorted(scoped):
        for field in fields:
            digest.update(field.encode("utf-8"))
            digest.update(b"\0")
    return tenant.key, digest.hexdigest()


async def install_startup_cookie_set(tab: Any, page_url: str, accounts: object) -> bool:
    """Prepare a supported scoped cookie set before the first navigation.

    This only writes browser storage. The ensuing homepage navigation is the
    ordinary one; no legacy reload or assertion of accepted login is involved.
    """
    auth = get_tenant_auth_profile(page_url)
    if auth is None or not auth.supports_cookie_login:
        return False
    if not isinstance(accounts, dict):
        raise ValueError("uTiki accounts section is unavailable")
    raw = accounts.get(auth.cookie_config_key, "")
    if not isinstance(raw, str) or not raw.strip():
        return False
    cookies = parse_ctbc_cookie_string(raw)
    if not cookies:
        raise ValueError("uTiki cookie format is invalid; re-enter the scoped cookie set")
    existing = await tab.browser.cookies.get_all()
    for cookie in existing:
        if (
            isinstance(getattr(cookie, "name", None), str)
            and cookie.name.startswith(auth.cookie_name_prefix)
            and cookie_domain_is_in_scope(auth, getattr(cookie, "domain", None))
        ):
            await tab.send(cdp.network.delete_cookies(
                name=cookie.name, domain=cookie.domain,
                path=getattr(cookie, "path", None) or "/",
            ))
    for name, value in cookies:
        result = await tab.send(cdp.network.set_cookie(
            name=name, value=value, domain=auth.cookie_set_host,
            path="/", secure=True, http_only=True,
        ))
        if result is False:
            raise ValueError("uTiki cookie storage refused the configured set")
    return True


class BrowserSessionAuthority:
    """Map this tab's cookie store and route to a revocable session identity."""

    def __init__(
        self, tab: Any, tab_key: TabKey,
        credentials: InlineLoginCredentials | None,
    ) -> None:
        if not isinstance(tab_key, TabKey):
            raise ValueError("session provider requires a bound tab")
        if credentials is not None and not isinstance(credentials, InlineLoginCredentials):
            raise ValueError("session provider requires typed credentials")
        self._tab = tab
        self._tab_key = tab_key
        self._credentials = credentials
        self._identity: tuple[str, str] | None = None
        self._epoch = 0
        self._available = False
        self._page_kind = PageKind.UNKNOWN

    async def observe(self, page_url: str) -> None:
        """Read the real browser store after navigation; a failed read grants nothing."""
        self._available = False
        actual_url = await self._tab.evaluate("location.href")
        if actual_url != page_url:
            return
        try:
            cookies = await self._tab.browser.cookies.get_all()
        except Exception:
            return
        identity = _session_fingerprint(page_url, cookies)
        if identity != self._identity:
            self._epoch += 1
            self._identity = identity
        self._page_kind = classify_utiki_page(page_url)
        self._available = True

    def current_purchase_authority(self, tab: TabKey) -> PurchaseAuthority | None:
        if (
            tab != self._tab_key or not self._available or self._identity is None
            or self._page_kind not in {
                PageKind.GENERAL_ADMISSION, PageKind.RESERVED_SEAT,
                PageKind.SERVER_ASSIGNED_QUANTITY,
            }
        ):
            return None
        revision = self._credentials.revision if self._credentials is not None else 0
        return PurchaseAuthority(AuthEpoch(self._epoch), revision)

    def current_inline_login_credentials(
        self, tab: TabKey, tenant_key: str,
    ) -> InlineLoginCredentials | None:
        if (
            self.current_purchase_authority(tab) is None
            or self._identity is None or self._identity[0] != tenant_key
        ):
            return None
        return self._credentials

    def current_verified_login(
        self, tab: TabKey, rejected_attempt_id: str,
    ) -> VerifiedLoginAcceptance | None:
        """A scoped session cookie does not attest accepted inline login."""
        return None
