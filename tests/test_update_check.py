"""Версия инструмента (r7/version.py) и проверка обновлений
(r7/update_check.py): разбор тегов, сравнение, молчание при ошибках сети,
ссылка в шапке главного окна, tool_version в JSON и HTML-отчёте."""
import json
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import r7_reports
import r7.ui.main_window as mw
from r7 import update_check
from r7.version import __version__

# Настоящий сетевой вызов: conftest подменяет его на время каждого теста,
# а здесь он нужен сам — берётся при сборе модуля, до подмены.
_REAL_FETCH = update_check.fetch_latest_release


def _release(tag, **extra):
    data = {"tag_name": tag, "html_url": f"https://github.com/x/y/releases/tag/{tag}",
            "draft": False, "prerelease": False}
    data.update(extra)
    return lambda timeout=None: data


# ── версия ───────────────────────────────────────────────────────────────

def test_version_is_semver():
    assert update_check.parse_version(__version__) is not None


@pytest.mark.parametrize("tag, parsed", [
    ("v1.2.0", (1, 2, 0)), ("1.2.0", (1, 2, 0)), ("v10.0.3", (10, 0, 3)),
    ("v1.2.0-rc1", None), ("v1.2.0b1", None), ("v1.2", None), ("", None), (None, None),
    ("release-1.2.0", None), (" v1.2.0 ", (1, 2, 0)),
])
def test_parse_version(tag, parsed):
    assert update_check.parse_version(tag) == parsed


@pytest.mark.parametrize("latest, current, newer", [
    ("v1.2.0", "1.0.0", True), ("v1.0.1", "1.0.0", True), ("v2.0.0", "1.9.9", True),
    ("v1.0.0", "1.0.0", False), ("v0.9.9", "1.0.0", False), ("v1.0.0", "1.0.1", False),
    ("v1.10.0", "1.9.0", True),            # числами, не строками
    ("v1.2.0-rc1", "1.0.0", False),        # предварительная — не предлагается
    ("garbage", "1.0.0", False), ("v1.2.0", "garbage", False),
])
def test_is_newer(latest, current, newer):
    assert update_check.is_newer(latest, current) is newer


# ── check_for_update ─────────────────────────────────────────────────────

def test_newer_release_reported_with_url():
    info = update_check.check_for_update("1.0.0", fetch=_release("v1.2.0"))
    assert info == {"latest": "1.2.0", "url": "https://github.com/x/y/releases/tag/v1.2.0"}


@pytest.mark.parametrize("tag", ["v1.0.0", "v0.9.0", "v1.0.0-rc2", "nightly"])
def test_equal_older_or_odd_tag_gives_none(tag):
    assert update_check.check_for_update("1.0.0", fetch=_release(tag)) is None


@pytest.mark.parametrize("flag", ["prerelease", "draft"])
def test_prerelease_and_draft_releases_ignored(flag):
    fetch = _release("v9.0.0", **{flag: True})
    assert update_check.check_for_update("1.0.0", fetch=fetch) is None


@pytest.mark.parametrize("failure", [
    TimeoutError("timed out"), OSError("no network"), ValueError("not json"),
    RuntimeError("HTTP 403"),
])
def test_network_or_parse_errors_give_none_and_never_raise(failure):
    def fetch(timeout=None):
        raise failure
    assert update_check.check_for_update("1.0.0", fetch=fetch) is None


@pytest.mark.parametrize("payload", [None, "text", [], {"tag_name": None}, {}])
def test_unexpected_payload_gives_none(payload):
    assert update_check.check_for_update("1.0.0", fetch=lambda timeout=None: payload) is None


def test_default_fetch_is_offline_in_tests_and_passes_timeout():
    """conftest подменяет сетевой вызов ошибкой — проверка возвращает None;
    таймаут доходит до fetch."""
    assert update_check.check_for_update("0.0.1") is None
    seen = {}

    def fetch(timeout=None):
        seen["timeout"] = timeout
        return {"tag_name": "v0.0.2", "html_url": "u"}
    assert update_check.check_for_update("0.0.1", timeout=1.5, fetch=fetch)["latest"] == "0.0.2"
    assert seen["timeout"] == 1.5


