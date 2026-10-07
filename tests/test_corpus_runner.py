"""Прогон корпуса (r7/corpus_runner.py) на фальшивом стенде: запуск Р7,
готовность, замер и закрытие подменены — ничего не запускается.

Проверяется поток: одна сессия Р7 на файл, Р7 открывает копию во временной
папке, ошибка файла не останавливает корпус, Р7 закрывается при любом
исходе, временные файлы экспорта и копия убираются, RunState занят."""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import r7_ops
from r7 import corpus
from r7 import corpus_runner as cr
from r7.corpus import Plan
from r7.run_state import CORPUS, PERF, RunStateMixin


class FakeOps:
    """r7_ops.SpreadsheetOps без Р7: экспорт только записывается."""
    instances: list = []

    def __init__(self, app, find_hwnd, log_cb, test_file):
        self.app, self.test_file = app, test_file
        FakeOps.instances.append(self)

    def save_as_format(self, ext):
        self.app.events.append(("export", self.test_file.name, ext))
        if ext in self.app.fail_export:
            raise RuntimeError(f"экспорт {ext} упал")


class Stand(cr.CorpusMixin, RunStateMixin):
    """Стенд без Р7. Каждое действие пишется в events."""
    CDP_LONG_OP_TIMEOUT_SEC = 180.0

    def __init__(self, tmp_path, *, r7_path="E:/R7/editors.exe", launch_fail=(),
                 not_ready=(), focus_fail=(), cdp_ok=True, close_ok=True, fail_export=(),
                 stop_after=None):
        self.tmp_path = tmp_path
        self.r7_path = r7_path
        self.launch_fail, self.not_ready, self.focus_fail = set(launch_fail), set(not_ready), \
            set(focus_fail)
        self.cdp_ok, self.close_ok, self.fail_export = cdp_ok, close_ok, set(fail_export)
        self.stop_after, self.stop_event = stop_after, threading.Event()
        self.events, self.log = [], []
        self.opened = []          # пути, которые «открыл» Р7
        self.current_version_info = {"version": "2026.3.2"}
        self.alive = False

    # журнал, план питания, окружение, метаданные
    def add_test_log(self, msg):
        self.log.append(msg)

    def _engage_power_plan(self, log_cb):
        self.events.append(("power", "on"))

    def _restore_power_plan(self, log_cb):
        self.events.append(("power", "off"))

    def _capture_environment(self, log_cb=None):
        return {"fingerprint_hash": "fp1"}

    def _find_r7_path(self):
        return self.r7_path

    def _build_metadata(self):
        return {"build_number": "2026.3.2.1"}

    def _build_system_info(self):
        return {"environment": getattr(self, "_run_environment", None)}

    # запуск и готовность
    def _extra_opens(self, r7_path, test_file, n, stop_event):
        self.events.append(("extra_opens", test_file.name, n))
        return [{"open_elapsed": 1.0, "status": "ok"} for _ in range(n - 1)]

    def _launch_r7(self, r7_path, test_file):
        self.opened.append(Path(test_file))
        assert Path(test_file).is_file()     # копия на месте, пока Р7 «открыт»
        if test_file.name in self.launch_fail:
            self.alive = True                # процессы остались, окна нет
            return None
        self.alive = True
        self.events.append(("launch", test_file.name))
        return (100.0, 101.0, 0.2)

    def _start_update_monitor(self, log_cb=None):
        return threading.Event()

    def _wait_until_r7_ready(self, find_hwnd, timeout=120, log_cb=None):
        self.events.append(("ready_timeout", timeout))
        return self._current().name not in self.not_ready

    def _current(self):
        return self.opened[-1]

    def _main_open_record(self, open_start, window_ts, setup, data_ready):
        return {"open_elapsed": 2.0, "status": "ok" if data_ready else "timeout"}

    def _x2t_since(self, start):
        return []

    def _open_result(self, opens, data_ready, sample0):
        return {"name": "Открытие файла", "time": 2.0, "runs": [o["open_elapsed"] for o in opens],
                "run_statuses": [o["status"] for o in opens], "error": None if data_ready
                else "документ не загрузился"}

    def _focus_r7_settled(self, test_file):
        return test_file.name not in self.focus_fail

    def _prepare_cdp_session(self):
        self.events.append(("cdp", self._current().name))

    def _find_r7_window(self, title):
        return 77 if self.alive else None

    # замер
    def _measure_op_repeated(self, name, func, runs, find_hwnd, log_cb, stop_event,
                             focus_cb=None, post_delay=None):
        self.events.append(("measure", name, runs))
        error = None
        try:
            func()
        except Exception as e:
            error = str(e)
        if self.stop_after and name == self.stop_after:
            self.stop_event.set()
        if error:
            return {"name": name, "time": 0.0, "error": error, "runs": [], "run_statuses": []}
        return {"name": name, "time": 1.5, "error": None, "runs": [1.5] * runs,
                "run_statuses": ["ok"] * runs, "mad": 0.0}

    def _cdp_sequence(self, label, steps, checker=None, log_cb=None):
        self.events.append(("cdp_seq", label, steps[0][0], steps[0][2]))
        if not self.cdp_ok:
            return False
        conn = SimpleNamespace(recalculate=lambda timeout=None: {"ok": True})
        return steps[0][1](conn, steps[0][2])["ok"]

    @staticmethod
    def _cdp_check_document_changed(before, after):
        return True, "ok"

    # закрытие
    def _restore_autosave(self, log_cb=None):
        self.events.append(("autosave",))

    def _close_r7_gracefully(self, hwnd, log_cb=None, timeout=10):
        self.events.append(("close", hwnd))
        if self.close_ok:
            self.alive = False
        return self.close_ok

    def _r7_gone(self, timeout=None):
        return not self.alive

    def _emergency_close_r7(self, find_hwnd, log_cb=None):
        self.events.append(("emergency",))
        self.alive = False
        return True

    def _close_webdriver_connector(self):
        self.events.append(("ws_close",))

    def _cleanup_x2t_temp_pdfs(self, log_cb=None):
        self.events.append(("cleanup",))


