"""Тип установщика дистрибутива и ключи тихой установки.

Раньше install_version дописывал /quiet любому .exe. У msiexec это тихая
установка, а установщик Inno Setup (все .exe-дистрибутивы Р7 на стенде,
проверено 07.10.2026 по строке «Inno Setup» в файлах) такой ключ не знает
и открывал мастер. Тип .exe определяется по содержимому: установщики
носят имя движка в своих ресурсах близко к началу файла (у дистрибутивов
Р7 — в первом мегабайте), поэтому читаются только первые
INSTALLER_SCAN_LIMIT байт.
"""
from pathlib import Path

INSTALLER_SCAN_LIMIT = 8 * 1024 * 1024
_CHUNK = 1024 * 1024

# Маркер в байтах файла → тип установщика.
_MARKERS = (
    (b"Inno Setup", "inno"),
    (b"Nullsoft", "nsis"),
)

# Ключи тихой установки по типу. unknown — пусто: тихо поставить нельзя,
# покажется мастер (install_version об этом предупреждает и ждёт дольше).
_SILENT_ARGS = {
    "msi": ["/quiet", "/norestart"],
    "inno": ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
    "nsis": ["/S"],
    "unknown": [],
}


def detect_installer_kind(path):
    """Тип дистрибутива: "msi", "inno", "nsis" или "unknown".

    .msi — по расширению. .exe — по маркеру движка в первых
    INSTALLER_SCAN_LIMIT байтах; файл читается кусками, стык кусков
    перекрывается на длину маркера, чтобы не потерять строку на границе.
    Нечитаемый файл — "unknown".
    """
    path = Path(path)
    if path.suffix.lower() == ".msi":
        return "msi"
    if path.suffix.lower() != ".exe":
        return "unknown"
    overlap = max(len(marker) for marker, _ in _MARKERS) - 1
    tail = b""
    read = 0
    try:
        with open(path, "rb") as f:
            while read < INSTALLER_SCAN_LIMIT:
                chunk = f.read(min(_CHUNK, INSTALLER_SCAN_LIMIT - read))
                if not chunk:
                    break
                read += len(chunk)
                window = tail + chunk
                for marker, kind in _MARKERS:
                    if marker in window:
                        return kind
                tail = window[-overlap:]
    except OSError:
        return "unknown"
    return "unknown"


def silent_args(kind):
    """Ключи тихой установки для типа из detect_installer_kind (копия списка)."""
    return list(_SILENT_ARGS.get(kind, []))
