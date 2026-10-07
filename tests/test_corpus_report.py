"""Страницы корпуса (r7/corpus_report.py, corpus.html, corpus_compare.html),
команды `python -m r7 corpus` и `corpus-compare` (r7/cli.py), пересчёт в
коннекторе, вид прогона CORPUS и проверка экспорта в xlsx."""
import json
import zipfile

import pytest

import r7_webdriver_connector as wdc
from r7 import cli, corpus, corpus_report, firstrun, logfile, noise
from r7.cli import EXIT_GATE, EXIT_OK, EXIT_PRECONDITION, EXIT_RUN
from r7.corpus import Plan
from r7.run_state import BISECT, CORPUS, INSTALL, PERF, SCENARIO, RunState
from r7.stats import REGRESSION
from r7.x2t_files import X2tFilesMixin

FAST = [1.00, 1.01, 0.99, 1.02, 1.00, 0.98]
SLOW = [1.50, 1.52, 1.49, 1.51, 1.50, 1.53]


def _rec(name, runs, error=None, discard=False):
    import statistics
    valid = runs[1:] if discard else runs
    return {"name": name, "time": statistics.median(valid) if valid else 0.0, "error": error,
            "runs": list(runs), "run_statuses": ["ok"] * len(runs), "mad": 0.01,
            "first_run_discarded": discard, "n_runs": len(valid)}


def _file(fid, name, steps, error=None, plan=None):
    return {"id": fid, "name": name, "rel": name, "ext": name.rsplit(".", 1)[-1],
            "sha256": fid * 5, "size_bytes": 3 * 1024 * 1024,
            "plan": (plan or Plan()).to_dict(), "notes": None, "steps": steps, "error": error,
            "elapsed_sec": 30.0}


def _report(version, files, ts="20261007_120000", fp="fp1", hide=False):
    rep = corpus.build_report(files, Plan(), timestamp=ts, measure_schema=10,
                              tool_version="1.0", version=version, build=None,
                              system={"environment": {"fingerprint": {"cpu_model": "x"},
                                                      "fingerprint_hash": fp}},
                              corpus_dir=None)
    return corpus.hide_names(rep) if hide else rep


def _steps(open_runs, recalc_runs, pdf_runs):
    return {"open": _rec("Открытие файла", open_runs),
            "recalc": _rec(corpus.RECALC_OP_NAME, recalc_runs),
            "export:pdf": _rec("Сохранение в PDF (конвертация x2t)", pdf_runs)}


# ── матрица прогона ──────────────────────────────────────────────────────

def test_matrix_model_cells_and_errors():
    rep = _report("2026.3.2", [
        _file("aaa", "a.xlsx", _steps(FAST, FAST, FAST)),
        _file("bbb", "b.ods", {"open": _rec("Открытие файла", FAST),
                               "recalc": _rec(corpus.RECALC_OP_NAME, [], error="нет api")},
              error=None),
        _file("ccc", "c.xlsx", {}, error="окно Р7 не появилось"),
    ])
    m = corpus_report.matrix_model(rep)
    assert m["columns"] == ["Открытие", "Пересчёт", "Экспорт PDF"]
    a, b, c = m["rows"]
    assert a["cells"][0]["text"] == "1,000" and a["cells"][0]["tone"] == "neutral"
    assert "6/6" in a["cells"][0]["sub"] and not a["bad"]
    assert b["cells"][1] == {"text": "ошибка", "sub": None, "tone": "critical",
                             "title": "нет api"}
    assert b["cells"][2]["text"] == "—" and b["bad"]
    assert c["bad"] and c["error"] == "окно Р7 не появилось" and c["size"] == "3,0"
    tiles = {t["label"]: t for t in m["tiles"]}
    assert tiles["Файлов с ошибкой"]["value"] == "2"


def test_matrix_partial_error_is_warning():
    rec = _rec("Открытие файла", FAST, error="все открытия упёрлись в таймаут")
    cell = corpus_report.step_cell(rec)
    assert cell["text"] == "ошибка"          # comparable_time None при error
    rec2 = dict(_rec("x", FAST), n_timeouts=1)
    assert corpus_report.step_cell(rec2)["tone"] == "warning"


def test_matrix_columns_order_export_formats():
    plan = Plan(formats=("xlsx", "pdf"))
    files = [_file("a", "a.xlsx", {}, plan=plan)]
    assert corpus_report.column_keys(files) == ["open", "recalc", "export:pdf", "export:xlsx"]


