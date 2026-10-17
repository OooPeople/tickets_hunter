#!/usr/bin/env python3
"""Build Tickets Hunter locally and check the result is actually runnable.

PyInstaller cannot cross-compile: the frozen bundle carries this machine's
Python runtime and the compiled extensions that came with it (onnxruntime
behind ddddocr, cryptography). A Windows run produces only a Windows build and
a macOS run only a macOS build, so verifying both platforms means running this
on both.

This deliberately does less than build_and_test.bat, which installs
dependencies, packages a ZIP, runs the test suite and writes a report. The job
here is the one question that script buries: does PyInstaller produce a bundle
that starts? That is what the spec file's excludes and every native dependency
upgrade can silently break, and it is worth being able to re-run in under a
minute.

    python build_scripts/build_local.py            build, assemble, verify
    python build_scripts/build_local.py --assemble-only   assemble existing PyInstaller outputs, verify
    python build_scripts/build_local.py --skip-build      re-verify an existing dist
    python build_scripts/build_local.py --keep         leave build/ for inspection

Exit code is 0 only when every check passes.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from zipfile import BadZipFile, ZipFile

ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPTS = ROOT / "build_scripts"
DIST = ROOT / "dist"
STAGE = DIST / "tickets_hunter"

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
EXE_SUFFIX = ".exe" if IS_WINDOWS else ""

SPECS = ["nodriver_tixcraft", "settings"]

# PyInstaller can point these macOS top-level aliases at different bundled
# copies. The old merge always selected the settings copy; keep that explicit
# and leave all other, including uTiki resource, collisions as hard errors.
MACOS_ALIAS_PAIRS = {
    "libfreetype.6.dylib": (
        "PIL/.dylibs/libfreetype.6.dylib",
        "cv2/.dylibs/libfreetype.6.dylib",
    ),
    "liblzma.5.dylib": (
        "cv2/.dylibs/liblzma.5.dylib",
        "PIL/.dylibs/liblzma.5.dylib",
    ),
    "libpng16.16.dylib": (
        "PIL/.dylibs/libpng16.16.dylib",
        "cv2/.dylibs/libpng16.16.dylib",
    ),
}

# Two smoke checks run the frozen bot: --help proves the import chain loaded,
# and --self-test proves OCR works, which --help cannot because the ddddocr
# import is wrapped in try/except.
SMOKE_TIMEOUT = 120

results = []


def step(name, ok, detail=""):
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))
    return ok


def run(cmd, **kwargs):
    print(f"    $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=ROOT, **kwargs)


def same_file_bytes(first, second):
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as left, second.open("rb") as right:
        return (hashlib.file_digest(left, "sha256").digest() ==
                hashlib.file_digest(right, "sha256").digest())


def equivalent_base_library(first, second):
    """Ignore ZIP metadata only when every importable member is identical."""
    try:
        with ZipFile(first) as left, ZipFile(second) as right:
            names = set(left.namelist())
            return (names == set(right.namelist()) and
                    all(left.read(name) == right.read(name) for name in names))
    except (OSError, BadZipFile):
        return False


def expected_macos_alias(first, second, relative):
    pair = MACOS_ALIAS_PAIRS.get(relative.as_posix()) if IS_MACOS else None
    return bool(
        pair and first.is_symlink() and second.is_symlink()
        and os.readlink(first) == pair[0]
        and os.readlink(second) == pair[1]
        and first.resolve().is_file() and second.resolve().is_file()
    )


def preflight():
    print("\n[1/5] Preflight")
    step("python >= 3.10", sys.version_info >= (3, 10),
         f"running {sys.version.split()[0]}")

    proc = run([sys.executable, "-m", "PyInstaller", "--version"],
               capture_output=True, text=True)
    if proc.returncode != 0:
        return step("PyInstaller installed", False,
                    "pip install pyinstaller")
    step("PyInstaller installed", True, proc.stdout.strip())

    missing = []
    for mod in ("ddddocr", "zendriver", "tornado", "cryptography"):
        probe = run([sys.executable, "-c", f"import {mod}"],
                    capture_output=True, text=True)
        if probe.returncode != 0:
            missing.append(mod)
    return step("core imports work in this interpreter", not missing,
                f"missing: {', '.join(missing)}" if missing else
                "ddddocr, zendriver, tornado, cryptography")


def build():
    print("\n[2/5] PyInstaller")
    # A stale dist/ is the classic way to "verify" a build that never ran, so
    # it goes before anything else rather than after a failure.
    for path in (DIST, ROOT / "build"):
        if path.exists():
            shutil.rmtree(path)

    for name in SPECS:
        started = time.time()
        proc = run([sys.executable, "-m", "PyInstaller",
                    str(BUILD_SCRIPTS / f"{name}.spec"),
                    "--clean", "--noconfirm"])
        if not step(f"build {name}", proc.returncode == 0,
                    f"{time.time() - started:.0f}s"):
            return False
    return True


def assemble():
    """Merge the two PyInstaller outputs the way the release workflow does."""
    print("\n[3/5] Assemble dist/tickets_hunter")
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)

    for name in SPECS:
        src = DIST / name / f"{name}{EXE_SUFFIX}"
        if not src.exists():
            return step(f"{name} executable produced", False, str(src))
        shutil.copy2(src, STAGE / src.name)
        if not IS_WINDOWS:
            os.chmod(STAGE / src.name, 0o755)

        internal = DIST / name / "_internal"
        if internal.exists():
            target = STAGE / "_internal"
            for item in internal.rglob("*"):
                destination = target / item.relative_to(internal)
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif item.is_file():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists():
                        relative = item.relative_to(internal)
                        if not destination.is_file():
                            return step("_internal content conflict", False,
                                        str(relative))
                        if same_file_bytes(item, destination):
                            continue
                        first = DIST / SPECS[0] / "_internal" / relative
                        if relative.as_posix() == "base_library.zip" and \
                                equivalent_base_library(first, item):
                            step("equivalent base_library.zip", True,
                                 "all members identical")
                            continue
                        if expected_macos_alias(first, item, relative):
                            shutil.copy2(item, destination)
                            step("canonical macOS library alias", True,
                                 str(relative))
                            continue
                        return step("_internal content conflict", False,
                                    str(relative))
                    else:
                        shutil.copy2(item, destination)
                else:
                    return step("_internal unsupported entry", False,
                                str(item.relative_to(internal)))
    step("executables and _internal merged", True)

    # assets/ and www/ are copied from src/ rather than bundled, matching the
    # release workflow -- the settings web UI reads them from disk at runtime.
    for folder in ("assets", "www"):
        shutil.copytree(ROOT / "src" / folder, STAGE / folder,
                        dirs_exist_ok=True)
    step("assets and www copied", True)

    if IS_MACOS:
        launchers = BUILD_SCRIPTS / "macos"
        if launchers.exists():
            # build.command lives beside them but is a developer entry point,
            # not something to ship inside the release bundle.
            shipped = [item for item in launchers.glob("*.command")
                       if item.name != "build.command"]
            for item in shipped:
                shutil.copy2(item, STAGE / item.name)
                os.chmod(STAGE / item.name, 0o755)
            step("macOS launchers copied", True,
                 ", ".join(i.name for i in shipped))
        else:
            step("macOS launchers copied", True,
                 "skipped: build_scripts/macos/ not in this tree")
    return True


def verify_layout():
    print("\n[4/5] Verify layout")
    ok = True
    for name in SPECS:
        exe = STAGE / f"{name}{EXE_SUFFIX}"
        ok &= step(f"{exe.name} exists", exe.exists())
        if exe.exists() and not IS_WINDOWS:
            ok &= step(f"{exe.name} is executable",
                       os.access(exe, os.X_OK))

    ok &= step("_internal present", (STAGE / "_internal").is_dir())
    ok &= step("www/settings.html present",
               (STAGE / "www" / "settings.html").is_file())

    source_scripts = ROOT / "src" / "platforms" / "utiki" / "scripts"
    bundled_scripts = STAGE / "_internal" / "platforms" / "utiki" / "scripts"
    source_files = {
        item.relative_to(source_scripts): item
        for item in source_scripts.rglob("*") if item.is_file()
    }
    bundled_files = {
        item.relative_to(bundled_scripts): item
        for item in bundled_scripts.rglob("*") if item.is_file()
    } if bundled_scripts.is_dir() else {}
    scripts_match = bool(source_files) and source_files.keys() == bundled_files.keys()
    if scripts_match:
        scripts_match = all(same_file_bytes(source, bundled_files[relative])
                            for relative, source in source_files.items())
    ok &= step("uTiki scripts match source in final bundle", scripts_match)

    # ddddocr ships its ONNX model as a data file. PyInstaller collects it via
    # collect_data_files(), and a silent miss turns into "OCR always fails" at
    # run time rather than a build error, so it is checked explicitly.
    onnx = list((STAGE / "_internal").rglob("*.onnx"))
    ok &= step("ddddocr model bundled", bool(onnx),
               f"{len(onnx)} .onnx file(s)" if onnx else
               "no .onnx under _internal")

    # The spec files exclude these to keep the bundle small; confirm it held.
    for unwanted in ("torch", "matplotlib", "IPython"):
        hits = list((STAGE / "_internal").glob(unwanted))
        ok &= step(f"{unwanted} excluded", not hits)

    size_mb = sum(f.stat().st_size for f in STAGE.rglob("*") if f.is_file())
    size_mb /= 1024 * 1024
    step("bundle size", True, f"{size_mb:.0f} MB")
    return ok


def run_bot(exe, flag, *, cwd=None):
    """Run the frozen bot with one flag; (returncode, output) or (None, "")."""
    try:
        proc = subprocess.run([str(exe), flag], cwd=cwd or STAGE,
                              capture_output=True, text=True,
                              timeout=SMOKE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def print_tail(output):
    print("\n".join(output.strip().splitlines()[-12:]))


def smoke():
    print("\n[5/5] Smoke test")
    exe = STAGE / f"nodriver_tixcraft{EXE_SUFFIX}"
    if not exe.exists():
        return step("bot binary starts", False, "not built")

    code, output = run_bot(exe, "--help")
    if code is None:
        return step("bot binary starts", False,
                    f"no output within {SMOKE_TIMEOUT}s")
    if code != 0:
        print_tail(output)
        return step("bot binary starts", False, f"exit {code}")
    if "--homepage" not in output:
        print_tail(output)
        return step("bot binary starts", False,
                    "exit 0 but usage text missing")
    step("bot binary starts", True, "import chain loaded, argparse reached")

    code, output = run_bot(exe, "--self-test")
    if code is None:
        return step("OCR self-test", False,
                    f"no output within {SMOKE_TIMEOUT}s")
    if code != 0:
        print_tail(output)
        return step("OCR self-test", False, f"exit {code}")
    step("OCR self-test", True, "both models read the test image")

    with tempfile.TemporaryDirectory(prefix="tickets-hunter-resource-smoke-") as cwd:
        code, output = run_bot(exe, "--utiki-resource-self-test", cwd=cwd)
        if code != 0 or "uTiki resource self-test passed:" not in output:
            print_tail(output)
            return step("bot frozen uTiki resource self-test", False,
                        f"exit {code}")
        step("bot frozen uTiki resource self-test", True)
        settings_exe = STAGE / f"settings{EXE_SUFFIX}"
        try:
            proc = subprocess.run(
                [str(settings_exe), "--resource-self-test"], cwd=cwd,
                capture_output=True, text=True, timeout=SMOKE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return step("settings frozen resource self-test", False,
                        f"no output within {SMOKE_TIMEOUT}s")
        if proc.returncode != 0 or "Settings resource self-test passed:" not in proc.stdout:
            print_tail((proc.stdout or "") + (proc.stderr or ""))
            return step("settings frozen resource self-test", False,
                        f"exit {proc.returncode}")
        step("settings frozen resource self-test", True)

        script_file = (STAGE / "_internal" / "platforms" / "utiki" /
                       "scripts" / "date_snapshot.js")
        original = script_file.read_bytes()
        backup = script_file.with_name("date_snapshot.js.resource-smoke-backup")
        if backup.exists():
            return step("resource smoke backup absent", False)
        try:
            script_file.rename(backup)
            code, output = run_bot(exe, "--utiki-resource-self-test", cwd=cwd)
            if code in (None, 0) or "uTiki resource self-test FAILED:" not in output:
                print_tail(output)
                return step("missing frozen uTiki script rejected", False)
        finally:
            backup.rename(script_file)
        step("missing frozen uTiki script rejected", True)

        try:
            script_file.write_bytes(original + b" ")
            code, output = run_bot(exe, "--utiki-resource-self-test", cwd=cwd)
            if code in (None, 0) or "digest mismatch" not in output:
                print_tail(output)
                return step("mixed frozen uTiki script rejected", False)
        finally:
            script_file.write_bytes(original)
        return step("mixed frozen uTiki script rejected", True)


def clear_quarantine():
    """Drop the quarantine flag macOS puts on a freshly written unsigned binary.

    Without it the first launch is refused. Whoever just built the bundle
    already trusts it; shipping it to others is for signing and notarisation.
    """
    proc = subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(STAGE)],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        print("Gatekeeper quarantine flag cleared.")
    else:
        print("Could not clear the quarantine flag, so the first launch will "
              "be blocked. Right-click a .command file and choose Open, or run:")
        print(f"  xattr -dr com.apple.quarantine {STAGE}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--assemble-only", action="store_true",
                      help="assemble existing PyInstaller outputs and verify the bundle")
    mode.add_argument("--skip-build", action="store_true",
                      help="re-verify an already assembled dist/ without rebuilding")
    parser.add_argument("--keep", action="store_true",
                        help="keep build/ for inspecting warn-*.txt")
    args = parser.parse_args()

    print(f"Tickets Hunter local build -- {sys.platform}")
    print(f"Project root: {ROOT}")

    if not preflight():
        print("\nPreflight failed, nothing was built.")
        return 1

    if not args.skip_build:
        if not args.assemble_only and not build():
            print("\nPyInstaller failed. See build/*/warn-*.txt for the "
                  "module it could not resolve.")
            return 1
        if not assemble():
            return 1
    elif not STAGE.exists():
        print(f"\n--skip-build needs an existing {STAGE}")
        return 1

    layout_ok = verify_layout()
    smoke_ok = smoke()

    if not args.keep and (ROOT / "build").exists():
        shutil.rmtree(ROOT / "build")

    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED ({len(failed)}): " + ", ".join(failed))
        return 1
    print(f"All {len(results)} checks passed")
    print(f"Bundle: {STAGE}")
    if IS_MACOS:
        clear_quarantine()
    return 0 if layout_ok and smoke_ok else 1


if __name__ == "__main__":
    sys.exit(main())
