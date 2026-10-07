"""Решения диалогов без Tk: что выбрано для Batch, годится ли это для
запуска, где искать рабочую фикстуру (этап 4 плана, 07.10.2026).

Диалог только собирает ввод и показывает отказ; проверка — чистые функции,
которые тестируются без окна. Прежде проверки жили внутри on_start диалога,
а тест старта Batch пришлось собирать на живом окне Tk.
"""
import re
from dataclasses import dataclass
from pathlib import Path

# Рабочая фикстура: 50 000 строк × 50 столбцов, имя в латинице — его создаёт
# генератор («Тестовые файлы») при этих размерах. Прежнее имя
# «файл-для-теста-Р7-офис-50К.xlsx» на стендах остаётся: «й» в нём хранится
# в NFD, на другом ПК то же имя в NFC с литеральным шаблоном не совпадёт,
# поэтому после точных шаблонов идут запасные «*50К*» (кириллица) и «*50k*».
# Порядок — порядок предпочтения внутри одной папки.
FIXTURE_NAME = "r7-test-50k.xlsx"
FIXTURE_ROWS = 50_000
FIXTURE_COLS = 50
LEGACY_FIXTURE_NAME = "файл-для-теста-Р7-офис-50К.xlsx"
TEST_FILE_PATTERNS = ("r7-test-50k*.xlsx",
                      "файл-для-теста-Р7-офис-50К*.xlsx", "файл-для-теста-Р7-офис-50К*.xls",
                      "*50К*.xlsx", "*50k*.xlsx")
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

    Внутри папки шаблоны идут по порядку TEST_FILE_PATTERNS: новое имя
    FIXTURE_NAME предпочтительнее прежнего кириллического, если лежат оба.

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


# ── Генератор тестового файла (окно «📄 Тестовые файлы») ─────────────────

ROWS_RANGE = (1_000, 1_000_000)
COLS_RANGE = (1, 100)


def validate_fixture_dims(rows, cols):
    """Строки и столбцы генератора. Returns: (rows, cols, None) или
    (None, None, (поле, текст)) — поле «rows»/«cols», куда вернуть фокус.

    Прежде проверка шла через assert — под python -O она исчезала бы."""
    try:
        r = int(str(rows).strip())
    except ValueError:
        r = None
    if r is None or not ROWS_RANGE[0] <= r <= ROWS_RANGE[1]:
        return None, None, ("rows", "Строки: от 1 000 до 1 000 000.")
    try:
        c = int(str(cols).strip())
    except ValueError:
        c = None
    if c is None or not COLS_RANGE[0] <= c <= COLS_RANGE[1]:
        return None, None, ("cols", "Столбцы: от 1 до 100.")
    return r, c, None


def fixture_file_name(name):
    """Имя создаваемого файла: латиница, цифры, «_» и «.», с .xlsx на конце.
    Returns: (имя, None) или (None, текст ошибки)."""
    import re
    name = (name or "").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9_.]+", name):
        return None, "Имя файла: только латиница, цифры, '_' и '.'."
    return (name if name.endswith(".xlsx") else name + ".xlsx"), None


def auto_fixture_name(rows, cols):
    """Имя файла по размерам. Размеры рабочей фикстуры дают FIXTURE_NAME —
    тот файл, который find_test_file ищет первым."""
    rows, cols = int(rows), int(cols)
    if (rows, cols) == (FIXTURE_ROWS, FIXTURE_COLS):
        return FIXTURE_NAME
    return f"test_data_{rows}x{cols}.xlsx"
