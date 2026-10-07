"""Удобство интерфейса: размер окна, поле повторов, список тестов, прокрутка.

Чистые функции (_fit_window, _clamp_runs, _short_version_text, группы тестов)
проверяются без Tk. Остальное — на настоящем окне: без него не проверить,
что панель кнопок не обрезается и колесо мыши прокручивает список. Окно
не показывается (withdraw), файл выбора тестов пользователя не пишется.
"""
import tkinter as tk
from tkinter import ttk

import pytest

import r7_Testovarka as r7mod
from r7 import config as r7config  # noqa: E402

R = r7mod.R7Testovarka


# ── Размер окна ─────────────────────────────────────────────────────────────

def test_fit_window_uses_default_size_when_ui_is_small():
    w, h, x, y, zoomed = R._fit_window(860, 470, (0, 0, 1920, 1040))
    assert (w, h, zoomed) == (R.DEFAULT_WIN_W, R.DEFAULT_WIN_H, False)
    assert x > 0 and y >= 0


def test_fit_window_grows_to_what_ui_needs():
    """Масштаб 150%: интерфейс просит больше констант — окно растёт."""
    w, h, _x, _y, zoomed = R._fit_window(1300, 820, (0, 0, 1920, 1040))
    assert (w, h, zoomed) == (1300, 820, False)


def test_fit_window_stays_inside_work_area():
    """Ноутбук 1366x728 без панели задач: окно не уходит за край."""
    w, h, x, y, zoomed = R._fit_window(860, 470, (0, 0, 1366, 728))
    assert not zoomed
    assert x + w + 16 <= 1366 and y + h + 40 <= 728


def test_fit_window_zooms_when_ui_does_not_fit():
    *_, zoomed = R._fit_window(1500, 900, (0, 0, 1366, 728))
    assert zoomed


def test_fit_window_respects_work_area_offset():
    """Панель задач слева или сверху: окно начинается внутри рабочей области."""
    _w, _h, x, y, _z = R._fit_window(860, 470, (60, 40, 1860, 1000))
    assert x >= 60 and y >= 40


# ── Поле числа повторов ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("7", 7), (" 3 ", 3), ("0", r7config.RUNS_MIN), ("99", r7config.RUNS_MAX),
    ("", 5), ("abc", 5), (None, 5), (12, 12),
])
def test_clamp_runs(raw, expected):
    assert R._clamp_runs(raw, 5) == expected


# ── Шапка ───────────────────────────────────────────────────────────────────

def test_short_version_text_drops_edition_suffix_keeps_build():
    info = {"name": "Р7-Офис. Профессиональный (десктопная версия)", "version": "2026.3.2.3229"}
    assert R._short_version_text(info) == "Р7-Офис. Профессиональный · 2026.3.2.3229"


def test_short_version_text_without_name():
    assert R._short_version_text({"name": "", "version": "1.2"}) == "1.2"


# ── Группы и умолчания ──────────────────────────────────────────────────────

def test_test_groups_cover_every_test_once_in_order(bare_r7):
    names = [n for _title, group in bare_r7._test_groups() for n in group]
    assert names == list(R.TEST_DEFINITIONS)


def test_exports_are_in_their_own_group(bare_r7):
    groups = dict(bare_r7._test_groups())
    export_group = [g for t, g in groups.items() if "ЭКСПОРТ" in t][0]
    assert set(export_group) == R.EXPORT_TESTS


def test_default_entries(bare_r7):
    pdf = "Сохранение в PDF (конвертация x2t)"
    assert bare_r7._default_test_entry(pdf) == {"enabled": True, "runs": R.DEFAULT_FORMAT_TEST_RUNS}
    ods = "Сохранение в ODS (конвертация x2t)"
    assert bare_r7._default_test_entry(ods)["enabled"] is False
    assert bare_r7._default_test_entry(R.OPEN_TEST_NAME)["runs"] == R.DEFAULT_OPEN_RUNS
    assert bare_r7._default_test_entry("Функция ВПР (50K строк)")["runs"] == r7config.DEFAULT_TEST_RUNS


# ── Настоящее окно ──────────────────────────────────────────────────────────

