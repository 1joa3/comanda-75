# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['typing_extensions']
tmp_ret = collect_all('oracledb')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='AutoComanda',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AutoComanda',
)

import shutil
import os

dist_dir = os.path.join(DISTPATH, 'AutoComanda')

# Copia as pastas para a raiz do dist (ao lado do .exe e fora do _internal)
shutil.copytree('config', os.path.join(dist_dir, 'config'), dirs_exist_ok=True)
shutil.copytree('templates', os.path.join(dist_dir, 'templates'), dirs_exist_ok=True)
shutil.copytree('instantclient_19_24', os.path.join(dist_dir, 'instantclient_19_24'), dirs_exist_ok=True)

# Copia os scripts .bat
shutil.copy('instalar_autostart.bat', dist_dir)
shutil.copy('desinstalar_autostart.bat', dist_dir)
