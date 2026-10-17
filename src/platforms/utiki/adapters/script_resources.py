"""Load versioned browser scripts from the installed uTiki package."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Mapping, NamedTuple, cast


RESOURCE_DIR = Path(__file__).resolve().parent.parent / "scripts"
NATIVE_SETTER_PLACEHOLDER = "__UTIKI_NATIVE_INPUT_SETTER_FRAGMENT__"


class ScriptResourceError(RuntimeError):
    """A required browser script is missing or does not match this build."""


class _Manifest(NamedTuple):
    scripts: dict[str, str]
    native_setter_sha256: str


@lru_cache(maxsize=1)
def _manifest() -> _Manifest:
    manifest_path = RESOURCE_DIR / "manifest.json"
    if RESOURCE_DIR.is_symlink() or manifest_path.is_symlink():
        raise ScriptResourceError("uTiki script manifest path is invalid")
    try:
        raw = manifest_path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ScriptResourceError("uTiki script manifest is unavailable") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or set(document) != {
            "schema_version", "scripts", "native_setter_sha256"
        }
        or not isinstance(document.get("scripts"), dict)
        or not isinstance(document.get("native_setter_sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", document["native_setter_sha256"]) is None
    ):
        raise ScriptResourceError("uTiki script manifest is invalid")
    scripts = document["scripts"]
    if any(
        not isinstance(name, str)
        or re.fullmatch(r"[a-z][a-z0-9_]*", name) is None
        or not isinstance(digest, str)
        or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        for name, digest in scripts.items()
    ):
        raise ScriptResourceError("uTiki script manifest has invalid entries")
    return _Manifest(
        cast(dict[str, str], scripts), document["native_setter_sha256"]
    )


@lru_cache(maxsize=1)
def verified_native_setter_fragment(value: str) -> str:
    """Verify a composition-root supplied native setter before script binding."""
    if (
        not isinstance(value, str)
        or hashlib.sha256(value.encode("utf-8")).hexdigest()
        != _manifest().native_setter_sha256
    ):
        raise ScriptResourceError("uTiki native input setter digest mismatch")
    return value


@lru_cache(maxsize=32)
def load_script(name: str) -> str:
    """Return one verified source script; never fall back to the source cwd."""
    if not isinstance(name, str) or name not in _manifest().scripts:
        raise ScriptResourceError("uTiki script name is not in the manifest")
    script_path = RESOURCE_DIR / f"{name}.js"
    if script_path.is_symlink():
        raise ScriptResourceError(f"uTiki script path is invalid: {name}")
    try:
        raw = script_path.read_bytes()
    except OSError as exc:
        raise ScriptResourceError(f"uTiki script is unavailable: {name}") from exc
    if hashlib.sha256(raw).hexdigest() != _manifest().scripts[name]:
        raise ScriptResourceError(f"uTiki script digest mismatch: {name}")
    try:
        script = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ScriptResourceError(f"uTiki script is not UTF-8: {name}") from exc
    if not script.endswith("\n"):
        raise ScriptResourceError(f"uTiki script has no final newline: {name}")
    return script[:-1]


def assemble_script(
    name: str,
    replacements: Mapping[str, str] | None = None,
    *,
    native_setter_fragment: str | None = None,
) -> str:
    """Bind explicitly supplied placeholders without changing script ordering."""
    script = load_script(name)
    values = dict(replacements or {})
    if NATIVE_SETTER_PLACEHOLDER in script:
        if NATIVE_SETTER_PLACEHOLDER in values:
            raise ScriptResourceError("uTiki native input setter cannot be overridden")
        if native_setter_fragment is None:
            raise ScriptResourceError("uTiki native input setter was not supplied")
        values[NATIVE_SETTER_PLACEHOLDER] = verified_native_setter_fragment(
            native_setter_fragment
        )
    if not values:
        return script
    for placeholder, value in values.items():
        if script.count(placeholder) != 1 or not isinstance(value, str):
            raise ScriptResourceError(f"uTiki script placeholder mismatch: {name}")
    pattern = re.compile("|".join(re.escape(key) for key in values))
    return pattern.sub(lambda match: values[match.group(0)], script)


def verify_script_resources(native_setter_fragment: str) -> str:
    """Verify every installed script and return this package manifest digest."""
    _manifest.cache_clear()
    load_script.cache_clear()
    verified_native_setter_fragment.cache_clear()
    scripts = _manifest().scripts
    if not scripts:
        raise ScriptResourceError("uTiki script manifest is empty")
    for name in scripts:
        load_script(name)
    verified_native_setter_fragment(native_setter_fragment)
    return hashlib.sha256((RESOURCE_DIR / "manifest.json").read_bytes()).hexdigest()
