"""One uTiki planning owner for the current core control interface."""

from __future__ import annotations

from dataclasses import dataclass

from utiki_config import CurrentPolicyState

from ..domain.identity import OperationStamp, TabKey
from ..domain.plans import QuantityPlan
from ..selection import select_quantity_snapshot
from .ports import CoreControl, CoreControlPort, CoreGate, QuantityBrowserPort, QuantityObservation


@dataclass(frozen=True, slots=True)
class SessionHandle:
    tab: TabKey
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.tab, TabKey) or type(self.generation) is not int or self.generation <= 0:
            raise ValueError("session handle requires tab and generation")


@dataclass(frozen=True, slots=True)
class PlanningContext:
    session_generation: int
    policy_revision: int
    control_revision: int


@dataclass(slots=True)
class _Session:
    handle: SessionHandle
    control: CoreControl
    control_revision: int
    closing: bool = False


class UtikiEngine:
    """Own attached tabs; only return read-only plans while all gates agree."""

    def __init__(
        self,
        policy: CurrentPolicyState,
        browser: QuantityBrowserPort,
        core: CoreControlPort,
    ) -> None:
        if not isinstance(policy, CurrentPolicyState):
            raise ValueError("engine requires validated current policy state")
        self._policy = policy
        self._browser = browser
        self._core = core
        self._sessions: dict[TabKey, _Session] = {}
        self._next_generation = 0
        self._policy_conflict = False

    def _host_control(self, tab: TabKey) -> CoreControl:
        value = self._core.current_control(tab)
        if not isinstance(value, CoreControl):
            raise ValueError("core control source returned an invalid gate")
        return value

    def attach(self, tab: TabKey) -> SessionHandle:
        if not isinstance(tab, TabKey):
            raise ValueError("attach requires a typed tab")
        control = self._host_control(tab)
        if control.gate is CoreGate.CLOSING:
            raise ValueError("closing control cannot attach a session")
        existing = self._sessions.get(tab)
        if existing is not None:
            if existing.closing or existing.control != control:
                raise ValueError("reattach cannot replace current control")
            return existing.handle
        self._next_generation += 1
        handle = SessionHandle(tab, self._next_generation)
        self._sessions[tab] = _Session(handle, control, 1)
        return handle

    def _current(self, handle: SessionHandle) -> _Session | None:
        if not isinstance(handle, SessionHandle):
            return None
        current = self._sessions.get(handle.tab)
        return current if current is not None and current.handle == handle else None

    def update_control(self, handle: SessionHandle) -> bool:
        current = self._current(handle)
        if current is None:
            return False
        control = self._host_control(handle.tab)
        if control.revision < current.control.revision or (
            control.revision == current.control.revision and control != current.control
        ):
            if not current.closing and current.control.gate is not CoreGate.PAUSED:
                current.control = CoreControl(current.control.revision, CoreGate.PAUSED)
                current.control_revision += 1
            return False
        if control == current.control:
            return True
        current.control = control
        current.control_revision += 1
        current.closing = current.closing or control.gate is CoreGate.CLOSING
        return True

    def update_policy(self, policy: CurrentPolicyState) -> bool:
        if not isinstance(policy, CurrentPolicyState):
            self._policy_conflict = True
            return False
        if (
            policy.policy_revision < self._policy.policy_revision
            or policy.control_revision < self._policy.control_revision
            or (
                policy.last_valid != self._policy.last_valid
                and policy.policy_revision <= self._policy.policy_revision
            )
            or (
                policy != self._policy
                and policy.policy_revision == self._policy.policy_revision
                and policy.control_revision == self._policy.control_revision
            )
            or (
                self._policy_conflict
                and policy.policy_revision == self._policy.policy_revision
                and policy.control_revision == self._policy.control_revision
            )
        ):
            self._policy_conflict = True
            return False
        if policy != self._policy or self._policy_conflict:
            self._policy = policy
            self._policy_conflict = False
            for current in self._sessions.values():
                current.control_revision += 1
        return True

    def close(self, handle: SessionHandle) -> bool:
        current = self._current(handle)
        if current is None:
            return False
        if not current.closing:
            current.closing = True
            current.control_revision += 1
        control = self._host_control(handle.tab)
        if (
            control.gate is not CoreGate.CLOSING
            or control.revision < current.control.revision
            or (
                control.revision == current.control.revision
                and control != current.control
            )
        ):
            return False
        self._sessions.pop(handle.tab, None)
        return True

    def context(self, handle: SessionHandle) -> PlanningContext | None:
        current = self._current(handle)
        if (
            current is None or current.closing or self._policy_conflict
            or not self._policy.allows_new_mutation
            or current.control.gate is not CoreGate.PURCHASE_ALLOWED
            or self._host_control(handle.tab) != current.control
        ):
            return None
        return PlanningContext(
            handle.generation, self._policy.policy_revision, current.control_revision
        )

    async def plan_quantity(
        self, handle: SessionHandle, stamp: OperationStamp, ticket_number: int
    ) -> QuantityPlan | None:
        """Observe once; discard a plan if its source or current authority changes."""
        before = self.context(handle)
        if (
            before is None or not isinstance(stamp, OperationStamp)
            or stamp.tab != handle.tab
            or (
                stamp.session_generation, stamp.policy_revision, stamp.control_revision
            ) != (
                before.session_generation, before.policy_revision, before.control_revision
            )
        ):
            return None
        observation = await self._browser.observe_page(stamp)
        if (
            self.context(handle) != before
            or not isinstance(observation, QuantityObservation)
            or observation.source != stamp
            or observation.snapshot.document_id != stamp.document.token
        ):
            return None
        policy = self._policy.last_valid
        if policy is None:
            return None
        return select_quantity_snapshot(
            observation.snapshot, policy.ticket_type_keyword, ticket_number
        )
