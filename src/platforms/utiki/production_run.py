"""Formal uTiki caller: one tab, one engine, and one mutation owner per Run."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any

from zendriver import cdp
from nodriver_common import _find_cf_iframe_in_dom

from .adapters.browser import ZendriverSelectionBrowser
from .adapters.notification import CheckoutNotificationAdapter
from .adapters.refresh import SqliteRefreshAdapter
from .adapters.session import BrowserSessionAuthority
from .application.engine import SelectionProgress, UtikiEngine
from .application.flow_settings import EffectivePolicy, apply_effective_policy_reload
from .application.ports import CoreControl, CoreGate, InlineLoginCredentials
from .application.refresh_contract import RefreshProgress, RefreshReason
from .application.refresh_host import IsolatedRefreshHost
from .bootstrap import create_refresh_run_engine
from .domain.identity import TabKey
from .profiles import PageKind, classify_utiki_page


@dataclass(frozen=True)
class RunResult:
    progress: str
    manual_reason: str = ""


def _schedule_target(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        if "/" in value:
            return datetime.strptime(value.strip(), "%Y/%m/%d %H:%M:%S")
        clock = datetime.strptime(value.strip(), "%H:%M:%S").time()
        return datetime.combine(datetime.now().date(), clock)
    except ValueError as exc:
        raise ValueError("uTiki refresh_datetime must be YYYY/MM/DD HH:MM:SS or HH:MM:SS") from exc


class _RunHost:
    def __init__(self, tab: Any, key: TabKey, credentials: InlineLoginCredentials | None):
        self.tab = tab
        self.key = key
        self.session = BrowserSessionAuthority(tab, key, credentials)
        self._control = CoreControl(1, CoreGate.PASSIVE)
        self._clearance: tuple[str, str] | None = None
        self._document = ""
        self.health_reason = "unverified"

    def set_gate(self, gate: CoreGate) -> None:
        if self._control.gate is not gate:
            self._control = CoreControl(self._control.revision + 1, gate)

    async def observe(self, url: str) -> None:
        await self.session.observe(url)
        document = await self.tab.evaluate("String(performance.timeOrigin)")
        if not isinstance(document, str) or not document or document == "0":
            raise ValueError("uTiki document identity unavailable")
        if document != self._document:
            self._document = document
            self._clearance = None
            self.health_reason = "new-document"

    def current_control(self, tab: TabKey) -> CoreControl:
        if tab != self.key:
            raise ValueError("uTiki control belongs to another tab")
        return self._control

    def current_purchase_authority(self, tab: TabKey):
        return self.session.current_purchase_authority(tab)

    def current_inline_login_credentials(self, tab: TabKey, tenant_key: str):
        return self.session.current_inline_login_credentials(tab, tenant_key)

    def current_verified_login(self, tab: TabKey, rejected_attempt_id: str):
        return self.session.current_verified_login(tab, rejected_attempt_id)

    def current_health_clearance(self, tab: TabKey):
        if tab != self.key:
            return None
        return self._clearance

    async def verify_health_without_mutation(self, document) -> bool:
        if (
            document.tab != self.key or document.classification != "normal"
            or document.document_id != self._document
        ):
            self._clearance = None
            self.health_reason = document.classification
            return False
        # One read-only DOM query. Any uncertain observation withholds clearance;
        # challenge solving is a user action, never an outer reload/click.
        try:
            proof = await self.tab.evaluate("""(() => ({
            url: location.href, document: String(performance.timeOrigin),
            ready: document.readyState === 'complete' || document.readyState === 'interactive',
            challenge: !!(document.querySelector('iframe[src*="challenges.cloudflare.com"]') ||
                document.querySelector('.cf-turnstile') ||
                document.querySelector('#cf-challenge-running') ||
                document.querySelector('#cf-browser-verification')),
            interstitial: /checking your browser|please wait while we verify|verify you are human/i
                .test((document.body && document.body.innerText || '').slice(0, 4000))
        }))()""")
            pierced = await self.tab.send(cdp.dom.get_document(depth=-1, pierce=True))
            challenge_iframe, _source = _find_cf_iframe_in_dom(pierced)
        except Exception:
            self._clearance = None
            self.health_reason = "observation-unavailable"
            return False
        healthy = bool(
            isinstance(proof, dict) and proof.get("url") == document.url
            and proof.get("document") == document.document_id
            and proof.get("ready") is True and proof.get("challenge") is False
            and proof.get("interstitial") is False and not challenge_iframe
        )
        self._clearance = (document.url, document.document_id) if healthy else None
        self.health_reason = "clear" if healthy else "challenge-unverified"
        return healthy


class UtikiProductionRun:
    """A persistent formal host; tick and reload never replace its engine."""

    def __init__(
        self, tab: Any, profile: dict[str, object], raw_profile: str,
        refresh_adapter: SqliteRefreshAdapter | None = None,
    ):
        raw_key = getattr(getattr(tab, "target", None), "target_id", None)
        key_value = str(raw_key) if raw_key is not None else ""
        if not key_value:
            raise ValueError("uTiki tab has no target identity")
        self.tab = tab
        self.key = TabKey(1, key_value)
        self.policy = apply_effective_policy_reload(None, raw_profile)
        if not self.policy.allows_new_mutation or self.policy.flow is None:
            raise ValueError(
                f"uTiki profile is not current ({self.policy.block_reason}); "
                "reopen Settings, fill the uTiki behavior fields, and save again"
            )
        _schedule_target(self.policy.flow.refresh_datetime)
        accounts = profile.get("accounts")
        if not isinstance(accounts, dict):
            raise ValueError("uTiki accounts section is required")
        account, password = accounts.get("ctbc_account"), accounts.get("ctbc_password")
        credentials = (
            InlineLoginCredentials(account, password, 1)
            if isinstance(account, str) and account.strip()
            and isinstance(password, str) and password else None
        )
        self.host = _RunHost(tab, self.key, credentials)
        self.browser = ZendriverSelectionBrowser(tab, self.key)
        self.engine: UtikiEngine = create_refresh_run_engine(
            self.policy, self.browser, self.host,
            refresh_adapter if refresh_adapter is not None else SqliteRefreshAdapter(),
            CheckoutNotificationAdapter(profile),
        )
        self.handle = self.engine.attach(self.key)
        self.refresh_host = IsolatedRefreshHost(self.engine, self.host)
        self._closed = False
        self.close_status = ""
        self._paused = False
        self._schedule_target = _schedule_target(self.policy.flow.refresh_datetime)

    @property
    def closed(self) -> bool:
        return self._closed

    def update_policy(self, raw_profile: str) -> bool:
        updated = apply_effective_policy_reload(self.policy, raw_profile)
        if updated.flow is not None and updated.allows_new_mutation:
            try:
                target = _schedule_target(updated.flow.refresh_datetime)
            except ValueError:
                updated = EffectivePolicy(
                    self.policy.last_valid, self.policy.flow,
                    self.policy.policy_revision,
                    self.policy.control_revision + int(self.policy.allows_new_mutation),
                    "INVALID_REFRESH_DATETIME",
                )
                target = self._schedule_target
        else:
            target = self._schedule_target
        if not self.engine.update_policy(updated):
            return False
        self.policy = updated
        self._schedule_target = target
        return updated.allows_new_mutation

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.host.set_gate(CoreGate.PAUSED if paused else CoreGate.PASSIVE)
        self.engine.update_control(self.handle)

    async def tick(self, url: str, *, paused: bool = False) -> RunResult:
        if self._closed:
            return RunResult("closed")
        if str(getattr(getattr(self.tab, "target", None), "target_id", "")) != self.key.target_id:
            await self.close()
            return RunResult("tab-changed", "restart Run after original tab is reconciled")
        kind = classify_utiki_page(url)
        try:
            await self.host.observe(url)
        except Exception:
            self.host.set_gate(CoreGate.PAUSED)
            self.engine.update_control(self.handle)
            return RunResult("observation-unavailable", "verify the active uTiki tab manually")
        if paused:
            self.set_paused(True)
            await self.engine.advance_submit(self.handle)
            return RunResult("paused")
        terminal, _rejection = self.engine.submission_state(self.handle)
        if terminal in {
            SelectionProgress.SUBMIT_CONFIRMED, SelectionProgress.QUOTA_BLOCKED,
        }:
            return RunResult(terminal.value)
        if kind is PageKind.CHECKOUT:
            # A late attributable checkout closes its original attempt before
            # a scheduled refresh can hold the new document in a wait state.
            result = await self.engine.advance_submit(self.handle)
            if result in {
                SelectionProgress.SUBMIT_CONFIRMED, SelectionProgress.QUOTA_BLOCKED,
            }:
                return RunResult(result.value)
            return RunResult(
                "checkout-needs-attribution",
                "inspect checkout manually; no current claim proves which submission reached it",
            )
        target = self._schedule_target
        due = bool(target is not None and datetime.now() >= target)
        waiting = bool(target is not None and not due)
        self.host.set_gate(CoreGate.HEALTH_ONLY if waiting else CoreGate.PURCHASE_ALLOWED)
        self.engine.update_control(self.handle)
        refresh = await self.refresh_host.tick(
            self.handle, schedule_due=due,
            apply_local_interval=False,
        )
        if self.host.health_reason == "challenge-unverified":
            await self.engine.advance_submit(self.handle)
            return RunResult(
                "challenge-unverified",
                "complete the page challenge manually; this Run will recheck the document",
            )
        if self.host.health_reason in {"unknown", "unverified", "observation-unavailable"}:
            await self.engine.advance_submit(self.handle)
            return RunResult("health-unverified", "verify the current document manually")
        if self.host.health_reason == "blocked":
            await self.engine.advance_submit(self.handle)
            return RunResult(f"rate-limited:{refresh.value}")
        if waiting:
            await self.engine.advance_submit(self.handle)
            return RunResult("waiting-for-schedule")
        if due and refresh is not RefreshProgress.COMPLETE:
            await self.engine.advance_submit(self.handle)
            return RunResult(f"scheduled-refresh:{refresh.value}")
        if kind is PageKind.UNKNOWN:
            await self.engine.advance_submit(self.handle)
            return RunResult("unknown-page", "verify the page and source manually")
        if kind is PageKind.CHECKOUT:
            result = await self.engine.advance_submit(self.handle)
            return RunResult(result.value)
        if kind is PageKind.DATE:
            progress = await self.engine.advance_date(self.handle)
            if progress is SelectionProgress.BLOCKED and self.policy.flow.date_enabled:
                refresh = await self.refresh_host.tick(
                    self.handle, schedule_due=False, reason=RefreshReason.HEALTH,
                )
                return RunResult(f"no-date-refresh:{refresh.value}")
            return RunResult(progress.value)
        if kind is PageKind.AREA:
            progress = await self.engine.advance_area(self.handle)
            if progress is SelectionProgress.BLOCKED and self.policy.flow.area_enabled:
                refresh = await self.refresh_host.tick(
                    self.handle, schedule_due=False, reason=RefreshReason.HEALTH,
                )
                return RunResult(f"no-area-refresh:{refresh.value}")
            return RunResult(progress.value)
        terminal, rejection = self.engine.submission_state(self.handle)
        if terminal is SelectionProgress.SUBMIT_CONFIRMED or terminal is SelectionProgress.QUOTA_BLOCKED:
            return RunResult(terminal.value)
        if terminal is SelectionProgress.SUBMIT_BLOCKED:
            if rejection == "auth":
                progress = await self.engine.advance_login_recovery(self.handle)
                reason = (
                    "accepted manual login cannot be verified by the current provider; "
                    "edit the rejected input or inspect the result manually"
                    if progress is SelectionProgress.MANUAL_ACTION_REQUIRED else ""
                )
                return RunResult(progress.value, reason)
            if rejection == "captcha":
                dismissed = await self.engine.advance_dialog_dismissal(self.handle)
                if dismissed is SelectionProgress.DIALOG_DISMISSED:
                    reset = await self.engine.advance_captcha_reset(self.handle)
                    if reset is SelectionProgress.CAPTCHA_RESET_VERIFIED:
                        return RunResult((await self.engine.advance_submit(self.handle)).value)
                    return RunResult(reset.value)
                return RunResult(dismissed.value)
            if rejection == "seat_conflict":
                return RunResult((await self.engine.advance_conflict_recovery(self.handle)).value)
            return RunResult("submit-blocked", "inspect the unclassified result manually")
        if terminal is not None:
            return RunResult(terminal.value)
        if self.host.current_purchase_authority(self.key) is None:
            reconciled = await self.engine.advance_submit(self.handle)
            if reconciled in {
                SelectionProgress.SUBMIT_CONFIRMED, SelectionProgress.QUOTA_BLOCKED,
            }:
                return RunResult(reconciled.value)
            return RunResult(
                "manual-action-required",
                "scoped purchase session is unavailable for this tenant or tab; "
                "sign in manually or review the configured cookie set",
            )
        # Reconcile any in-flight request before planning another mutation.
        result = await self.engine.advance_submit(self.handle)
        terminal, _ = self.engine.submission_state(self.handle)
        if terminal is not None or result is SelectionProgress.SUBMIT_DISPATCHED:
            return RunResult(result.value)
        selection = await (
            self.engine.advance_seat(self.handle)
            if kind is PageKind.RESERVED_SEAT else self.engine.advance_quantity(self.handle)
        )
        if selection not in {SelectionProgress.QUANTITY_READY, SelectionProgress.SEAT_READY}:
            return RunResult(selection.value)
        login = await self.engine.advance_login(self.handle)
        if login is not SelectionProgress.LOGIN_READY:
            return RunResult(login.value, "check login fields manually" if login is SelectionProgress.BLOCKED else "")
        input_result = await self.engine.advance_input(self.handle)
        if input_result is not SelectionProgress.INPUT_READY:
            return RunResult(input_result.value, "enter CAPTCHA manually" if input_result is SelectionProgress.MANUAL_ACTION_REQUIRED else "")
        return RunResult((await self.engine.advance_submit(self.handle)).value)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.host.set_gate(CoreGate.CLOSING)
        self.engine.update_control(self.handle)
        intent = self.engine.refresh_intent(self.handle)
        if not self.engine.close(self.handle):
            self.close_status = "owner-close-unconfirmed"
        try:
            await self.engine.reconcile_closed(self.handle)
            refresh = await self.engine.reconcile_closed_refresh(self.handle)
            while (
                refresh is RefreshProgress.CLEANUP_PENDING and intent is not None
                and intent.cleanup_attempts < 3
            ):
                await asyncio.sleep(max(0.0, intent.next_retry_at - time.monotonic()))
                refresh = await self.engine.reconcile_closed_refresh(self.handle)
            if refresh in {
                RefreshProgress.CLEANUP_PENDING,
                RefreshProgress.RELOAD_POSSIBLE,
                RefreshProgress.AWAITING_CLEARANCE,
            }:
                self.close_status = refresh.value
        except Exception:
            self.close_status = "reconciliation-unavailable"
        if self.close_status:
            print(f"[uTiki] Original Run close needs manual reconciliation: {self.close_status}")