def test_matrix_page_renders_and_escapes():
    rep = _report("2026.3.2", [_file("aaa", "<script>x</script>.xlsx",
                                     _steps(FAST, FAST, FAST))])
    html = corpus_report.matrix_page(corpus_report.matrix_model(rep))
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "Файл × шаг" in html and "Экспорт PDF" in html


def test_matrix_text_marks_bad_files():
    rep = _report("v", [_file("a", "a.xlsx", {}, error="сбой")])
    text = corpus_report.matrix_text(corpus_report.matrix_model(rep))
    assert "a.xlsx" in text and "<<" in text and "сбой" in text


def test_hidden_report_page_has_no_names():
    rep = _report("v", [_file("aaa111", "Клиент ООО.xlsx", _steps(FAST, FAST, FAST))],
                  hide=True)
    html = corpus_report.matrix_page(corpus_report.matrix_model(rep))
    assert "Клиент" not in html and "aaa111" in html


# ── сравнение «файл × версия» ────────────────────────────────────────────

def test_compare_detects_regression_with_bh():
    base = _report("2026.3.2", [_file("aaa", "a.xlsx", _steps(FAST, FAST, FAST)),
                                _file("bbb", "b.xlsx", _steps(FAST, FAST, FAST))])
    new = _report("2026.3.3", [_file("aaa", "a.xlsx", _steps(FAST, SLOW, FAST)),
                               _file("bbb", "b.xlsx", _steps(FAST, FAST, FAST))],
                  ts="20261008_120000")
    m = corpus_report.compare_model([base, new])
    recalc_a = next(r for r in m["rows"] if r["id"] == "aaa" and r["step"] == "Пересчёт")
    cell = recalc_a["cells"][0]
    assert cell["verdict"] == REGRESSION and cell["tone"] == "critical"
    assert cell["delta"].startswith("+50 %") and "6 ячеек" in cell["note"]
    assert corpus_report.has_regression(m)
    assert m["summaries"][0]["regressions"] == 1
    assert m["rows"][0]["file"] == "a.xlsx" and m["rows"][1]["file"] is None
    assert m["rows"][0]["rowspan"] == 3
    text = corpus_report.compare_text(m)
    assert "<< a.xlsx · Пересчёт: РЕГРЕССИЯ" in text


def test_compare_few_runs_gives_delta_without_verdict():
    base = _report("v1", [_file("a", "a.xlsx", _steps(FAST[:3], FAST[:3], FAST[:3]))])
    new = _report("v2", [_file("a", "a.xlsx", _steps(SLOW[:3], FAST[:3], FAST[:3]))])
    m = corpus_report.compare_model([base, new])
    cell = m["rows"][0]["cells"][0]
    assert cell["verdict"] == "мало повторов" and cell["delta"].startswith("+50")
    assert not corpus_report.has_regression(m)


def test_compare_errors_missing_and_matching_by_name():
    base = _report("v1", [_file("a", "a.xlsx", _steps(FAST, FAST, FAST)),
                          _file("b", "b.xlsx", _steps(FAST, FAST, FAST))])
    # «a» переобезличен (другой id, то же имя), у «b» упал экспорт, «z» — новый.
    new = _report("v2", [
        _file("a2", "a.xlsx", _steps(FAST, FAST, FAST)),
        _file("b", "b.xlsx", {"open": _rec("Открытие файла", FAST),
                              "export:pdf": _rec("x", [], error="x2t упал")}),
        _file("z", "z.xlsx", _steps(FAST, FAST, FAST))])
    m = corpus_report.compare_model([base, new])
    by = {(r["id"], r["step"]): r["cells"][0] for r in m["rows"]}
    assert by[("a", "Открытие")]["verdict"] in ("эквивалентно", "не определено")
    assert by[("b", "Экспорт PDF")]["verdict"] == "ошибка"
    assert "x2t упал" in by[("b", "Экспорт PDF")]["note"]
    assert by[("b", "Пересчёт")]["verdict"] == "нет данных"
    assert any("z.xlsx" in w for w in m["warnings"])


