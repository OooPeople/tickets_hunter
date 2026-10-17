"""Build one new uTiki engine from current policy and narrow capabilities."""

from __future__ import annotations

from utiki_config import CurrentPolicyState

from .application.engine import UtikiEngine
from .application.ports import CoreControlPort, QuantityBrowserPort


def create_engine(
    policy: CurrentPolicyState, browser: QuantityBrowserPort, core: CoreControlPort
) -> UtikiEngine:
    """Create an isolated engine; no legacy state or module registry is consulted."""
    return UtikiEngine(policy, browser, core)