@pytest.fixture
def app(monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    saved = []
    monkeypatch.setattr(R, "_load_test_selection", lambda self: {})
    monkeypatch.setattr(R, "_save_test_selection", lambda self: saved.append(1))
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    inst = R(root)
    inst._saved_calls = saved
    root.update()
    yield inst
    root.destroy()


def _entry_of(ctl):
    return [c for c in ctl.winfo_children() if isinstance(c, ttk.Entry)][0]


def _buttons_of(ctl):
    return [c for c in ctl.winfo_children() if isinstance(c, ttk.Button)]


def test_runs_control_steps_and_clamps(app):
    name = "Функция ВПР (50K строк)"
    idx = list(app.test_runs).index(name)
    ctl = app._runs_controls[idx]
    minus, plus = _buttons_of(ctl)
    app.test_runs[name].set(r7config.RUNS_MAX)
    plus.invoke()
    assert app.test_runs[name].get() == r7config.RUNS_MAX
    minus.invoke()
    assert app.test_runs[name].get() == r7config.RUNS_MAX - 1
    entry = _entry_of(ctl)
    entry.delete(0, tk.END)
    ctl.commit()
    assert app.test_runs[name].get() == r7config.RUNS_MAX - 1   # пустое поле не роняет
    assert entry.get() == str(r7config.RUNS_MAX - 1)


def test_runs_entry_accepts_only_digits(app):
    entry = _entry_of(app._runs_controls[0])
    entry.delete(0, tk.END)
    entry.insert(0, "a")
    assert entry.get() == ""
    entry.insert(0, "123")   # больше двух цифр
    assert entry.get() == ""


def test_bulk_runs_apply_only_to_checked(app):
    app._set_all_tests(False)
    on = "Функция ВПР (50K строк)"
    off = "Удаление столбца (Del)"
    app.test_vars[on].set(True)
    before_off = app.test_runs[off].get()
    app._bulk_runs.set(11)
    app._apply_bulk_runs()
    assert app.test_runs[on].get() == 11
    assert app.test_runs[off].get() == before_off


def test_group_toggle_and_summary(app):
    app._set_all_tests(False)
    exports = [n for n in R.TEST_DEFINITIONS if n in R.EXPORT_TESTS]
    app._toggle_group(exports)
    assert all(app.test_vars[n].get() for n in exports)
    assert "экспорт идёт долго" in app.lbl_tests_summary.cget("text")
    app._toggle_group(exports)
    assert not any(v.get() for v in app.test_vars.values())
    assert str(app.btn_run_perf.cget("state")) == tk.DISABLED   # нечего запускать


def test_selection_change_is_saved_without_run(app):
    """Выбор сохраняется сам — раньше только при нажатии «Запустить»."""
    app.test_vars["Функция ВПР (50K строк)"].set(False)
    app.root.after(900, app.root.quit)
    app.root.mainloop()
    assert app._saved_calls


def test_first_log_message_replaces_hint(app):
    assert "Здесь появится ход прогона" in app.test_log.get("1.0", tk.END)
    app.add_test_log("✅ старт")
    text = app.test_log.get("1.0", tk.END)
    assert "Здесь появится" not in text and "старт" in text


def test_log_is_read_only_from_keyboard(app):
    app.add_test_log("строка")
    before = app.test_log.get("1.0", tk.END)
    app.test_log.focus_force()
    app.test_log.event_generate("<Key>", keysym="1", when="now")
    assert app.test_log.get("1.0", tk.END) == before


def test_run_button_visible_at_minimum_size(app):
    root = app.root
    root.deiconify()
    root.state("normal")
    root.geometry(f"{R.MIN_WIN_W}x{R.MIN_WIN_H}")
    app.notebook.select(app.tab_perf)
    for _ in range(3):
        root.update()
    btn = app.btn_run_perf
    bottom = btn.winfo_rooty() + btn.winfo_height() - root.winfo_rooty()
    assert btn.winfo_viewable() and bottom <= root.winfo_height()
    app.notebook.select(app.tab_versions)
    root.update()
    ib = app.btn_install
    assert ib.winfo_rooty() + ib.winfo_height() - root.winfo_rooty() <= root.winfo_height()
    # Вкладка «Сценарии»: все три «Запустить» и «Остановить» в пределах окна.
    app.notebook.select(app.tab_scenarios)
    for _ in range(3):
        root.update()
    for b in (*app.scenario_run_buttons.values(), app.btn_stop_scenario):
        assert b.winfo_viewable()
        assert b.winfo_rooty() + b.winfo_height() - root.winfo_rooty() <= root.winfo_height()
        assert b.winfo_rootx() + b.winfo_width() - root.winfo_rootx() <= root.winfo_width()


def test_mouse_wheel_scrolls_test_list(app):
    root = app.root
    root.deiconify()
    root.state("normal")
    root.geometry(f"{R.MIN_WIN_W}x{R.MIN_WIN_H}")
    app.notebook.select(app.tab_perf)
    for _ in range(3):
        root.update()
    ctl = app._runs_controls[3]
    canvas = ctl.master.master
    assert isinstance(canvas, tk.Canvas)
    before = canvas.yview()[0]
    ctl.event_generate("<MouseWheel>", delta=-120, when="now")
    root.update()
    assert canvas.yview()[0] > before


# ── Диалоги ─────────────────────────────────────────────────────────────────

def test_wheel_scrolls_list_when_pointer_is_over_a_row(app):
    """Колесо над строкой-флажком прокручивает список — так устроен список
    версий Batch-режима. Сам диалог Batch здесь не открывается: он модальный
    и ждёт ответа пользователя."""
    root = app.root
    root.deiconify()
    dlg = tk.Toplevel(root)
    canvas = tk.Canvas(dlg, height=100)
    inner = ttk.Frame(canvas)
    canvas.create_window((0, 0), window=inner, anchor="nw")
    rows = [ttk.Checkbutton(inner, text=f"r7-office_{i}.exe") for i in range(20)]
    for r in rows:
        r.pack(anchor=tk.W)
    canvas.pack()
    root.update()
    canvas.configure(scrollregion=canvas.bbox("all"))
    app._bind_wheel(canvas, canvas, inner)
    rows[2].event_generate("<MouseWheel>", delta=-120, when="now")
    root.update()
    assert canvas.yview()[0] > 0


def test_toplevel_opened_in_screen_corner_is_centered(app):
    root = app.root
    root.deiconify()
    root.state("normal")
    root.geometry("900x600+400+200")
    root.update()
    dlg = tk.Toplevel(root)
    ttk.Label(dlg, text="диалог").pack(padx=40, pady=40)
    dlg.geometry("+0+0")
    for _ in range(5):
        root.update()
    cx = dlg.winfo_rootx() + dlg.winfo_width() // 2
    assert root.winfo_rootx() <= cx <= root.winfo_rootx() + root.winfo_width()
