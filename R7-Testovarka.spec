# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec-файл для R7-Testovarka
#
# Сборка:
#   pyinstaller R7-Testovarka.spec
# Проверка собранного exe (CI делает это сам):
#   dist\R7-Testovarka.exe --self-check
#
# Или напрямую (без spec):
#   pyinstaller --onefile --name="R7-Testovarka" --uac-admin --console r7_Testovarka.py
#
# После сборки рядом с R7-Testovarka.exe нужно разместить папки:
#   Distributives\   — дистрибутивы .msi / .exe
#   TestFiles\       — тестовые .xlsx файлы (создаётся автоматически)
#   Reports\         — отчёты HTML / JSON   (создаётся автоматически)

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

a = Analysis(
    ['r7_Testovarka.py'],
    pathex=[],
    binaries=[],
    datas=[('templates', 'templates'),    # шаблоны HTML-отчётов (r7_reports.py)
           *collect_data_files('sv_ttk')],  # тема интерфейса: .tcl и картинки
    hiddenimports=[
        *collect_submodules('r7'),   # пакет целиком: часть модулей импортируется не напрямую
        'win32gui',
        'win32con',
        'win32api',
        'pywintypes',
        'psutil',
        'pyautogui',
        'pyperclip',
        'openpyxl',
        'openpyxl.styles',
        'openpyxl.utils',
        'PIL',
        'PIL.Image',
        'PIL.ImageTk',
        'sv_ttk',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='R7-Testovarka',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # консоль видна — полезно для диагностики
    uac_admin=True,         # запрашивает права администратора (UAC)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
