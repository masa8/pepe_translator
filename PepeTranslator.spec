# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['gpt.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PepeTranslator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app_icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PepeTranslator',
)
app = BUNDLE(
    coll,
    name='PepeTranslator.app',
    icon='assets/app_icon.icns',
    bundle_identifier='jp.agran.pepe.translator',
    info_plist={
        "NSMicrophoneUsageDescription": "This app requires access to the microphone for realtime translation.",
    },
)
