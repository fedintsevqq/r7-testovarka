"""Тема интерфейса (sv-ttk), значки и цвета журнала — на настоящем (скрытом) Tk."""
import tkinter as tk
from tkinter import ttk
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod
import r7.ui.base as ub
import r7.ui.icons as icons
from conftest import patch_ui_name  # noqa: E402
from r7 import config as r7config

R = r7mod.R7Testovarka


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
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    patch_ui_name(monkeypatch, "messagebox", Mock())
    inst = R(root)
    root.update()
    yield inst
    ub.COLORS.update(ub.PALETTES[ub.DEFAULT_THEME])   # палитра — общая для модулей
    root.destroy()


def _walk(w):
    for c in w.winfo_children():
        yield c
        yield from _walk(c)


# ── значки ───────────────────────────────────────────────────────────────

@pytest.mark.skipif(not icons.available(), reason="нет Pillow или шрифта Segoe MDL2")
def test_icon_render_is_square_and_colored():
    img = icons.render("play", "#ff0000", 20)
    assert img.size == (20, 20)
    w, h = img.size
    opaque = [px for px in (img.getpixel((x, y)) for x in range(w) for y in range(h)) if px[3] > 200]
    assert opaque and all(px[0] > 200 and px[1] < 60 for px in opaque)


def test_icon_unknown_or_without_font_is_none(monkeypatch):
    assert icons.render("нет-такого", "#000", 16) is None
    monkeypatch.setattr(icons, "FONT_PATH", icons.Path("Z:/нет/шрифта.ttf"))
    assert icons.render("play", "#000", 16) is None


def test_every_icon_name_used_in_ui_exists():
    import re
    from pathlib import Path
    used = set()
    for f in Path(ub.__file__).parent.glob("*.py"):
        used |= set(re.findall(r'_icon_button\([^)]*?"[^"]*",\s*"(\w+)"', f.read_text(encoding="utf-8")))
    assert used and used <= set(icons.GLYPHS)


# ── тема ─────────────────────────────────────────────────────────────────

def test_style_colors_win_over_palette_option(app):
    """tk_setPalette (sv-ttk) не должен перебивать цвет стилей у надписей."""
    app.root.update()
    style = ttk.Style(app.root)
    groups = [w for w in _walk(app.root) if isinstance(w, ttk.Label)
              and str(w.cget("style")) == "Group.TLabel"]
    assert groups and all(str(g.cget("foreground")) == "" for g in groups)
    assert style.lookup("Group.TLabel", "foreground") == ub.COLORS["accent"]


def test_toggle_theme_recolors_and_persists(app):
    assert app._theme == "dark"
    app._toggle_theme()
    app.root.update()
    assert app._theme == "light" and ub.COLORS["bg"] == ub.PALETTES["light"]["bg"]
    assert str(app.test_log.cget("bg")) == ub.PALETTES["light"]["log_bg"]
    assert ub.load_ui_settings()["theme"] == "light"
    app._toggle_theme()
    app.root.update()
    assert str(app.test_log.cget("bg")) == ub.PALETTES["dark"]["log_bg"]


def test_saved_theme_applied_at_start(monkeypatch, tmp_path):
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    ub.save_ui_settings({"theme": "light"})
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    monkeypatch.setattr(R, "_load_test_selection", lambda self: {})
    monkeypatch.setattr(R, "_save_test_selection", lambda self: None)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    try:
        inst = R(root)
        assert inst._theme == "light"
    finally:
        ub.COLORS.update(ub.PALETTES[ub.DEFAULT_THEME])
        root.destroy()


def test_broken_ui_settings_fall_back_to_default(monkeypatch, tmp_path):
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    (tmp_path / ub.UI_SETTINGS_FILE).write_text("{битый", encoding="utf-8")
    assert ub.load_ui_settings() == {}


def test_disabled_icon_button_has_dim_image(app):
    btn = app.btn_install                          # недоступна до выбора дистрибутива
    spec = btn.cget("image")
    if not spec:
        pytest.skip("значков нет на этой системе")
    assert "disabled" in [str(x) for x in spec]


