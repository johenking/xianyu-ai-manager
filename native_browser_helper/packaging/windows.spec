# PyInstaller spec for the Windows helper executable.
from pathlib import Path

from PyInstaller.building.build_main import Analysis, PYZ
from PyInstaller.building.api import EXE


ROOT = Path(SPECPATH).parent
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
    analysis.binaries,
    analysis.datas,
    [],
    name="XianyuNativeHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