def test_compare_three_versions_and_fingerprint_warning():
    reps = [_report(f"v{i}", [_file("a", "a.xlsx", _steps(FAST, FAST, FAST))],
                    ts=f"2026100{i}_120000", fp=f"fp{i % 2}") for i in range(3)]
    m = corpus_report.compare_model(reps)
    assert [v["label"] for v in m["versions"]] == ["v0 (20261000_120000)",
                                                    "v1 (20261001_120000)",
                                                    "v2 (20261002_120000)"]
    assert len(m["rows"][0]["cells"]) == 2 and len(m["summaries"]) == 2
    assert any("разных машинах" in w for w in m["warnings"])


def test_compare_needs_two_reports():
    with pytest.raises(corpus.CorpusError):
        corpus_report.compare_model([_report("v", [])])


def test_compare_uses_noise_profile_threshold():
    base = _report("v1", [_file("a", "a.xlsx", _steps(FAST, FAST, FAST))])
    new = _report("v2", [_file("a", "a.xlsx", _steps([t * 1.06 for t in FAST], FAST, FAST))])
    loose = corpus_report.compare_model([base, new])
    tight = corpus_report.compare_model(
        [base, new], {"fingerprint_hash": "fp1", "tests": {"Открытие файла": {"cv_pct": 0.5}}})
    assert loose["rows"][0]["cells"][0]["verdict"] != REGRESSION      # 10 % по умолчанию
    assert tight["rows"][0]["cells"][0]["verdict"] == REGRESSION      # порог 2 %
    assert "профиля шума" in tight["noise_note"]


def test_compare_page_renders():
    base = _report("v1", [_file("a", "a.xlsx", _steps(FAST, FAST, FAST))])
    new = _report("v2", [_file("a", "a.xlsx", _steps(SLOW, FAST, FAST))])
    html = corpus_report.compare_page(corpus_report.compare_model([base, new]))
    assert "Файл × версия" in html and "РЕГРЕССИЯ" in html and "v2" in html


def test_noise_names_match_tab_operations():
    assert corpus_report.noise_name("open") == "Открытие файла"
    assert corpus_report.noise_name("export:csv") == "Сохранение в CSV (конвертация x2t)"
    assert corpus_report.noise_name("recalc") == corpus.RECALC_OP_NAME


# ── CLI ──────────────────────────────────────────────────────────────────

class CliApp:
    """Голое приложение для CLI: Р7 найден, не запущен, run_corpus — подмена."""

    def __init__(self, tmp_path, running=False, r7_path="E:/R7/editors.exe", report=None,
                 error=None):
        self.reports_folder = tmp_path / "Reports"
        self.reports_folder.mkdir(exist_ok=True)
        self.running, self.r7_path = running, r7_path
        self.report, self.error, self.calls = report, error, []
        self.current_version_info = {"version": "2026.3.2"}

    def _get_r7_processes(self, log_cb=None):
        from unittest.mock import Mock
        return [Mock(pid=4242)] if self.running else []

    def _find_r7_path(self):
        return self.r7_path

    def run_corpus(self, items, plan, corpus_dir=None, warnings=(), log_cb=None,
                   stop_event=None):
        self.calls.append((items, plan, corpus_dir))
        if self.error:
            raise self.error
        return self.report


@pytest.fixture(autouse=True)
def _quiet_logging(monkeypatch, tmp_path):
    monkeypatch.setattr(logfile, "setup_logging", lambda *a, **k: None)


def _install(monkeypatch, app):
    monkeypatch.setattr(cli, "make_headless_app", lambda log_cb=None, reports_folder=None: app)


def _corpus_dir(tmp_path, names=("a.xlsx",)):
    d = tmp_path / "Corpus"
    d.mkdir(exist_ok=True)
    for n in names:
        (d / n).write_bytes(n.encode())
    return d


def test_parser_corpus_defaults():
    a = cli.build_parser().parse_args(["corpus"])
    assert (a.steps, a.formats, a.open_runs, a.recalc_runs, a.export_runs) == \
        ("open,recalc,export", "pdf", 5, 6, 6)
    assert not a.hide_names and a.dir is None
    b = cli.build_parser().parse_args(["corpus-compare", "A.json", "B.json", "--hide-names"])
    assert b.reports == ["A.json", "B.json"] and b.hide_names


def test_corpus_missing_dir_is_precondition(tmp_path, capsys):
    assert cli.main(["corpus", "--dir", str(tmp_path / "нет")]) == EXIT_PRECONDITION
    assert "Папки корпуса нет" in capsys.readouterr().out