@pytest.fixture(autouse=True)
def fake_ops(monkeypatch):
    FakeOps.instances = []
    monkeypatch.setattr(r7_ops, "SpreadsheetOps", FakeOps)


def _corpus(tmp_path, names=("a.xlsx", "b.ods"), plan=None, manifest=""):
    folder = tmp_path / "Corpus"
    folder.mkdir()
    for i, n in enumerate(names):
        (folder / n).write_bytes(f"содержимое {i}".encode())
    if manifest:
        (folder / "corpus.toml").write_text(manifest, encoding="utf-8")
    items, warnings = corpus.build_items(folder, plan or Plan(), corpus.load_manifest(folder))
    return folder, items, warnings


def _run(stand, items, plan=None, folder=None):
    return stand.run_corpus(items, plan or Plan(), corpus_dir=folder, warnings=["w"],
                            log_cb=stand.add_test_log, stop_event=stand.stop_event)


def test_corpus_all_steps_one_session_per_file(tmp_path, monkeypatch):
    workdirs = []
    real_mkdtemp = cr.tempfile.mkdtemp

    def mkdtemp(prefix=None):
        d = real_mkdtemp(prefix=prefix, dir=tmp_path)
        workdirs.append(Path(d))
        return d
    monkeypatch.setattr(cr.tempfile, "mkdtemp", mkdtemp)
    folder, items, _w = _corpus(tmp_path)
    st = Stand(tmp_path)
    rep = _run(st, items, folder=folder)

    assert [f["name"] for f in rep["files"]] == ["a.xlsx", "b.ods"]
    for f in rep["files"]:
        assert set(f["steps"]) == {"open", "recalc", "export:pdf"} and f["error"] is None
        assert f["steps"]["open"]["runs"] == [1.0, 1.0, 2.0]    # 2 лишних + основное
    measures = [e for e in st.events if e[0] == "measure"]
    assert measures[0] == ("measure", corpus.RECALC_OP_NAME, 5)
    assert measures[1] == ("measure", "Сохранение в PDF (конвертация x2t)", 3)
    assert [e for e in st.events if e[0] == "launch"] == [("launch", "a.xlsx"), ("launch", "b.ods")]
    assert ("extra_opens", "a.xlsx", 3) in st.events
    assert sum(1 for e in st.events if e[0] == "close") == 2
    assert ("emergency",) not in st.events
    assert sum(1 for e in st.events if e[0] == "cleanup") == 2
    assert sum(1 for e in st.events if e[0] == "ws_close") == 2
    # Р7 открывал копии во временных папках, они удалены; исходники целы.
    assert all(folder not in p.parents for p in st.opened)
    assert workdirs and not any(d.exists() for d in workdirs)
    assert (folder / "a.xlsx").read_bytes() == "содержимое 0".encode()
    # пересчёт — через CDP с длинным таймаутом, экспорт — через SpreadsheetOps
    assert ("cdp_seq", "Полный пересчёт", "asc_calculate(All)", 180.0) in st.events
    assert ("export", "a.xlsx", "pdf") in st.events
    # метаданные и состояние
    assert rep["version"] == "2026.3.2" and rep["build"]["build_number"] == "2026.3.2.1"
    assert rep["system"]["environment"]["fingerprint_hash"] == "fp1"
    assert rep["corpus_dir"] == str(folder) and rep["warnings"] == ["w"] and not rep["stopped"]
    assert st.events[0] == ("power", "on") and st.events[-1] == ("power", "off")
    assert st.run_state.active is None


def test_launch_failure_isolated_and_r7_closed(tmp_path):
    folder, items, _w = _corpus(tmp_path)
    st = Stand(tmp_path, launch_fail={"a.xlsx"})
    rep = _run(st, items)
    a, b = rep["files"]
    assert "окно Р7 не появилось" in a["error"] and a["steps"] == {}
    assert b["error"] is None and "recalc" in b["steps"]
    assert ("emergency",) in st.events                  # процессы первого файла убиты


