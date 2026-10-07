"""Сравнение версий, тренды и тест своего файла: выбор отчётов, окна с
результатами, открытие HTML-страниц. Окна — r7/ui/compare_dialog.py и
r7/ui/fixture_dialog.py.

Статистика — r7.stats.compare_runs, вид страниц — r7_reports.py.
CompareMixin — методы, которые R7Testovarka получает наследованием.
"""
import webbrowser
from datetime import datetime
from tkinter import messagebox

import r7_reports
from r7.compare_files import scan_reports
from r7.ui.compare_dialog import CompareDialog
from r7.ui.fixture_dialog import FixtureDialog


class CompareMixin:
    """Сравнение версий, тренды, тест своего файла — часть R7Testovarka."""

    def compare_versions(self):
        """Opens dialog to select 2-10 performance JSON files and builds a comparison report.

        Окно — r7.ui.compare_dialog.CompareDialog."""
        settings = self._load_comparison_settings()
        initial_meta = scan_reports(self.reports_folder, settings.get("custom_names", {}))
        if len(initial_meta) < 2:
            self.add_test_log(
                f"⚠️ Сравнение версий: найдено {len(initial_meta)} файлов "
                f"performance_full_*.json (нужно минимум 2)")
            messagebox.showwarning(
                "Недостаточно данных",
                "Для сравнения нужно минимум 2 файла performance_full_*.json.\n"
                "Запустите тесты для нескольких версий R7-Office."
            )
            return
        try:
            CompareDialog(self, initial_meta, settings)   # недостроенное окно закроет само
        except Exception as ex:
            self.add_test_log(f"❌ Ошибка при построении окна сравнения версий: {ex}")
            messagebox.showerror("Ошибка", f"Не удалось открыть окно сравнения версий:\n{ex}")

    def show_trends(self):
        """Строит и открывает в браузере страницу трендов по всем
        накопленным performance_full_*.json. Точка входа из UI (кнопка
        «📈 Тренды» рядом с «Сравнить версии»)."""
        runs = self._load_trends_runs()
        if len(runs) < 2:
            messagebox.showinfo(
                "Недостаточно данных",
                f"Найдено {len(runs)} файлов performance_full_*.json "
                f"(нужно минимум 2 для тренда).\nЗапустите тесты несколько раз.")
            return
        html_content = self._generate_trends_html(runs)
        ts_now = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.reports_folder / f"trends_{ts_now}.html"
        try:
            out_path.write_text(html_content, encoding="utf-8")
            self.add_test_log(f"📈 Страница трендов: {out_path.name}")
            webbrowser.open(str(out_path))
        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка сохранения страницы трендов: {e}")
            messagebox.showerror("Ошибка", f"Не удалось сохранить страницу трендов:\n{e}")

    def compare_file_sizes(self):
        """Opens the test-file generation dialog with 4 separate action buttons.

        Окно — r7.ui.fixture_dialog.FixtureDialog; возврат — после его закрытия."""
        FixtureDialog(self).show()

    def _show_custom_test_report(self, result):
        """Отчёт по своему файлу (templates/reports/custom.html): строит,
        сохраняет и открывает в браузере."""
        html_content = r7_reports.render("custom.html", **r7_reports.custom_model(result))
        ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = self.reports_folder / f"custom_test_{ts_file}.html"
        try:
            out_path.write_text(html_content, encoding="utf-8")
            self.add_test_log(f"📊 Отчёт готов: {out_path.name}")
            webbrowser.open(str(out_path))
        except Exception as e:
            self.add_test_log(f"⚠️ Ошибка записи отчёта: {e}")