def test_fetch_uses_requests_when_available(monkeypatch):
    resp = Mock()
    resp.json.return_value = {"tag_name": "v1.1.0"}
    fake_requests = SimpleNamespace(get=Mock(return_value=resp))
    monkeypatch.setattr(update_check, "requests", fake_requests)
    assert _REAL_FETCH(timeout=2.0) == {"tag_name": "v1.1.0"}
    args, kwargs = fake_requests.get.call_args
    assert args == (update_check.RELEASES_API_URL,) and kwargs["timeout"] == 2.0
    assert "User-Agent" in kwargs["headers"]            # GitHub API без него отвечает 403
    resp.raise_for_status.assert_called_once()


def test_fetch_falls_back_to_urllib_without_requests(monkeypatch):
    monkeypatch.setattr(update_check, "requests", None)
    body = json.dumps({"tag_name": "v1.1.0"}).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body
    calls = {}

    def urlopen(req, timeout=None):
        calls["url"], calls["timeout"] = req.full_url, timeout
        return _Resp()
    monkeypatch.setattr(update_check.urllib.request, "urlopen", urlopen)
    assert _REAL_FETCH(timeout=2.5) == {"tag_name": "v1.1.0"}
    assert calls == {"url": update_check.RELEASES_API_URL, "timeout": 2.5}


# ── шапка главного окна ──────────────────────────────────────────────────

class _Header:
    """Хватает MainWindowMixin: скрытая метка, _ui_call — сразу."""
    _start_update_check = mw.MainWindowMixin._start_update_check
    _show_update_link = mw.MainWindowMixin._show_update_link

    def __init__(self, root):
        self.root = root
        self.lbl_update = ttk.Label(root, text="", cursor="hand2")

    def _ui_call(self, fn):
        fn()


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"Tk недоступен: {e}")
    root.withdraw()
    yield root
    root.destroy()


def _sync_thread(monkeypatch):
    class _Thread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            self._target()
    monkeypatch.setattr(mw.threading, "Thread", _Thread)


def test_header_link_appears_only_when_update_exists(tk_root, monkeypatch):
    _sync_thread(monkeypatch)
    opened = []
    monkeypatch.setattr(mw.webbrowser, "open", opened.append)
    h = _Header(tk_root)

    monkeypatch.setattr(mw.update_check, "check_for_update", lambda: None)
    h._start_update_check()
    assert not h.lbl_update.winfo_manager()           # нет обновления — метка скрыта

    monkeypatch.setattr(mw.update_check, "check_for_update",
                        lambda: {"latest": "1.2.0", "url": "https://example/rel"})
    h._start_update_check()
    assert h.lbl_update.winfo_manager() == "pack"
    assert h.lbl_update.cget("text") == "Доступна версия 1.2.0"
    h.lbl_update.event_generate("<Button-1>")
    tk_root.update()
    assert opened == ["https://example/rel"]


def test_header_link_survives_closed_window(tk_root, monkeypatch):
    h = _Header(tk_root)
    h.lbl_update.destroy()
    h._show_update_link({"latest": "1.2.0", "url": "u"})   # TclError проглочен


# ── версия инструмента в отчётах ─────────────────────────────────────────

def test_tool_version_in_full_json_report(bare_r7):
    bare_r7._applied_r7_window_size = None
    bare_r7._run_environment = None
    bare_r7._cached_cpu_count = 4
    rep = bare_r7._build_full_report("20261007_120000", "2026.3.2", "f.xlsx", [], {})
    assert rep["tool_version"] == __version__
    assert json.loads(json.dumps(rep))["tool_version"] == __version__


def _op(name, t):
    return {"name": name, "time": t, "runs": [t], "run_statuses": ["ok"]}


def test_tool_version_in_html_stand_block(tmp_path):
    model = r7_reports.run_report_model([_op("Открытие файла", 9.9)], tmp_path / "a.xlsx",
                                        9.9, "v", tool_version="1.0.0")
    assert ("Версия инструмента", "1.0.0") in model["meta"]
    html = r7_reports.render("run.html", **model)
    assert "Версия инструмента" in html and "1.0.0" in html
    old = r7_reports.run_report_model([_op("Открытие файла", 9.9)], tmp_path / "a.xlsx", 9.9, "v")
    assert ("Версия инструмента", "—") in old["meta"]   # отчёт без версии не падает