def test_corpus_bad_steps_and_empty_corpus(tmp_path, capsys):
    d = _corpus_dir(tmp_path)
    assert cli.main(["corpus", "--dir", str(d), "--steps", "open,save"]) == EXIT_PRECONDITION
    assert cli.main(["corpus", "--dir", str(d), "--formats", "docx"]) == EXIT_PRECONDITION
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["corpus", "--dir", str(empty)]) == EXIT_PRECONDITION
    out = capsys.readouterr().out
    assert "save" in out and "docx" in out and "нет файлов" in out


def test_corpus_refuses_when_r7_running(monkeypatch, tmp_path, capsys):
    app = CliApp(tmp_path, running=True)
    _install(monkeypatch, app)
    assert cli.main(["corpus", "--dir", str(_corpus_dir(tmp_path))]) == EXIT_PRECONDITION
    assert "4242" in capsys.readouterr().out and not app.calls


def test_corpus_ok_writes_json_and_html(monkeypatch, tmp_path, capsys):
    rep = _report("2026.3.2", [_file("aaa", "a.xlsx", _steps(FAST, FAST, FAST))])
    app = CliApp(tmp_path, report=rep)
    _install(monkeypatch, app)
    d = _corpus_dir(tmp_path)
    code = cli.main(["corpus", "--dir", str(d), "--steps", "open,export",
                     "--formats", "pdf,csv", "--open-runs", "5"])
    assert code == EXIT_OK
    items, plan, folder = app.calls[0]
    assert plan.steps == ("open", "export") and plan.formats == ("pdf", "csv")
    assert plan.open_runs == 5 and folder == d and [i.name for i in items] == ["a.xlsx"]
    js = app.reports_folder / "corpus_20261007_120000.json"
    assert js.is_file() and (app.reports_folder / "corpus_20261007_120000.html").is_file()
    assert json.loads(js.read_text(encoding="utf-8"))["files"][0]["name"] == "a.xlsx"
    assert "a.xlsx" in capsys.readouterr().out


def test_corpus_hide_names_writes_only_hidden(monkeypatch, tmp_path):
    rep = _report("v", [_file("aaa111", "Секрет клиента.xlsx", _steps(FAST, FAST, FAST))])
    app = CliApp(tmp_path, report=rep)
    _install(monkeypatch, app)
    assert cli.main(["corpus", "--dir", str(_corpus_dir(tmp_path)), "--hide-names"]) == EXIT_OK
    for p in app.reports_folder.iterdir():
        assert "Секрет" not in p.read_text(encoding="utf-8")


def test_corpus_file_errors_exit_1_and_stop_exit_2(monkeypatch, tmp_path):
    bad = _report("v", [_file("a", "a.xlsx", {}, error="сбой")])
    _install(monkeypatch, CliApp(tmp_path, report=bad))
    assert cli.main(["corpus", "--dir", str(_corpus_dir(tmp_path))]) == EXIT_GATE
    stopped = dict(_report("v", [_file("a", "a.xlsx", _steps(FAST, FAST, FAST))]), stopped=True)
    _install(monkeypatch, CliApp(tmp_path, report=stopped))
    assert cli.main(["corpus", "--dir", str(_corpus_dir(tmp_path))]) == EXIT_RUN


def test_corpus_worker_errors(monkeypatch, tmp_path, capsys):
    from r7.corpus_runner import CorpusRunError
    _install(monkeypatch, CliApp(tmp_path, error=CorpusRunError("Идёт бисект")))
    assert cli.main(["corpus", "--dir", str(_corpus_dir(tmp_path))]) == EXIT_PRECONDITION
    _install(monkeypatch, CliApp(tmp_path, error=RuntimeError("упало")))
    assert cli.main(["corpus", "--dir", str(_corpus_dir(tmp_path))]) == EXIT_RUN
    out = capsys.readouterr().out
    assert "Идёт бисект" in out and "Корпус упал" in out


