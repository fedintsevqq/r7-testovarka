"""Запуск прогона вкладки «Производительность» и диалог после него.

Перед запуском проверяется, что не идёт другой прогон (_perf_running /
_batch_running): оба режима шлют клавиши в Р7. Сам прогон — воркер
редактора, выбранного переключателем над списком тестов
(r7.editors.EDITOR_WORKERS: _spreadsheet_worker, _document_worker,
_presentation_worker), в отдельном потоке. PerfTabMixin — методы, которые
R7Testovarka получает наследованием.
"""
import json
import shutil
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from r7 import config, env, readiness
from r7.batch_config import find_test_file
from r7.doc_fixtures import DOC_FIXTURE_NAME, find_doc_fixture
from r7.editors import EDITOR_DOCUMENT, EDITOR_SPREADSHEET, EDITOR_WORKERS
from r7.pptx_fixtures import PPTX_FIXTURE_NAME, find_pptx_fixture
from r7.run_state import PERF, missing_packages
from r7.env import pyperclip
from r7.ui.base import COLORS


class PerfTabMixin:
    """Запуск прогона и диалог после него — часть R7Testovarka (через наследование)."""

    def run_spreadsheet_test(self):
        """Entry point for the stress test — validates prerequisites then launches worker thread."""
        refusal = self.run_state.refusal(PERF)
        if refusal:
            messagebox.showwarning(*refusal)
            return
        # Права администратора прогону не нужны: без них только не сбросится
        # файловый кэш ОС перед открытием — _purge_os_file_cache напишет об
        # этом в журнал и в «Условия прогона» отчёта (r7/privileges.py).
        if not self.current_version_info:
            messagebox.showwarning("Нет версии", "Р7-Офис не установлен или не определён.")
            return
        _warn = readiness._missing_cdp_warning()
        if _warn and not messagebox.askyesno("Нет доступа к интерфейсу Р7", _warn):
            return
        missing = missing_packages(env.PYAUTOGUI_OK, bool(pyperclip), env.EXCEL_OK, env.WIN32_OK)
        if missing:
            messagebox.showerror("Ошибка",
                                 f"Отсутствуют библиотеки:\n{', '.join(missing)}\n"
                                 f"Установите: pip install " + " ".join(missing))
            return
        editor = self._perf_editor
        enabled = ({n for n, v in self.test_vars.items() if v.get()}
                   if self.test_vars else set(self._tab_test_names(editor)))
        if not enabled:
            messagebox.showwarning("Нет тестов", "Выберите хотя бы один тест для выполнения.")
            return
        # Снимок self.test_runs на главном потоке — как enabled_tests, чтобы
        # фоновый поток не трогал Tk-переменные напрямую. Сначала применяется
        # ввод, который ещё не подтверждён (число набрано, фокус не уходил).
        self._commit_runs_inputs()
        runs_snapshot = {}
        for n, v in self.test_runs.items():
            try:
                runs_snapshot[n] = self._clamp_runs(v.get(), self._default_test_entry(n)["runs"])
            except tk.TclError:
                runs_snapshot[n] = self._default_test_entry(n)["runs"]
        self._save_test_selection()

        def _prepare_ui():
            self.perf_stop_event.clear()
            self._set_busy_indicator(True)
            self.progress_var.set(0)
            self.btn_run_perf.config(state=tk.DISABLED)
            self.btn_stop_perf.config(state=tk.NORMAL)
            self._set_editor_selector_state(False)

        # Воркер — по редактору на переключателе: тот же, что у CLI
        # (`python -m r7 run --suite …`). Документ и презентация — режимы
        # общего _spreadsheet_worker (r7/doc_run.py, r7/pptx_run.py).
        worker = getattr(self, EDITOR_WORKERS[editor])
        # Кнопки возвращает _reset_perf_buttons в главном потоке при любом
        # исходе прогона: завершение, остановка, исключение.
        self._start_run(PERF, lambda: worker(enabled, runs_snapshot, self.perf_stop_event),
                        before=_prepare_ui, on_done=self._reset_perf_buttons)

    # ---------------------- Редактор на вкладке ----------------------
    def _set_editor_selector_state(self, enabled):
        """Переключатель редактора: недоступен, пока идёт прогон вкладки."""
        for rb in getattr(self, "_editor_radios", ()):
            try:
                rb.config(state=tk.NORMAL if enabled else tk.DISABLED)
            except tk.TclError:  # окно закрыто — переключателя уже нет
                pass

    def _on_perf_editor_selected(self):
        """Щелчок по переключателю: выбор прежнего редактора сохраняется,
        список тестов собирается заново — тесты и повторы нового редактора."""
        editor = self.perf_editor_var.get()
        if editor == self._perf_editor:
            return
        if self._perf_running:
            # Переключатель в прогоне выключен; это страховка от гонки.
            self.perf_editor_var.set(self._perf_editor)
            return
        self._commit_runs_inputs()
        self._cancel_pending_selection_save()
        self._save_test_selection()
        self._perf_editor = editor
        self._fill_test_rows()
        self._save_test_selection()          # запомнить, какой редактор выбран

    def _cancel_pending_selection_save(self):
        pending = getattr(self, "_save_selection_job", None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except tk.TclError:  # отложенное сохранение уже выполнилось
                pass
            self._save_selection_job = None

    def _perf_fixture_hint(self):
        """Подпись под переключателем: какой файл откроет прогон.

        Таблица — рабочая фикстура (её создаёт окно «Тестовые файлы»).
        Документ и презентация — своя фикстура; нет её — воркер создаёт
        файл сам при запуске (r7/doc_fixtures.py, r7/pptx_fixtures.py).
        """
        folder = Path(self.test_files_folder)
        editor = self._perf_editor
        if editor == EDITOR_SPREADSHEET:
            found, _locks = find_test_file(
                [folder, config.BASE_DIR, Path.home() / "Downloads",
                 Path.home() / "Загрузки", Path.cwd()])
            if found is None:
                return ("Файл: таблица не найдена — создайте её кнопкой "
                        "«Тестовые файлы»")
            return f"Файл: {found.name} ({found.parent})"
        if editor == EDITOR_DOCUMENT:
            found, name = find_doc_fixture([folder, config.BASE_DIR, Path.cwd()]), DOC_FIXTURE_NAME
        else:
            found, name = find_pptx_fixture([folder, config.BASE_DIR, Path.cwd()]), PPTX_FIXTURE_NAME
        if found is not None:
            return f"Файл: {found.name} ({found.parent})"
        return f"Файл: {name} — создастся сам в {folder} при запуске"

    def _update_fixture_hint(self):
        try:
            text = self._perf_fixture_hint()
        except Exception as e:  # папка недоступна — подсказка не должна ронять вкладку
            text = f"Файл: не удалось проверить папку ({e})"
        try:
            self.lbl_fixture_hint.config(text=text)
        except (AttributeError, tk.TclError):  # подпись ещё не создана или окно закрыто
            pass

    @staticmethod
    def _json_for_script(obj, **kwargs):
        r"""json.dumps(), но безопасный для вставки прямо внутрь <script>...</script>.

        Строковое значение, содержащее буквальную последовательность
        "</script", закрыло бы окружающий тег раньше времени — HTML-парсер
        браузера не знает, что находится внутри JS-строкового литерала, и
        видит закрывающий тег буквально. Версия/имя теста, попадающие сюда,
        приходят из простых текстов (реестр, simpledialog, JSON-файлы с
        диска), но ничто не мешает им случайно содержать такую подстроку.

        "<\/" — валидный экранированный слэш в JS-строках (не спецсимвол,
        декодируется в тот же "/"), который ломает поиск тега парсером HTML,
        не меняя значение после разбора JSON.
        """
        return json.dumps(obj, **kwargs).replace("</", r"<\/")

    def _show_post_test_dialog(self, html_path, ts):
        """Shows dialog after test completion: open report, new test, or exit."""
        dlg = tk.Toplevel(self.root)
        dlg.transient(self.root)
        dlg.configure(bg=COLORS["bg"])
        dlg.title("Тест завершён")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.focus_set()

        ttk.Label(dlg, text="Тест завершён", style="Header.TLabel").pack(
            pady=(24, 6), padx=40)
        ttk.Label(dlg, text="Что делать дальше?", style="Secondary.TLabel").pack(pady=(0, 20))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=(0, 24), padx=40)

        def show_report():
            if not Path(html_path).exists():
                # Сохранение HTML могло упасть — не открывать пустую ссылку молча.
                messagebox.showerror("Отчёт не сохранён",
                                     "HTML-отчёт не записан, причина — в журнале теста.",
                                     parent=dlg)
                return
            webbrowser.open(str(html_path))
            dlg.destroy()
            if messagebox.askyesno("Сохранить копию", "Сохранить копию HTML-отчёта?"):
                save_path = filedialog.asksaveasfilename(
                    defaultextension=".html",
                    filetypes=[("HTML files", "*.html"), ("All files", "*.*")],
                    initialfile=f"Performance_Report_{ts}.html"
                )
                if save_path:
                    shutil.copy(str(html_path), save_path)
                    self.add_test_log(f"📎 Копия отчёта сохранена: {save_path}")

        def new_test():
            dlg.destroy()
            self._reset_test_state()

        def exit_app():
            dlg.destroy()
            self.root.quit()

        self._icon_button(btn_frame, "Показать отчёт", "report", command=show_report,
                          style="Accent.TButton").pack(side=tk.LEFT, padx=5)
        self._icon_button(btn_frame, "Новый тест", "refresh", command=new_test
                          ).pack(side=tk.LEFT, padx=5)
        self._icon_button(btn_frame, "Выход", "uncheck", command=exit_app
                          ).pack(side=tk.LEFT, padx=5)

        dlg.update_idletasks()
        w = dlg.winfo_reqwidth()
        h = dlg.winfo_reqheight()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")

    def _reset_test_state(self):
        """Clears the test log and resets the status bar for a new run."""
        self.test_log.delete("1.0", tk.END)
        self.status_var.set("Готов")
        self.add_test_log("🔄 Готов к новому тесту.")
