"""Build one new uTiki engine from current policy and narrow capabilities."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import cast

from .application.engine import UtikiEngine
from .application.flow_settings import EffectivePolicy, FlowSettings
from .application.ports import CaptchaProviderPort, CheckoutNotificationPort, CoreControlPort, QuantityBrowserPort
from .application.refresh_contract import RefreshCoordinatorPort


def build_run_captcha_provider(flow: FlowSettings) -> CaptchaProviderPort | None:
    """Build the existing ddddocr capability once; missing models leave manual input."""
    if not isinstance(flow, FlowSettings) or not flow.ocr_enabled:
        return None
    try:
        model = import_module("ddddocr")
        if flow.ocr_use_universal and flow.ocr_path not in (
            "", "assets/model/universal",
        ):
            util = import_module("util")
            model_dir = Path(flow.ocr_path)
            if not model_dir.is_absolute():
                model_dir = Path(util.get_app_root()) / model_dir
            onnx = model_dir / "custom.onnx"
            charsets = model_dir / "charsets.json"
            if not onnx.is_file() or not charsets.is_file():
                return None
            return cast(CaptchaProviderPort, model.DdddOcr(
                det=False, ocr=False, show_ad=False,
                import_onnx_path=str(onnx), charsets_path=str(charsets),
            ))
        provider = model.DdddOcr(show_ad=False, beta=flow.ocr_beta)
        provider.set_ranges(5)
        return cast(CaptchaProviderPort, provider)
    except Exception:
        return None


def create_engine(
    policy: EffectivePolicy, browser: QuantityBrowserPort, core: CoreControlPort,
    captcha_provider: CaptchaProviderPort | None = None,
    checkout_notification: CheckoutNotificationPort | None = None,
) -> UtikiEngine:
    """Create an isolated engine; no legacy state or module registry is consulted."""
    return UtikiEngine(policy, browser, core, captcha_provider, checkout_notification=checkout_notification)


def create_run_engine(
    policy: EffectivePolicy, browser: QuantityBrowserPort, core: CoreControlPort,
    checkout_notification: CheckoutNotificationPort | None = None,
) -> UtikiEngine:
    """Start one isolated Run with its actual startup OCR capability."""
    provider = (
        build_run_captcha_provider(policy.flow)
        if isinstance(policy, EffectivePolicy) and policy.flow is not None else None
    )
    return create_engine(policy, browser, core, provider, checkout_notification)


def create_refresh_run_engine(
    policy: EffectivePolicy, browser: QuantityBrowserPort, core: CoreControlPort,
    refresh_coordinator: RefreshCoordinatorPort,
    checkout_notification: CheckoutNotificationPort | None = None,
) -> UtikiEngine:
    """Compose the isolated refresh owner with its actual coordinator capability."""
    provider = (
        build_run_captcha_provider(policy.flow)
        if isinstance(policy, EffectivePolicy) and policy.flow is not None else None
    )
    return UtikiEngine(
        policy, browser, core, provider, refresh_coordinator,
        checkout_notification,
    )
