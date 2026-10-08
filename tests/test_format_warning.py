"""Диалог-предупреждение формата после «Сохранить как» (r7/export.py):
окна редактора Р7 (Qt) не принимаются за диалог."""
import types

import r7.export as export
import r7.windows as r7windows


class _Gui:
    """Окна: hwnd → (класс, заголовок, дочерние кнопки)."""

    def __init__(self, windows):
        self.windows = windows
        self.clicked = []

    def GetClassName(self, h):
        return self.windows[h][0]

    def GetWindowText(self, h):
        return self.windows[h][1]

    def EnumChildWindows(self, h, cb, extra):
        for child in self.windows[h][2]:
            cb(child, extra)

    def SendMessage(self, h, msg, w, l):
        self.clicked.append(h)


def _app(bare_r7, gui, order):
    def find(*subs, exclude=(), owner_pids=None):
        for h in order:
            if h not in exclude:
                return h
        return None
    bare_r7._find_window_hwnd = find
    bare_r7._r7_window_owner_pids = lambda: {7}
    bare_r7._paced_total = 0.0
    return bare_r7


def test_editor_window_with_ods_title_is_skipped_and_real_dialog_clicked(bare_r7, monkeypatch):
    # После экспорта в ODS заголовок окна документа — «temp_export….ods — Р7-Офис»;
    # поиск не должен на нём защёлкнуться, иначе настоящий диалог не нажат.
    gui = _Gui({
        5: ("Qt5152QWindowIcon", "temp_export_x2t_1.ods - Р7-Офис. Профессиональный", []),
        9: ("#32770", "Р7-Офис", [91]),
        91: ("Button", "OK", []),
    })
    monkeypatch.setattr(r7windows, "win32gui", gui)
    monkeypatch.setattr(r7windows, "win32con", types.SimpleNamespace(BM_CLICK=245))
    monkeypatch.setattr(export.env, "WIN32_OK", True)
    app = _app(bare_r7, gui, order=[5, 9])
    assert app._dismiss_saveas_format_warning(1, main_hwnd=2, timeout=0.2,
                                              log_cb=lambda m: None) is True
    assert gui.clicked == [91]


def test_only_editor_windows_means_no_dialog(bare_r7, monkeypatch):
    gui = _Gui({5: ("Qt5152QWindowIcon", "doc.ods - Р7-Офис", [])})
    monkeypatch.setattr(r7windows, "win32gui", gui)
    monkeypatch.setattr(export.env, "WIN32_OK", True)
    logs = []
    app = _app(bare_r7, gui, order=[5])
    assert app._dismiss_saveas_format_warning(1, main_hwnd=2, timeout=0.1,
                                              log_cb=logs.append) is False
    assert gui.clicked == [] and not any("кнопка OK — нет" in m for m in logs)


def test_no_dialog_wait_ends_when_export_file_appears(bare_r7, monkeypatch):
    """Предупреждение бывает только до конвертации: файл экспорта появился —
    ждать его полные 3 с не нужно (XLTX, PDF, DOCX теряли так по 3 с)."""
    import time as _time
    gui = _Gui({5: ("Qt5152QWindowIcon", "doc - Р7-Офис", [])})
    monkeypatch.setattr(r7windows, "win32gui", gui)
    monkeypatch.setattr(export.env, "WIN32_OK", True)
    app = _app(bare_r7, gui, order=[5])
    t0 = _time.perf_counter()
    assert app._dismiss_saveas_format_warning(1, main_hwnd=2, timeout=3.0, log_cb=lambda m: None,
                                              stop_when=lambda: True) is False
    assert _time.perf_counter() - t0 < 0.5


class _NameEdit:
    def __init__(self, value):
        self.value, self.set_calls = value, []

    def get_value(self):
        return self.value

    def set_edit_text(self, text):
        self.set_calls.append(text)


def test_saveas_name_with_uia_spacing_is_accepted():
    # UIA диалога текстового редактора отдаёт путь с лишними пробелами между
    # символами (живой прогон 07.10.2026): это тот же путь, вписывать заново не нужно.
    edit = _NameEdit(r"  E : \T e mp \t e mp _x .p d f   ")
    assert export.ExportMixin._saveas_name_is(edit, r"E:\Temp\temp_x.pdf", lambda m: None)
    assert edit.set_calls == []


def test_saveas_name_other_path_is_rewritten_and_checked():
    edit = _NameEdit("Документ1")
    logs = []
    assert not export.ExportMixin._saveas_name_is(edit, r"E:\Temp\temp_x.pdf", logs.append)
    assert edit.set_calls == [r"E:\Temp\temp_x.pdf"]
