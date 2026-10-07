"""Окна, вынесенные из длинных методов (plan-to-10, шаг 2): хеши
дистрибутивов, сравнение версий, генератор файла — на настоящем (скрытом) Tk.
Действия зовутся методами окна, диалоги ОС и messagebox подменены."""
import json
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import r7.ui.compare_dialog as cd
import r7.ui.fixture_dialog as fd
import r7.ui.hash_window as hw
from conftest import patch_ui_name  # noqa: E402
from r7 import config as r7config

R = r7mod.R7Testovarka
MD5 = "a" * 32
SHA = "b" * 64


@pytest.fixture
def app(monkeypatch, tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    monkeypatch.setattr(R, "_load_test_selection", lambda self: {})
    monkeypatch.setattr(R, "_save_test_selection", lambda self: None)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    monkeypatch.setattr(R, "refresh_distributives", lambda self: None)
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    mb = Mock()
    mb.askyesno.return_value = True
    patch_ui_name(monkeypatch, "messagebox", mb)
    inst = R(root)
    for name in ("distributives_folder", "reports_folder", "test_files_folder"):
        folder = tmp_path / name
        folder.mkdir()
        setattr(inst, name, folder)
    inst.logs = []
    inst.add_test_log = inst.logs.append
    inst.mb, inst.root_ = mb, root
    root.update()
    yield inst
    root.destroy()


# ── Хеши дистрибутивов ───────────────────────────────────────────────────

def _results():
    return [{"name": "R7-1.msi", "size": "1.0", "md5": MD5, "sha256": SHA,
             "status": "нет эталона", "tag": "no_ref"},
            {"name": "R7-2.msi", "size": "—", "md5": "ОШИБКА", "sha256": "ОШИБКА",
             "status": "ошибка", "tag": "fail"}]


def _select(w, idx):
    iid = w.tree.get_children()[idx]
    w.tree.selection_set(iid)
    return iid


def test_hash_window_lists_rows_with_tags(app):
    w = app._show_hash_results(_results())
    rows = [w.tree.item(i) for i in w.tree.get_children()]
    assert [r["values"][0] for r in rows] == ["R7-1.msi", "R7-2.msi"]
    assert rows[0]["tags"] == ["no_ref"] and w.win.title() == "Хеш-суммы дистрибутивов"


def test_hash_save_reference_updates_status_and_file(app):
    res = _results()
    w = app._show_hash_results(res)
    iid = _select(w, 0)
    dlg = tk.Toplevel(w.win)
    w._save_reference(dlg, iid, res[0], MD5.upper(), SHA)
    ref = json.loads((app.distributives_folder / "hashes.json").read_text(encoding="utf-8"))
    assert ref["R7-1.msi"]["md5"] == MD5                      # регистр приведён
    assert res[0]["tag"] == "ok" and w.tree.item(iid)["tags"] == ["ok"]
    assert any("Добавлен эталон" in m for m in app.logs)


def test_hash_save_reference_rejects_bad_hex(app):
    res = _results()
    w = app._show_hash_results(res)
    dlg = tk.Toplevel(w.win)
    w._save_reference(dlg, _select(w, 0), res[0], "zz", SHA)
    assert app.mb.showerror.call_args.args[0] == "Ошибка ввода"
    assert not (app.distributives_folder / "hashes.json").exists()


def test_hash_edit_dialog_refused_for_unreadable_file(app):
    res = _results()
    w = app._show_hash_results(res)
    w.open_edit_dialog(_select(w, 1), res[1])
    assert app.mb.showwarning.call_args.args[0] == "Недоступно"


def test_hash_edit_dialog_refused_when_reference_file_broken(app):
    (app.distributives_folder / "hashes.json").write_text("{битый", encoding="utf-8")
    res = _results()
    w = app._show_hash_results(res)
    w.open_edit_dialog(_select(w, 0), res[0])
    assert app.mb.showerror.call_args.args[0] == "Эталоны недоступны"


def test_hash_edit_dialog_opens_with_current_values(app):
    res = _results()
    w = app._show_hash_results(res)
    _select(w, 0)
    w.edit_selected()
    tops = [c for c in w.win.winfo_children() if isinstance(c, tk.Toplevel)]
    assert tops and "R7-1.msi" in tops[0].title()


def test_hash_delete_reference(app):
    res = _results()
    w = app._show_hash_results(res)
    iid = _select(w, 0)
    w._save_reference(tk.Toplevel(w.win), iid, res[0], MD5, SHA)
    w.delete_selected()
    ref = json.loads((app.distributives_folder / "hashes.json").read_text(encoding="utf-8"))
    assert "R7-1.msi" not in ref and res[0]["tag"] == "no_ref"
    assert any("Удалён эталон" in m for m in app.logs)


def test_hash_delete_without_reference_informs(app):
    res = _results()
    w = app._show_hash_results(res)
    _select(w, 0)
    w.delete_selected()
    assert app.mb.showinfo.call_args.args[0] == "Нет эталона"


def test_hash_actions_need_selection(app):
    w = app._show_hash_results(_results())
    w.edit_selected()
    w.delete_selected()
    w.copy_selected(2, "MD5")
    assert app.mb.showwarning.call_count == 3


def test_hash_copy_md5_and_skip_error_values(app):
    w = app._show_hash_results(_results())
    _select(w, 0)
    w.copy_selected(2, "MD5")
    assert w.win.clipboard_get() == MD5
    _select(w, 1)
    app.mb.showinfo.reset_mock()
    w.copy_selected(2, "MD5")
    assert not app.mb.showinfo.called                     # «ОШИБКА» не копируется


def test_hash_double_click_copies_only_hash_columns(app, monkeypatch):
    w = app._show_hash_results(_results())
    iid = w.tree.get_children()[0]
    monkeypatch.setattr(w.tree, "identify_row", lambda y: iid)
    monkeypatch.setattr(w.tree, "identify_column", lambda x: "#4")
    w.on_double_click(SimpleNamespace(x=1, y=1))
    assert w.win.clipboard_get() == SHA
    monkeypatch.setattr(w.tree, "identify_column", lambda x: "#1")
    app.mb.showinfo.reset_mock()
    w.on_double_click(SimpleNamespace(x=1, y=1))
    assert not app.mb.showinfo.called


def test_hash_csv_export(app, monkeypatch, tmp_path):
    out = tmp_path / "h.csv"
    monkeypatch.setattr(hw.filedialog, "asksaveasfilename", lambda **k: str(out))
    w = app._show_hash_results(_results())
    w.save_csv()
    assert out.exists() and "R7-1.msi" in out.read_text(encoding="utf-8-sig")
    monkeypatch.setattr(hw.filedialog, "asksaveasfilename", lambda **k: "")
    w.save_csv()                                          # отмена — ничего не пишется


# ── Сравнение версий ─────────────────────────────────────────────────────

def _meta(folder, n):
    p = folder / f"performance_full_{n}.json"
    p.write_text(json.dumps({"version": f"v{n}", "timestamp": f"2026100{n}_000000",
                             "results": []}), encoding="utf-8")
    return {"key": str(p), "path": p, "version": f"v{n}", "ts": f"0{n}.10.2026"}


def test_compare_dialog_rename_delete_and_base(app, monkeypatch):
    metas = [_meta(app.reports_folder, i) for i in (1, 2, 3)]
    d = cd.CompareDialog(app, metas, {"last_base_version": metas[1]["key"]})
    assert d._current_base_key() == metas[1]["key"]       # база с прошлого раза
    monkeypatch.setattr(cd.simpledialog, "askstring", lambda *a, **k: "  Новая  ")
    label = SimpleNamespace(config=lambda **k: None)
    d.rename(metas[0], label)
    assert metas[0]["display_name"] == "Новая" and d.custom_names[metas[0]["key"]] == "Новая"
    assert d.base_combo["values"][0].startswith("Новая")
    d.set_base(metas[2]["key"])
    assert d._current_base_key() == metas[2]["key"]
    d.delete(metas[2], tk.Frame(d.inner))
    assert metas[2]["key"] not in d.file_meta_by_key
    assert d._current_base_key() == metas[1]["key"]       # удалённая база → прошлая
    d.close()


def test_compare_dialog_add_file_and_limits(app, monkeypatch):
    metas = [_meta(app.reports_folder, i) for i in (1, 2)]
    d = cd.CompareDialog(app, metas, {})
    extra = _meta(app.reports_folder, 3)
    monkeypatch.setattr(cd.filedialog, "askopenfilename", lambda **k: str(extra["path"]))
    d.add_file()
    assert str(extra["path"]) in d.file_meta_by_key
    d.add_file()                                          # повтор — «Уже добавлен»
    assert app.mb.showinfo.call_args.args[0] == "Уже добавлен"
    bad = app.reports_folder / "bad.json"
    bad.write_text("{", encoding="utf-8")
    monkeypatch.setattr(cd.filedialog, "askopenfilename", lambda **k: str(bad))
    d.add_file()
    assert app.mb.showerror.call_args.args[0] == "Ошибка"
    monkeypatch.setattr(cd, "MAX_FILES", 3)
    d.add_file()
    assert app.mb.showwarning.call_args.args[0] == "Лимит"
    d.close()


def test_compare_dialog_refresh_list_picks_new_reports(app):
    metas = [_meta(app.reports_folder, i) for i in (1, 2)]
    d = cd.CompareDialog(app, metas[:1], {})
    d.refresh_list()
    assert len(d.file_meta_by_key) == 2 and app.mb.showinfo.call_args.args[0] == "Обновлено"
    d.refresh_list()
    assert app.mb.showinfo.call_args.args[0] == "Нет изменений"
    d.close()


def test_compare_dialog_build_failure_closes_window(app, monkeypatch):
    def boom(self):
        raise RuntimeError("сломалось")
    monkeypatch.setattr(cd.CompareDialog, "_build_toolbar", boom)
    for i in (1, 2):
        _meta(app.reports_folder, i)
    app.compare_versions()
    assert not [w for w in app.root_.winfo_children() if isinstance(w, tk.Toplevel)]
    assert app.mb.showerror.called and any("сломалось" in m for m in app.logs)


# ── Генератор тестового файла ────────────────────────────────────────────

def test_fixture_dialog_auto_name_follows_dims_until_edited(app):
    d = fd.FixtureDialog(app)
    d.rows_var.set("2000")
    d.cols_var.set("7")
    assert d.filename_var.get() == "test_data_2000x7.xlsx"
    d.filename_var.set("мой.xlsx")
    d.rows_var.set("3000")
    assert d.filename_var.get() == "мой.xlsx"             # ручное имя не затирается
    d.dlg.destroy()


def test_fixture_dialog_choose_keeps_external_path(app, monkeypatch, tmp_path):
    ext = tmp_path / "внешний.xlsx"
    ext.write_bytes(b"x")
    monkeypatch.setattr(fd.filedialog, "askopenfilename", lambda **k: str(ext))
    d = fd.FixtureDialog(app)
    d.on_choose()
    assert d.ext_path == str(ext) and d._resolve_path() == ext
    d.on_create()                                         # создать можно только по имени
    assert app.mb.showwarning.call_args.args[0] == "Внимание"
    d.dlg.destroy()


def test_fixture_dialog_refuses_existing_without_overwrite(app):
    (app.test_files_folder / "f.xlsx").write_bytes(b"x")
    d = fd.FixtureDialog(app)
    d.rows_var.set("1000")
    d.cols_var.set("5")
    d.filename_var.set("f")
    d.overwrite_var.set(False)
    d.on_create()
    app.root_.update()
    assert "уже существует" in d.status_var.get()
    d.dlg.destroy()


def test_fixture_dialog_test_missing_file(app):
    d = fd.FixtureDialog(app)
    d.filename_var.set("нет-такого")
    d.on_test()
    app.root_.update()
    assert "Файл не найден" in d.status_var.get()
    d.filename_var.set("")
    d.on_test()
    app.root_.update()
    assert "Укажите имя" in d.status_var.get()
    d.dlg.destroy()


def test_fixture_dialog_status_after_close_is_silent(app):
    d = fd.FixtureDialog(app)
    d.dlg.destroy()
    d.set_status("поздно")                                # фоновый поток после закрытия
    d.lock()
    d.unlock()