# ── журнал ───────────────────────────────────────────────────────────────

def test_log_lines_colored_by_meaning(app):
    for msg in ("❌ сбой", "⚠️ внимание", "✅ файл открыт", "📊 медиана", "🔌 WebDriver"):
        app.add_test_log(msg)
    tags = []
    for line in range(1, 6):
        idx = f"{line}.12"                          # после «ЧЧ:ММ:СС  »
        tags.append([t for t in app.test_log.tag_names(idx) if t != "sel"][0])
    assert tags == ["ERROR", "WARN", "OK", "RESULT", "INFO"]
    assert "TIME" in app.test_log.tag_names("1.0")


def test_without_sv_ttk_falls_back_to_clam(monkeypatch, tmp_path):
    """Нет пакета sv-ttk — окно открывается в прежней тёмной теме на clam."""
    monkeypatch.setattr(ub, "SV_TTK_OK", False)
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(R, "_load_test_selection", lambda self: {})
    monkeypatch.setattr(R, "_save_test_selection", lambda self: None)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    try:
        R(root)
        assert ttk.Style(root).theme_use() == "clam"
    finally:
        ub.COLORS.update(ub.PALETTES[ub.DEFAULT_THEME])
        root.destroy()


def test_unreadable_sv_theme_files_fall_back_to_clam(monkeypatch, tmp_path):
    """Раннер CI: Tcl не прочитал sv.tcl — окно открывается, тема запасная."""
    def broken(theme, root=None):
        raise tk.TclError('couldn\'t read file "sv.tcl": no such file or directory')
    monkeypatch.setattr(ub.sv_ttk, "set_theme", broken)
    monkeypatch.setattr(r7config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(R, "_load_test_selection", lambda self: {})
    monkeypatch.setattr(R, "_save_test_selection", lambda self: None)
    monkeypatch.setattr(R, "detect_current_version", lambda self: None)
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    try:
        R(root)
        assert ttk.Style(root).theme_use() == "clam"
    finally:
        ub.COLORS.update(ub.PALETTES[ub.DEFAULT_THEME])
        root.destroy()


# ── доступность: подсказки, клавиатура, индикатор ────────────────────────

def test_icon_only_buttons_have_tooltips(app):
    btn = app.btn_theme
    assert btn._r7_tooltip.text == "Светлая или тёмная тема"
    btn._r7_tooltip.show()
    tip = btn._r7_tooltip.tip
    assert tip is not None and tip.winfo_exists()
    btn._r7_tooltip.hide()
    assert btn._r7_tooltip.tip is None


def test_tooltip_shows_on_keyboard_focus(app):
    btn = app.btn_theme
    btn.event_generate("<FocusIn>")
    assert btn._r7_tooltip.tip is not None
    btn.event_generate("<FocusOut>")
    assert btn._r7_tooltip.tip is None


def test_icon_button_without_text_gets_tooltip_by_default(app):
    btn = app._icon_button(app.root, "", "edit")
    assert btn._r7_tooltip.text == "edit"
    plain = app._icon_button(app.root, "Обновить", "refresh")
    assert not hasattr(plain, "_r7_tooltip")


def test_test_list_checkbuttons_take_keyboard_focus(app):
    boxes = [w for w in _walk(app.root) if isinstance(w, ttk.Checkbutton)
             and str(w.cget("style")) == "Panel.TCheckbutton"]
    assert len(boxes) == len(app.test_vars)
    assert all(str(b.cget("takefocus")) != "0" for b in boxes)
    name = next(iter(app.test_vars))
    before = app.test_vars[name].get()
    boxes[0].invoke()                                  # то же делает пробел
    assert app.test_vars[name].get() != before


def test_busy_indicator_uses_accent_not_warning(app):
    app._set_busy_indicator(True)
    assert str(app.lbl_status_dot.cget("style")) == "StatusBusy.TLabel"
    style = ttk.Style(app.root)
    assert style.lookup("StatusBusy.TLabel", "foreground") == ub.COLORS["accent"]
    app._set_busy_indicator(False)
    assert str(app.lbl_status_dot.cget("style")) == "StatusOk.TLabel"