def test_exception_in_session_recorded_and_r7_closed(tmp_path, monkeypatch):
    folder, items, _w = _corpus(tmp_path)
    st = Stand(tmp_path)

    def boom():
        raise OSError("CDP сломался")
    monkeypatch.setattr(st, "_prepare_cdp_session", boom)
    rep = _run(st, items)
    assert all("OSError: CDP сломался" in f["error"] for f in rep["files"])
    assert sum(1 for e in st.events if e[0] == "emergency") == 2
    assert sum(1 for e in st.events if e[0] == "cleanup") == 2
    assert st.run_state.active is None


def test_not_ready_records_open_and_skips_other_steps(tmp_path):
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx",),
                                manifest='[files."a.xlsx"]\nopen_timeout_sec = 300\n')
    st = Stand(tmp_path, not_ready={"a.xlsx"})
    (f,) = _run(st, items)["files"]
    assert set(f["steps"]) == {"open"} and "не загрузился за 300 с" in f["error"]
    assert ("ready_timeout", 300.0) in st.events
    assert not [e for e in st.events if e[0] == "measure"]
    assert ("close", 77) in st.events                   # штатное закрытие не пропущено…
    assert ("emergency",) not in st.events              # …и сработало


def test_focus_failure_is_file_error(tmp_path):
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx",))
    st = Stand(tmp_path, focus_fail={"a.xlsx"})
    (f,) = _run(st, items)["files"]
    assert "окно Р7 недоступно" in f["error"] and set(f["steps"]) == {"open"}


def test_graceful_close_failure_falls_back_to_emergency(tmp_path):
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx",))
    st = Stand(tmp_path, close_ok=False)
    _run(st, items)
    assert ("emergency",) in st.events


def test_plan_without_open_still_launches_once(tmp_path):
    plan = Plan(steps=("export",), formats=("pdf", "xlsx"))
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx",), plan=plan)
    st = Stand(tmp_path, fail_export={"xlsx"})
    (f,) = _run(st, items, plan)["files"]
    assert set(f["steps"]) == {"export:pdf", "export:xlsx"}
    assert ("extra_opens", "a.xlsx", 1) in st.events
    assert f["steps"]["export:xlsx"]["error"] == "экспорт xlsx упал"
    assert not [e for e in st.events if e[0] == "cdp_seq"]


def test_recalc_without_cdp_fails_honestly_without_keys(tmp_path):
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx",))
    st = Stand(tmp_path, cdp_ok=False)
    (f,) = _run(st, items)["files"]
    assert "только через api редактора" in f["steps"]["recalc"]["error"]
    assert "export:pdf" in f["steps"]                    # экспорт всё равно идёт


def test_stop_between_files(tmp_path):
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx", "b.xlsx", "c.xlsx"))
    st = Stand(tmp_path, stop_after="Сохранение в PDF (конвертация x2t)")
    rep = _run(st, items)
    assert [f["name"] for f in rep["files"]] == ["a.xlsx"] and rep["stopped"]
    assert any("Корпус остановлен" in m for m in st.log)


def test_stop_inside_file_skips_remaining_exports(tmp_path):
    plan = Plan(formats=("pdf", "csv"))
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx",), plan=plan)
    st = Stand(tmp_path, stop_after=corpus.RECALC_OP_NAME)
    (f,) = _run(st, items, plan)["files"]
    assert set(f["steps"]) == {"open", "recalc"}
    assert ("close", 77) in st.events


def test_refuses_when_other_run_active(tmp_path):
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx",))
    st = Stand(tmp_path)
    st.run_state.try_start(PERF)
    with pytest.raises(cr.CorpusRunError, match="тест производительности"):
        _run(st, items)
    assert st.run_state.active == PERF and not st.opened


def test_r7_not_found_releases_run_state(tmp_path):
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx",))
    st = Stand(tmp_path, r7_path=None)
    with pytest.raises(cr.CorpusRunError, match="не найден"):
        _run(st, items)
    assert st.run_state.active is None


def test_run_state_held_during_run(tmp_path, monkeypatch):
    folder, items, _w = _corpus(tmp_path, names=("a.xlsx",))
    st = Stand(tmp_path)
    seen = []
    orig = st._launch_r7

    def launch(r7_path, test_file):
        seen.append(st.run_state.active)
        return orig(r7_path, test_file)
    monkeypatch.setattr(st, "_launch_r7", launch)
    _run(st, items)
    assert seen == [CORPUS]


def test_cdp_recalculate_calls_connector_recalculate(tmp_path):
    st = Stand(tmp_path)
    assert st._cdp_recalculate() is True
    assert st.events[-1] == ("cdp_seq", "Полный пересчёт", "asc_calculate(All)", 180.0)


def test_app_class_has_corpus_mixin():
    import r7_Testovarka
    assert issubclass(r7_Testovarka.R7Testovarka, cr.CorpusMixin)
