"""Решения диалогов без Tk: что выбрано для Batch, годится ли это для
запуска, где искать рабочую фикстуру (этап 4 плана, 07.10.2026).

Диалог только собирает ввод и показывает отказ; проверка — чистые функции,
которые тестируются без окна. Прежде проверки жили внутри on_start диалога,
а тест старта Batch пришлось собирать на живом окне Tk.
"""
import re
from dataclasses import dataclass
from pathlib import Path

# Рабочая фикстура. «й» в имени файла хранится в NFD — литеральный шаблон
# может не совпасть, поэтому последним идёт запасной «*50К*.xlsx».
TEST_FILE_PATTERNS = ("файл-для-теста-Р7-офис-50К*.xlsx", "файл-для-теста-Р7-офис-50К*.xls",
                      "*50К*.xlsx")
DISTRIBUTIVE_PATTERNS = ("*.msi", "*.exe")


@dataclass(frozen=True)
class BatchConfig:
    """Параметры Batch-прогона, собранные диалогом."""
    versions: tuple
    test_file: Path
    stop_on_error: bool = True
    cleanup: bool = False


def validate_batch_config(selected, test_file, stop_on_error=True, cleanup=False):
    """Проверяет выбор в диалоге Batch.

    Args:
        selected: выбранные дистрибутивы (Path).
        test_file: путь к тестовому файлу как ввёл пользователь (str/Path/None).

    Returns:
        tuple[BatchConfig | None, tuple[str, str] | None]: конфигурация либо
        отказ (заголовок, текст) для окна.
    """
    if not selected:
        return None, ("Нет выбора", "Выберите хотя бы одну версию.")
    tf = str(test_file or "").strip()
    if not tf or not Path(tf).is_file():
        return None, ("Файл не найден", "Укажите существующий тестовый файл.")
    return BatchConfig(tuple(selected), Path(tf), bool(stop_on_error), bool(cleanup)), None


def list_distributives(folder, version_key):
    """Дистрибутивы Р7 в папке (.msi/.exe), по версии из имени.

    Args:
        folder: папка Distributives.
        version_key: функция «имя без расширения → строка версии ('v2026.3.2')
            или None» (R7Testovarka._extract_version); без версии — в конец, по имени.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = [f for pat in DISTRIBUTIVE_PATTERNS for f in folder.glob(pat)]

    def key(f):
        # Версия — числами, не строкой: прежде 'v2026.10.1' шла раньше
        # 'v2026.9', и Batch ставил версии не по порядку выпуска.
        ver = version_key(f.stem)
        nums = tuple(int(x) for x in re.findall(r"\d+", ver)) if ver else None
        return (nums is None, nums or (), f.name)
    return sorted(files, key=key)


def find_test_file(search_dirs, patterns=TEST_FILE_PATTERNS):
    """Ищет рабочую фикстуру по папкам по порядку.

    Office-файлы блокировки (`~$…`) пропускаются: пока файл открыт где-то
    ещё (или после сбоя), glob находил их вместо настоящего файла.

    Returns:
        tuple[Path | None, list[Path]]: найденный файл (из первой папки, где
        он есть) и файлы блокировки, встреченные по пути.
    """
    real_file, locks = None, []
    for sd in search_dirs:
        sd = Path(sd)
        if not sd.exists():
            continue
        for pat in patterns:
            for f in sd.glob(pat):
                if f.name.startswith("~$"):
                    if f not in locks:
                        locks.append(f)
                elif real_file is None:
                    real_file = f
        if real_file is not None:
            break
    return real_file, locks
