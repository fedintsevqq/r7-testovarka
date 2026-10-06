"""Тестовые файлы: генерация фикстур XLSX заданного профиля и размера.

Фикстура детерминирована (seed), профили — плоские данные, формулы,
стили, смешанный. FixturesMixin — методы, которые R7Testovarka получает
наследованием.
"""
import random

from r7 import env
from r7.env import Font, PatternFill, Workbook, WriteOnlyCell


class FixturesMixin:
    """Генерация тестовых файлов — часть R7Testovarka (через наследование)."""

    def _get_xlsx_row_count(self, path):
        """Returns data row count (excluding header row) via openpyxl read-only, or None."""
        if not env.EXCEL_OK:
            return None
        try:
            from openpyxl import load_workbook as _lw
            wb = _lw(str(path), read_only=True, data_only=True)
            max_row = wb.active.max_row
            wb.close()
            if not max_row:
                # Файл без записи о размерах листа (так пишет openpyxl в
                # write_only-режиме — им создаются тестовые файлы): число
                # строк неизвестно, а не «0 строк».
                return None
            return max(0, max_row - 1)
        except Exception as e:
            self.add_test_log(f"⚠️ Не удалось прочитать количество строк: {e}")
            return None

    def _generate_fixture(self, path, rows=100_000, profile="flat", seed=42, cols=6):
        """Генерирует .xlsx для нагрузочного тестирования по одному из
        FIXTURE_PROFILES.

        Args:
            path: Куда сохранить файл.
            rows: Число строк данных (без строки заголовка).
            profile: "flat" (числа/текст — парсер и аллокация ячеек),
                "formula" (цепочка формул — движок пересчёта),
                "styled" (уникальный стиль почти на каждой строке —
                таблица стилей), "mixed" (всё сразу).
            seed: Сид генератора случайных чисел. Одно и то же значение
                (по умолчанию 42) даёт БУКВАЛЬНО одинаковый файл на
                повторном вызове — обязательное условие для сравнения
                версий Р7 на одинаковой нагрузке, а не на случайно разных
                файлах одного размера.
            cols: Число столбцов данных. Используется только профилем
                "flat" — у "formula"/"styled"/"mixed" набор колонок
                фиксирован их структурой (цепочка формул/стиль/оба сразу).

        Returns:
            Path: тот же path — для цепочки вызовов.

        Raises:
            RuntimeError: openpyxl не установлен.
            ValueError: profile не входит в FIXTURE_PROFILES.
        """
        if not env.EXCEL_OK:
            raise RuntimeError("openpyxl не установлен")
        if profile not in self.FIXTURE_PROFILES:
            raise ValueError(
                f"неизвестный профиль фикстуры: {profile!r} "
                f"(допустимо: {', '.join(self.FIXTURE_PROFILES)})")

        rnd = random.Random(seed)
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Данные")

        {
            "flat": self._fixture_fill_flat,
            "formula": self._fixture_fill_formula,
            "styled": self._fixture_fill_styled,
            "mixed": self._fixture_fill_mixed,
        }[profile](ws, rows, cols, rnd)

        wb.save(str(path))
        return path

    @staticmethod
    def _fixture_fill_flat(ws, rows, cols, rnd):
        """flat: числа + короткий разнородный текст. Нагружает парсер
        значений и аллокацию ячеек — не движок пересчёта и не стили."""
        ws.append(["ID", "Name", "Qty"] + [f"Col{c}" for c in range(4, cols + 1)])
        for i in range(1, rows + 1):
            row = [i, f"Позиция {i} {rnd.randint(1, 999999)}", rnd.randint(1, 10_000)]
            for _ in range(4, cols + 1):
                row.append(round(rnd.random() * 1000, 2))
            ws.append(row)

    @staticmethod
    def _fixture_fill_formula(ws, rows, cols, rnd):
        """formula: колонка "Chain" каждой строки ссылается на "Chain"
        предыдущей — граф зависимостей длиной rows, который движок
        пересчёта не может распараллелить (в отличие от rows независимых
        формул).

        Ссылка на предыдущую строку — C{i}, НЕ C{i-1}: заголовок занимает
        строку листа 1, данные index i лежат в строке i+1, поэтому «Chain»
        предыдущего index (i-1) — это строка листа i, а не i-1. C{i-1} для
        i=2 указал бы на C1 — строку заголовка, а не на данные (ловилось
        тестом test_formula_profile_chains_to_previous_row)."""
        ws.append(["ID", "Base", "Chain", "Qty"])
        ws.append([1, rnd.randint(1, 1000), rnd.randint(1, 1000), rnd.randint(1, 10_000)])
        for i in range(2, rows + 1):
            ws.append([i, rnd.randint(1, 1000),
                      f"=C{i}*1.02+{rnd.randint(1, 50)}",
                      rnd.randint(1, 10_000)])

    @classmethod
    def _fixture_fill_styled(cls, ws, rows, cols, rnd):
        """styled: у почти каждой строки свой шрифт/заливка — раздувает
        таблицу стилей (styles.xml), а не таблицу данных. Именно это НЕ
        нагружает «жирный текст» и обычные flat-фикстуры."""
        ws.append(["ID", "Name", "Status"])
        for i in range(1, rows + 1):
            color = cls._FIXTURE_STYLE_PALETTE[i % len(cls._FIXTURE_STYLE_PALETTE)]
            name_cell = WriteOnlyCell(ws, value=f"Позиция {i}")
            name_cell.font = Font(bold=(i % 3 == 0), size=9 + (i % 4))
            name_cell.fill = PatternFill(start_color=color, end_color=color,
                                         fill_type="solid")
            ws.append([i, name_cell, rnd.choice(["ок", "ждём", "отказ"])])

    @classmethod
    def _fixture_fill_mixed(cls, ws, rows, cols, rnd):
        """mixed: числа + формулы + стили через строку — ближе всего к
        реальному документу, где узкое место заранее не известно.

        Ссылка D{i} (не D{i-1}) по той же причине, что и в formula-профиле:
        заголовок в строке листа 1 сдвигает данные index i в строку i+1."""
        ws.append(["ID", "Name", "Qty", "Formula", "Status"])
        ws.append([1, "Позиция 1", rnd.randint(1, 10_000), rnd.randint(1, 10_000), "ок"])
        for i in range(2, rows + 1):
            name_cell = WriteOnlyCell(ws, value=f"Позиция {i}")
            if i % 5 == 0:
                color = cls._FIXTURE_STYLE_PALETTE[i % len(cls._FIXTURE_STYLE_PALETTE)]
                name_cell.font = Font(bold=True)
                name_cell.fill = PatternFill(start_color=color, end_color=color,
                                             fill_type="solid")
            ws.append([i, name_cell, rnd.randint(1, 10_000),
                      f"=D{i}*2+{rnd.randint(1, 20)}",
                      rnd.choice(["ок", "ждём", "отказ"])])

    def _generate_custom_test_file(self, rows, cols, path):
        """Creates an xlsx file with rows×cols of test data using openpyxl.

        write_only=True: обычный Workbook() держит все объекты ячеек в
        памяти до save(). При заявленном максимуме 1 000 000 строк × 100
        столбцов это 100 млн объектов ячеек одновременно. В write_only-режиме
        openpyxl пишет каждую добавленную строку сразу в поток архива и не
        накапливает их — единственное отличие в API: лист создаётся через
        wb.create_sheet(), а не берётся готовым через wb.active (write_only
        workbook стартует без единого листа).
        """
        if not env.EXCEL_OK:
            raise RuntimeError("openpyxl не установлен")
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("Лист1")
        # Заголовки
        header = ["ID", "Name"] + [f"Col{i}" for i in range(3, cols + 1)]
        ws.append(header)
        # Данные
        for i in range(1, rows + 1):
            row = [i, f"Item_{i:05d}"]
            for c in range(3, cols + 1):
                row.append(i * c)
            ws.append(row)
        wb.save(str(path))
