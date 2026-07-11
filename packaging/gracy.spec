# PyInstaller spec for the standalone `gracy` binary.
#   pyinstaller packaging/gracy.spec
# Produces a single self-contained executable in dist/ that bundles the Python
# runtime, the compiled Rust core (gracy._core), and the REPL deps (rich,
# prompt_toolkit) — no Python install needed on the target machine.
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas: list = []
binaries: list = []
hiddenimports: list = ["gracy._core"]

# The REPL pulls rich + prompt_toolkit in lazily, so PyInstaller's static
# analysis misses them — collect them (and gracy's own submodules) explicitly.
for pkg in ("rich", "prompt_toolkit"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

hiddenimports += collect_submodules("gracy")

a = Analysis(
    ["gracy_entry.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gracy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
