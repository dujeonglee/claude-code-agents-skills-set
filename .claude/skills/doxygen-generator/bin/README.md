# Bundled Binaries

This directory contains pre-built binaries for Doxygen and Graphviz (dot), organized by platform.

## Versions

| Tool      | Version | Source                                                      |
|-----------|---------|-------------------------------------------------------------|
| Doxygen   | 1.16.1  | https://www.doxygen.nl/download.html                        |
| Graphviz  | 14.1.2  | https://gitlab.com/graphviz/graphviz/-/releases             |

## Directory Structure

All platforms follow the same convention. Scripts auto-detect versioned directories by prefix.

```
bin/{platform}/
  doxygen-{ver}/
    bin/
      doxygen[.exe]             # Main binary
  graphviz-{ver}/
    bin/
      dot[.exe]                 # Graph renderer
    lib/
      (shared libraries)        # .dylib / .so / .dll
      graphviz/                 # Plugin libraries (Unix only)
```

Platform-specific shared library extensions:

| Platform    | Shared libs         | Plugins                  |
|-------------|---------------------|--------------------------|
| win64       | `bin/*.dll`         | `bin/gvplugin_*.dll`     |
| linux-x64   | `lib/*.so*`         | `lib/graphviz/*.so*`     |
| macos-arm64 | `lib/*.dylib`       | `lib/graphviz/*.dylib`   |
| macos-x64   | `lib/*.dylib`       | `lib/graphviz/*.dylib`   |

Note: On Windows, DLLs live alongside `dot.exe` in `bin/` (Windows convention).
On macOS, `libltdl.7.dylib` is bundled in `lib/` and `dot` is patched to load it
via `@loader_path/../lib/` — no Homebrew dependency required.

## Download Sources

### Doxygen

- **All platforms:** https://www.doxygen.nl/download.html
  - Windows: portable zip → rename to `doxygen-{ver}/`, move binaries into `bin/` subdir
  - Linux: binary tarball → extract as `doxygen-{ver}/` (already has `bin/` subdir)
  - macOS: DMG → create `doxygen-{ver}/bin/`, copy binaries from `Doxygen.app/Contents/Resources/`

### Graphviz

- **All platforms:** https://gitlab.com/api/v4/projects/4207231/packages/generic/graphviz-releases/14.1.2/
  - Windows: `windows_10_cmake_Release_Graphviz-14.1.2-win64.zip`
  - Linux: `ubuntu_22.04_graphviz-14.1.2-debs.tar.xz` (extract `.deb` files with `ar x` + `tar xf data.tar.zst`)
  - macOS: `Darwin_23.6.0_Graphviz-14.1.2-Darwin.zip`

## macOS Gatekeeper Workaround

On macOS, you may see "cannot be opened because the developer cannot be verified". Fix with:

```bash
xattr -cr bin/macos-arm64/
# or
xattr -cr bin/macos-x64/
```

## Git LFS

These binaries total ~100-150 MB across all platforms. Consider tracking them with Git LFS:

```bash
git lfs track "skills/doxygen-generator/bin/win64/**"
git lfs track "skills/doxygen-generator/bin/linux-x64/**"
git lfs track "skills/doxygen-generator/bin/macos-arm64/**"
git lfs track "skills/doxygen-generator/bin/macos-x64/**"
```

## Refreshing Binaries

To update to a newer version:
1. Download new binaries from the URLs above
2. Place them in `bin/{platform}/` using the versioned directory naming convention
3. The scripts auto-detect by directory prefix (`doxygen-*`, `graphviz-*`)
4. Test with: `python3 scripts/platform.py` and `python3 scripts/generate.py <workspace>`
