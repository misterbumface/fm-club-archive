# PyInstaller recipe for "FM Club Archive.app" (docs/mac-app-plan.md, part B).
# Built by scripts/build_mac_app.sh; Apple Silicon, signed with this Mac's local certificate (else ad hoc).
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is given by PyInstaller

# What the app reads at run time besides its code: the pages, the shipped pictures and CSS, the
# library's migrations and the welcome page. Everything an archive writes lives in its own folder.
datas = [
    (str(ROOT / "app" / "templates"), "app/templates"),
    (str(ROOT / "app" / "static"), "app/static"),
    (str(ROOT / "migrations"), "migrations"),
    (str(ROOT / "macapp" / "launcher.html"), "macapp"),
]
# The archive's modules are imported inside functions (blueprints, services), and alembic loads the
# migrations by file: name them all.
hiddenimports = collect_submodules("app") + [
    "config",
    "macapp._version",
    "waitress",
    "webview.platforms.cocoa",
    "sqlalchemy.dialects.sqlite",
    "logging.config",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "macapp" / "main.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tkinter", "gunicorn", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FM Club Archive",
    console=False,
    target_arch="arm64",
    # Ad hoc here; the build script then signs the whole bundle with the local certificate. Signing with
    # it here signs in a stricter mode that refuses the bundled Python ("different Team IDs").
    codesign_identity=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="FM Club Archive")  # noqa: F821
app = BUNDLE(  # noqa: F821
    coll,
    name="FM Club Archive.app",
    bundle_identifier="com.fmclubarchive.app",
    info_plist={
        "CFBundleName": "FM Club Archive",
        "CFBundleDisplayName": "FM Club Archive",
        "CFBundleShortVersionString": "1.0",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        # The window shows the archive's own server on 127.0.0.1.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)