def _write_report(path, rep):
    path.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_corpus_compare_exit_codes_and_page(tmp_path, capsys):
    base = _write_report(tmp_path / "corpus_a.json",
                         _report("v1", [_file("a", "Клиент.xlsx", _steps(FAST, FAST, FAST))]))
    same = _write_report(tmp_path / "corpus_b.json",
                         _report("v2", [_file("a", "Клиент.xlsx", _steps(FAST, FAST, FAST))]))
    slow = _write_report(tmp_path / "corpus_c.json",
                         _report("v3", [_file("a", "Клиент.xlsx", _steps(SLOW, FAST, FAST))]))
    out_dir = tmp_path / "out"
    assert cli.main(["corpus-compare", base, same, "--out", str(out_dir)]) == EXIT_OK
    assert cli.main(["corpus-compare", base, slow, "--out", str(out_dir), "--hide-names"]) \
        == EXIT_GATE
    pages = sorted(out_dir.glob("corpus_compare_*.html"))
    assert pages
    assert "Клиент" not in pages[-1].read_text(encoding="utf-8")
    assert "РЕГРЕССИЯ" in capsys.readouterr().out


def test_corpus_compare_preconditions(tmp_path, capsys):
    assert cli.main(["corpus-compare", str(tmp_path / "a.json")]) == EXIT_PRECONDITION
    assert cli.main(["corpus-compare", str(tmp_path / "a.json"),
                     str(tmp_path / "b.json")]) == EXIT_PRECONDITION
    assert "не найден" in capsys.readouterr().out


def test_corpus_compare_reads_noise_profile_next_to_base(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(noise, "noise_for_report", lambda folder, rep: seen.append(folder))
    base = _write_report(tmp_path / "corpus_a.json", _report("v1", []))
    new = _write_report(tmp_path / "corpus_b.json", _report("v2", []))
    cli.main(["corpus-compare", base, new, "--out", str(tmp_path / "o")])
    assert seen == [tmp_path]


def test_r7_preconditions_skip_fixture(monkeypatch, tmp_path):
    app = CliApp(tmp_path)
    monkeypatch.setattr(firstrun, "check_fixture", lambda *a: pytest.fail("фикстура не нужна"))
    assert cli.r7_preconditions(app) == []


# ── пересчёт в коннекторе, RunState, экспорт xlsx ────────────────────────

def test_recalc_js_uses_calculate_all_and_marks_mutated():
    js = wdc._RECALC_JS
    assert "asc_calculate(all)" in js and "c_oAscCalculateType" in js
    assert "T.All" in js and ": 4;" in js
    assert js.index("st.mutated = true") < js.index("api.asc_calculate(all)")
    assert "no-method:asc_calculate" in js and "st.api_ms" in js


def test_connector_recalculate_evaluates_recalc_js():
    conn = wdc.R7WebDriverConnector.__new__(wdc.R7WebDriverConnector)
    sent = []
    conn.evaluate = lambda js, timeout=None: sent.append((js, timeout)) or {"ok": True}
    assert conn.recalculate(timeout=5) == {"ok": True}
    assert sent == [(wdc._RECALC_JS, 5)]


@pytest.mark.parametrize("other", [PERF, SCENARIO, INSTALL, BISECT])
def test_corpus_run_kind_excludes_everything(other):
    st = RunState()
    assert st.try_start(CORPUS) is None
    assert "корпуса" in st.try_start(other)[0]
    st.finish(CORPUS)
    assert st.try_start(other) is None
    assert st.try_start(CORPUS) is not None


def test_export_check_xlsx_vs_xltx(tmp_path):
    def book(name, ctype):
        p = tmp_path / name
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("[Content_Types].xml", f'<Types><Override ContentType="{ctype}"/></Types>')
        return p
    sheet = book("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    tmpl = book("b.xltx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml")
    assert X2tFilesMixin._check_export_format(sheet, "xlsx") == (True, "книга Excel")
    assert X2tFilesMixin._check_export_format(tmpl, "xlsx")[0] is False
    assert X2tFilesMixin._check_export_format(tmpl, "xltx") == (True, "шаблон Excel")
    assert X2tFilesMixin._check_export_format(sheet, "xltx")[0] is False


def test_cleanup_removes_temp_xlsx_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMP", str(tmp_path))
    (tmp_path / "temp_export_x2t_1.xlsx").write_bytes(b"x")
    keep = tmp_path / "book.xlsx"
    keep.write_bytes(b"x")

    class M(X2tFilesMixin):
        def _cleanup_x2t_crash_dumps(self, log_cb=None):
            pass
    M()._cleanup_x2t_temp_pdfs(log_cb=lambda *_: None)
    assert not (tmp_path / "temp_export_x2t_1.xlsx").exists() and keep.exists()
