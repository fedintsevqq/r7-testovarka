"""Корпус файлов: поиск, манифест, план шагов, отчёт и обезличивание
(r7/corpus.py). Без Р7 и без Tk."""
import json

import pytest

from r7 import config, corpus
from r7.corpus import CorpusError, Plan


def _write(path, data=b"PK\x03\x04 data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ── поиск файлов ─────────────────────────────────────────────────────────

def test_discover_supported_skips_locks_hidden_and_other(tmp_path):
    _write(tmp_path / "b.xlsx")
    _write(tmp_path / "A.XLS", b"xls")
    _write(tmp_path / "sub" / "c.ods", b"ods")
    _write(tmp_path / "d.csv", b"1;2")
    _write(tmp_path / "~$b.xlsx", b"lock")
    _write(tmp_path / ".~lock.c.ods#", b"lock")
    _write(tmp_path / "notes.txt", b"x")
    _write(tmp_path / ".hidden" / "e.xlsx", b"e")
    _write(tmp_path / "README.md", b"#")
    names = [p.relative_to(tmp_path).as_posix() for p in corpus.discover(tmp_path)]
    assert names == ["A.XLS", "b.xlsx", "d.csv", "sub/c.ods"]


def test_discover_missing_folder_is_empty(tmp_path):
    assert corpus.discover(tmp_path / "нет") == []


@pytest.mark.parametrize("name,lock", [("~$книга.xlsx", True), (".~lock.книга.xlsx#", True),
                                       ("книга.xlsx", False), (".~lock.x", False)])
def test_is_lock_file(name, lock):
    assert corpus.is_lock_file(name) is lock


def test_corpus_dir_follows_base_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    assert corpus.corpus_dir() == tmp_path / "Corpus"


# ── шаги и форматы ───────────────────────────────────────────────────────

def test_parse_steps_order_and_errors():
    assert corpus.parse_steps("export, OPEN") == ("open", "export")
    assert corpus.parse_steps(["recalc"]) == ("recalc",)
    with pytest.raises(CorpusError, match="неизвестные шаги: save"):
        corpus.parse_steps("open,save")
    with pytest.raises(CorpusError, match="ни один шаг"):
        corpus.parse_steps(" , ")


def test_parse_formats_dedup_and_errors():
    assert corpus.parse_formats("pdf,XLSX,pdf") == ("pdf", "xlsx")
    with pytest.raises(CorpusError, match="docx"):
        corpus.parse_formats("pdf,docx")


def test_parse_runs_clamped_and_typed():
    assert corpus.parse_runs(0, "x") == 1
    assert corpus.parse_runs(99, "x") == 20
    with pytest.raises(CorpusError):
        corpus.parse_runs("3", "x")
    with pytest.raises(CorpusError):
        corpus.parse_runs(True, "x")


def test_step_keys_and_titles():
    plan = Plan(steps=("open", "export"), formats=("pdf", "csv"))
    assert corpus.step_keys(plan) == ["open", "export:pdf", "export:csv"]
    assert corpus.step_keys(Plan()) == ["open", "recalc", "export:pdf"]
    assert corpus.step_title("export:pdf") == "Экспорт PDF"
    assert corpus.step_title("recalc") == "Пересчёт"


def test_export_op_name_matches_tab_tests():
    # Имя как у тестов вкладки: «Сохранение в» — признак операции без правки.
    assert corpus.export_op_name("pdf") == "Сохранение в PDF (конвертация x2t)"


def test_plan_defaults_match_task():
    p = Plan()
    assert (p.open_runs, p.recalc_runs, p.export_runs) == (5, 6, 6)
    assert p.to_dict()["steps"] == ["open", "recalc", "export"]


# ── манифест ─────────────────────────────────────────────────────────────

def test_manifest_absent_is_empty(tmp_path):
    m = corpus.load_manifest(tmp_path)
    assert m.defaults == {} and m.files == {} and m.errors == []


def test_manifest_bad_toml_tolerated_with_error(tmp_path):
    (tmp_path / "corpus.toml").write_text("[defaults\nsteps = ", encoding="utf-8")
    _write(tmp_path / "a.xlsx")
    m = corpus.load_manifest(tmp_path)
    assert m.files == {} and "ошибка в TOML" in m.errors[0]
    items, warnings = corpus.build_items(tmp_path, Plan(), m)
    assert [i.name for i in items] == ["a.xlsx"] and items[0].plan == Plan()
    assert any("ошибка в TOML" in w for w in warnings)


def test_manifest_structure_errors():
    m = corpus.parse_manifest({"defaults": 1, "files": {"a.xlsx": 5}, "extra": {}})
    text = " ".join(m.errors)
    assert "[extra]" in text and "[defaults]" in text and "a.xlsx" in text


def test_build_items_applies_defaults_and_file_overrides(tmp_path):
    _write(tmp_path / "a.xlsx", b"a")
    _write(tmp_path / "sub" / "b.ods", b"b")
    _write(tmp_path / "c.csv", b"c")
    (tmp_path / "corpus.toml").write_text(
        '[defaults]\nformats = ["pdf", "csv"]\nopen_runs = 2\n'
        '[files."sub/b.ods"]\nsteps = ["open"]\nopen_timeout_sec = 300\nnotes = "тяжёлый"\n'
        '[files."c.csv"]\nskip = true\n'
        '[files."нет.xlsx"]\nsteps = ["open"]\n', encoding="utf-8")
    items, warnings = corpus.build_items(tmp_path, Plan(), corpus.load_manifest(tmp_path))
    by = {i.rel: i for i in items}
    assert set(by) == {"a.xlsx", "sub/b.ods"}
    assert by["a.xlsx"].plan.formats == ("pdf", "csv") and by["a.xlsx"].plan.open_runs == 2
    b = by["sub/b.ods"]
    assert b.plan.steps == ("open",) and b.plan.open_timeout_sec == 300 and b.notes == "тяжёлый"
    assert b.step_keys() == ["open"] and b.ext == "ods"
    assert any("c.csv: пропущен (skip" in w for w in warnings)
    assert any("«нет.xlsx» в корпусе нет" in w for w in warnings)


def test_build_items_bad_field_keeps_file_with_warning(tmp_path):
    _write(tmp_path / "a.xlsx")
    (tmp_path / "corpus.toml").write_text(
        '[files."a.xlsx"]\nformats = ["docx"]\nrecalc_runs = "много"\ncolor = 1\n',
        encoding="utf-8")
    items, warnings = corpus.build_items(tmp_path, Plan(), corpus.load_manifest(tmp_path))
    assert items[0].plan == Plan()
    text = " ".join(warnings)
    assert "docx" in text and "recalc_runs" in text and "«color»" in text


def test_build_items_by_name_and_duplicates(tmp_path):
    _write(tmp_path / "x" / "one.xlsx", b"same")
    _write(tmp_path / "y" / "two.xlsx", b"same")
    (tmp_path / "corpus.toml").write_text('[files."one.xlsx"]\nsteps = ["open"]\n',
                                          encoding="utf-8")
    items, warnings = corpus.build_items(tmp_path, Plan(), corpus.load_manifest(tmp_path))
    assert [i.rel for i in items] == ["x/one.xlsx"] and items[0].plan.steps == ("open",)
    assert any("дубль" in w for w in warnings)


def test_file_id_is_content_hash_prefix(tmp_path):
    _write(tmp_path / "a.xlsx", b"abc")
    (item,), _ = corpus.build_items(tmp_path, Plan())
    assert item.sha256.startswith(item.file_id) and len(item.file_id) == corpus.FILE_ID_LEN
    assert item.file_id == "ba7816bf8f01" and item.size_bytes == 3


# ── отчёт ────────────────────────────────────────────────────────────────

def _report(tmp_path):
    _write(tmp_path / "Отчёт клиента.xlsx", b"r")
    (item,), _ = corpus.build_items(tmp_path, Plan())
    entry = corpus.file_entry(
        item, {"open": {"name": "Открытие файла", "time": 2.0, "runs": [2.0],
                        "error": "окно «Отчёт клиента.xlsx - Р7» не ответило"},
               "export:pdf": {"name": "Сохранение в PDF (конвертация x2t)", "time": 1.0,
                              "runs": [1.0], "r7_alerts": ["Отчёт клиента не сохранён"]}},
        error=None, elapsed_sec=12.34)
    entry["notes"] = "заметка про клиента"
    return corpus.build_report(
        [entry], Plan(), timestamp="20261007_120000", measure_schema=10, tool_version="1.2.3",
        version="2026.3.2", build={"build_number": "2026.3.2.1"},
        system={"environment": {"fingerprint_hash": "fp"}}, corpus_dir=str(tmp_path),
        warnings=["Отчёт клиента.xlsx: что-то"])


def test_build_report_shape(tmp_path):
    rep = _report(tmp_path)
    assert rep["kind"] == "corpus" and rep["corpus_format"] == 1
    f = rep["files"][0]
    assert f["name"] == "Отчёт клиента.xlsx" and f["elapsed_sec"] == 12.3
    assert f["plan"]["formats"] == ["pdf"] and not rep["hide_names"]
    json.dumps(rep, ensure_ascii=False)        # сериализуется


def test_hide_names_replaces_names_everywhere_but_op_names(tmp_path):
    rep = _report(tmp_path)
    fid = rep["files"][0]["id"]
    hidden = corpus.hide_names(rep)
    text = json.dumps(hidden, ensure_ascii=False)
    assert "Отчёт клиента" not in text and "заметка" not in text and str(tmp_path) not in text
    f = hidden["files"][0]
    assert f["name"] == fid and f["rel"] == fid and hidden["hide_names"]
    assert fid in f["steps"]["open"]["error"] and fid in hidden["warnings"][0]
    assert f["steps"]["export:pdf"]["name"] == "Сохранение в PDF (конвертация x2t)"
    # Исходный отчёт не изменён.
    assert rep["files"][0]["name"] == "Отчёт клиента.xlsx" and not rep["hide_names"]


def test_load_report_errors(tmp_path):
    with pytest.raises(CorpusError, match="не найден"):
        corpus.load_report(tmp_path / "нет.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(CorpusError, match="не прочитан"):
        corpus.load_report(bad)
    other = tmp_path / "performance_full_x.json"
    other.write_text('{"results": []}', encoding="utf-8")
    with pytest.raises(CorpusError, match="не отчёт корпуса"):
        corpus.load_report(other)
    good = tmp_path / "corpus_x.json"
    good.write_text(json.dumps(_report(tmp_path / "c")), encoding="utf-8")
    assert corpus.load_report(good)["kind"] == "corpus"


def test_corpus_folder_is_git_ignored_except_rules():
    text = (config.BASE_DIR / ".gitignore").read_text(encoding="utf-8")
    assert "/Corpus/*" in text and "!/Corpus/README.md" in text
    assert (config.BASE_DIR / "Corpus" / "README.md").is_file()
    example = config.BASE_DIR / "Corpus" / "corpus.example.toml"
    m = corpus.parse_manifest(__import__("tomllib").loads(example.read_text(encoding="utf-8")))
    assert not m.errors and m.files
    errors: list = []
    for key, entry in m.files.items():
        corpus.apply_overrides(Plan(), entry, key, errors)
    corpus.apply_overrides(Plan(), m.defaults, "defaults", errors)
    assert errors == []
