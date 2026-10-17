"""Load versioned browser scripts from the installed uTiki package."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Mapping, cast


RESOURCE_DIR = Path(__file__).resolve().parent.parent / "scripts"


class ScriptResourceError(RuntimeError):
    """A required browser script is missing or does not match this build."""


@lru_cache(maxsize=1)
def _manifest() -> dict[str, str]:
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
        or set(document) != {"schema_version", "scripts"}
        or not isinstance(document.get("scripts"), dict)
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
    return cast(dict[str, str], scripts)


@lru_cache(maxsize=32)
def load_script(name: str) -> str:
    """Return one verified source script; never fall back to the source cwd."""
    if not isinstance(name, str) or name not in _manifest():
        raise ScriptResourceError("uTiki script name is not in the manifest")
    script_path = RESOURCE_DIR / f"{name}.js"
    if script_path.is_symlink():
        raise ScriptResourceError(f"uTiki script path is invalid: {name}")
    try:
        raw = script_path.read_bytes()
    except OSError as exc:
        raise ScriptResourceError(f"uTiki script is unavailable: {name}") from exc
    if hashlib.sha256(raw).hexdigest() != _manifest()[name]:
        raise ScriptResourceError(f"uTiki script digest mismatch: {name}")
    try:
        script = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ScriptResourceError(f"uTiki script is not UTF-8: {name}") from exc
    if not script.endswith("\n"):
        raise ScriptResourceError(f"uTiki script has no final newline: {name}")
    return script[:-1]


def assemble_script(name: str, replacements: Mapping[str, str] | None = None) -> str:
    """Bind explicitly supplied placeholders without changing script ordering."""
    script = load_script(name)
    values = replacements or {}
    if not values:
        return script
    for placeholder, value in values.items():
        if script.count(placeholder) != 1 or not isinstance(value, str):
            raise ScriptResourceError(f"uTiki script placeholder mismatch: {name}")
    pattern = re.compile("|".join(re.escape(key) for key in values))
    return pattern.sub(lambda match: values[match.group(0)], script)


def verify_script_resources() -> str:
    """Verify every installed script and return this package manifest digest."""
    _manifest.cache_clear()
    load_script.cache_clear()
    scripts = _manifest()
    if not scripts:
        raise ScriptResourceError("uTiki script manifest is empty")
    for name in scripts:
        load_script(name)
    return hashlib.sha256((RESOURCE_DIR / "manifest.json").read_bytes()).hexdigest()
