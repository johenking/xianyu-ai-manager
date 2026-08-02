# PyInstaller spec for a signed macOS application bundle.
from pathlib import Path

from PyInstaller.building.build_main import Analysis, PYZ
from PyInstaller.building.api import COLLECT, EXE
from PyInstaller.building.osx import BUNDLE


ROOT = Path(SPECPATH).parent
VERSION = "1.0.1"
analysis = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT.parent)],
    binaries=[],
    datas=[],
    hiddenimports=["websocket", "websocket._abnf"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    name="XianyuNativeHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    exclude_binaries=True,
)
collected = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="XianyuNativeHelper",
)
BUNDLE(
    collected,
    name="XianyuNativeHelper.app",
    version=VERSION,
    bundle_identifier="com.cxywjx.xianyu-native-helper",
    info_plist={
        "LSUIElement": True,
        "NSLocalNetworkUsageDescription": "连接本机 Chrome 以完成官方账号登录。",
    },
)
