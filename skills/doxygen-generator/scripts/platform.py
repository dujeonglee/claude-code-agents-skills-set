#!/usr/bin/env python3
"""Platform detection and binary path resolution for the doxygen-generator skill.

Consistent directory layout across all platforms:
    bin/{platform}/doxygen-{ver}/bin/doxygen[.exe]
    bin/{platform}/graphviz-{ver}/bin/dot[.exe]
    bin/{platform}/graphviz-{ver}/lib/          (shared libs)
    bin/{platform}/graphviz-{ver}/lib/graphviz/ (plugin libs, Unix only)

On Windows, Graphviz DLLs live alongside dot.exe in graphviz-{ver}/bin/.
"""

import os
import stat
import struct
import sys
from pathlib import Path


def _get_machine() -> str:
    """Get machine architecture without importing stdlib platform (avoids self-shadow)."""
    if hasattr(os, "uname"):
        return os.uname().machine.lower()
    # Fallback for Windows (no os.uname)
    arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").lower()
    if arch in ("amd64", "x86_64"):
        return "x86_64"
    if arch == "arm64":
        return "arm64"
    return "x86_64" if struct.calcsize("P") == 8 else "x86"


def detect_platform() -> str:
    """Detect the current platform and return the bin/ subdirectory name.

    Returns one of: 'win64', 'linux-x64', 'macos-arm64', 'macos-x64'
    """
    system = sys.platform
    machine = _get_machine()

    if system == "win32":
        return "win64"
    elif system == "linux":
        return "linux-x64"
    elif system == "darwin":
        if machine == "arm64":
            return "macos-arm64"
        return "macos-x64"
    else:
        raise RuntimeError(f"Unsupported platform: {system}/{machine}")


def get_skill_root() -> Path:
    """Return the absolute path to the skill root directory (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def get_bin_dir() -> Path:
    """Return the bin/{platform}/ directory."""
    return get_skill_root() / "bin" / detect_platform()


def _find_versioned_dir(base: Path, prefix: str) -> Path | None:
    """Find a directory matching `prefix*` under base (e.g. 'doxygen-' or 'graphviz-')."""
    if not base.is_dir():
        return None
    candidates = sorted(base.glob(f"{prefix}*"), reverse=True)
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _find_doxygen_dir() -> Path | None:
    """Find the doxygen-{ver}/ directory under the platform bin dir."""
    return _find_versioned_dir(get_bin_dir(), "doxygen-")


def _find_graphviz_dir() -> Path | None:
    """Find the graphviz-X.Y.Z/ directory under the platform bin dir."""
    return _find_versioned_dir(get_bin_dir(), "graphviz-")


def get_doxygen_path() -> Path:
    """Return the full path to the bundled doxygen binary.

    All platforms use: doxygen-{ver}/bin/doxygen[.exe]
    """
    plat = detect_platform()
    name = "doxygen.exe" if plat == "win64" else "doxygen"
    doxy_dir = _find_doxygen_dir()

    if doxy_dir is None:
        return get_bin_dir() / name

    return doxy_dir / "bin" / name


def get_dot_path() -> Path:
    """Return the full path to the bundled dot (Graphviz) binary.

    Searches in graphviz-X.Y.Z/bin/dot[.exe].
    """
    plat = detect_platform()
    name = "dot.exe" if plat == "win64" else "dot"
    gv_dir = _find_graphviz_dir()

    if gv_dir is None:
        return get_bin_dir() / name

    # Graphviz always uses bin/ subdirectory
    return gv_dir / "bin" / name


def get_dot_dir() -> str:
    """Return the directory containing dot, for Doxygen's DOT_PATH setting."""
    return str(get_dot_path().parent)


def ensure_executable(path: Path) -> None:
    """Ensure a file has executable permission (no-op on Windows)."""
    if sys.platform == "win32":
        return
    if path.exists():
        current = path.stat().st_mode
        path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def get_lib_dir() -> Path:
    """Return the primary lib/ directory for Graphviz shared libraries.

    On Unix:    graphviz-X.Y.Z/lib/
    On Windows: graphviz-X.Y.Z/bin/  (DLLs live alongside executables)
    """
    plat = detect_platform()
    gv_dir = _find_graphviz_dir()

    if gv_dir is None:
        return get_bin_dir() / "lib"

    if plat == "win64":
        return gv_dir / "bin"
    return gv_dir / "lib"


def get_graphviz_plugin_dir() -> Path:
    """Return the Graphviz plugin directory (lib/graphviz/).

    On Unix:    graphviz-X.Y.Z/lib/graphviz/
    On Windows: graphviz-X.Y.Z/bin/  (plugins are DLLs in bin/)
    """
    plat = detect_platform()
    gv_dir = _find_graphviz_dir()

    if gv_dir is None:
        return get_bin_dir() / "lib"

    if plat == "win64":
        return gv_dir / "bin"
    return gv_dir / "lib" / "graphviz"


def get_env_for_subprocess() -> dict:
    """Return an env dict with library paths set for Graphviz plugin resolution.

    Sets LD_LIBRARY_PATH (Linux), DYLD_LIBRARY_PATH (macOS), or PATH (Windows)
    so that dot can find its shared libraries and plugins.
    """
    env = os.environ.copy()
    lib_dir = str(get_lib_dir())
    plugin_dir = str(get_graphviz_plugin_dir())
    plat = detect_platform()

    if plat == "linux-x64":
        paths = [lib_dir, plugin_dir]
        existing = env.get("LD_LIBRARY_PATH", "")
        if existing:
            paths.append(existing)
        env["LD_LIBRARY_PATH"] = ":".join(paths)
    elif plat.startswith("macos"):
        paths = [lib_dir, plugin_dir]
        existing = env.get("DYLD_LIBRARY_PATH", "")
        if existing:
            paths.append(existing)
        env["DYLD_LIBRARY_PATH"] = ":".join(paths)
    elif plat == "win64":
        paths = [lib_dir]
        existing = env.get("PATH", "")
        if existing:
            paths.append(existing)
        env["PATH"] = ";".join(paths)

    # GVBINDIR tells Graphviz where to find its plugin config file
    env["GVBINDIR"] = plugin_dir

    return env


def main():
    """Print platform info for diagnostics."""
    plat = detect_platform()
    root = get_skill_root()
    doxygen = get_doxygen_path()
    dot = get_dot_path()
    lib = get_lib_dir()
    plugin = get_graphviz_plugin_dir()

    print(f"Platform:        {plat}")
    print(f"Skill root:      {root}")
    print(f"Doxygen dir:     {_find_doxygen_dir()}")
    print(f"Doxygen path:    {doxygen}")
    print(f"Doxygen exists:  {doxygen.exists()}")
    print(f"Graphviz dir:    {_find_graphviz_dir()}")
    print(f"Dot path:        {dot}")
    print(f"Dot exists:      {dot.exists()}")
    print(f"Dot dir:         {get_dot_dir()}")
    print(f"Lib dir:         {lib}")
    print(f"Plugin dir:      {plugin}")


if __name__ == "__main__":
    main()
