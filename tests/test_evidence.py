"""Пакет улик (r7.evidence): архив из двух отчётов, страницы сравнения,
окружения, хвоста журнала и текста тикета. Без Tk и без Р7: отчёты —
синтетические JSON, страница сравнения — настоящий шаблон."""
import json
import zipfile

import pytest

from r7 import evidence

OP = "Выделение всех ячеек (Ctrl+A)"
OP2 = "Добавление нового листа"


def _result(name, median, n=6, **extra):
    runs = [median * (1 + d) for d in (0.0, 0.01, -0.01, 0.02, 0.0, -0.02)][:n]
    r = {"name": name, "time": median, "error": None, "runs": runs,
         "run_statuses": ["ok"] * len(runs), "n_runs": len(runs),
         "first_run_discarded": False, "mad": round(median * 0.01, 4), "ram": 900.0, "cpu": 50.0}
    r.update(extra)
    return r


def _report(folder, name, version, ops, warnings=(), **extra):
    data = {"timestamp": "20261007_120000", "measure_schema": 9, "version": version,
            "tool_version": "1.2.0", "test_file": "E:/TestFiles/r7-test-50k.xlsx",
            "system": {"os": "Windows-10", "ram_total_gb": 31.1, "cpu_model": "AMD64 Family 26",
                       "cpu_cores_logical": 16, "dpi_scale_pct": 100,
                       "environment": {"power_plan": "High Performance",
                                       "warnings": list(warnings)}},
            "summary": {"peak_ram_mb": 2900.0},
            "results": ops}
    data.update(extra)
    p = folder / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def pair(tmp_path):
    base = _report(tmp_path, "performance_full_1.json", "2026.3.1",
                   [_result(OP, 1.0), _result(OP2, 0.5)])
    cur = _report(tmp_path, "performance_full_2.json", "2026.3.2",
                  [_result(OP, 1.5), _result(OP2, 0.5)], warnings=["ноутбук работает от батареи"],
                  build="3229")
    return base, cur


def _members(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


# ── архив ────────────────────────────────────────────────────────────────

def test_pack_contains_reports_html_environment_log_and_ticket(pair, tmp_path):
    base, cur = pair
    log = tmp_path / "r7.log"
    log.write_text("\n".join(f"строка {i}" for i in range(2500)), encoding="utf-8")
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"png")

    out = evidence.build_evidence_pack(base, cur, tmp_path / "evidence", log_file=log,
                                       extra_files=[shot, tmp_path / "нет.txt"])

    assert out.parent == tmp_path / "evidence" and out.name.startswith("evidence_")
    members = _members(out)
    assert set(members) == {"performance_full_1.json", "performance_full_2.json",
                            "comparison.html", "environment.json", "r7-testovarka.tail.log",
                            "shot.png", "ticket.md"}
    tail = members["r7-testovarka.tail.log"].decode("utf-8").splitlines()
    assert len(tail) == 2000 and tail[0] == "строка 500" and tail[-1] == "строка 2499"
    env_json = json.loads(members["environment.json"])
    assert env_json["base"]["version"] == "2026.3.1" and env_json["current"]["build"] == "3229"
    assert env_json["current"]["system"]["cpu_cores_logical"] == 16
    html = members["comparison.html"].decode("utf-8")
    assert "Регрессии: 1" in html
    assert (out.parent / (out.stem + "_ticket.md")).read_text(encoding="utf-8") == \
        members["ticket.md"].decode("utf-8")


def test_pack_without_log_file_is_tolerated(pair, tmp_path):
    base, cur = pair
    out = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=tmp_path / "нет.log")
    members = _members(out)
    assert "r7-testovarka.tail.log" not in members
    assert "- (журнал программы не найден — хвост не приложен)" in members["ticket.md"].decode("utf-8")


def test_pack_uses_passed_renderer_and_labels(pair, tmp_path):
    base, cur = pair
    seen = {}

    def render(datasets, base_path):
        seen["labels"] = [d["version"] for d in datasets]
        seen["base"] = base_path
        return "<html>страница</html>"

    out = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=None,
                                       render_html=render, labels=("старая", "новая"))
    assert seen == {"labels": ["старая", "новая"], "base": str(base)}
    members = _members(out)
    assert members["comparison.html"].decode("utf-8") == "<html>\u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0430</html>"
    assert "Регрессия Выделение всех ячеек (Ctrl+A): старая → новая (+50 %)" in \
        members["ticket.md"].decode("utf-8")


def test_same_basenames_get_prefixes(tmp_path):
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    base = _report(a_dir, "performance_full.json", "v1", [_result(OP, 1.0)])
    cur = _report(b_dir, "performance_full.json", "v2", [_result(OP, 1.0)])
    out = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=None)
    names = set(_members(out))
    assert {"base_performance_full.json", "cur_performance_full.json"} <= names


def test_two_packs_in_one_second_do_not_overwrite(pair, tmp_path):
    base, cur = pair
    first = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=None)
    second = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=None)
    assert first != second and first.exists() and second.exists()


def test_rejects_non_report_json(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        evidence.build_evidence_pack(bad, bad, tmp_path / "ev", log_file=None)


# ── тикет ────────────────────────────────────────────────────────────────

def test_ticket_with_regression(pair, tmp_path):
    base, cur = pair
    out = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=None)
    ticket = _members(out)["ticket.md"].decode("utf-8")
    assert ticket.startswith(
        "# Регрессия Выделение всех ячеек (Ctrl+A): 2026.3.1 → 2026.3.2 (сборка 3229) (+50 %)")
    assert "медиана 1,500 с против 1,000 с (+50 %)" in ticket
    for section in ("## Сборки", "## Стенд", "## Шаги", "## Цифры", "## Условия прогона",
                    "## Вложения"):
        assert section in ticket
    assert "- База: 2026.3.1, прогон 07.10.2026 12:00, R7-Testovarka 1.2.0, схема замера 9" in ticket
    assert "- Проверяемая: 2026.3.2 (сборка 3229)" in ticket
    assert "CPU: AMD64 Family 26, 16 лог. ядер" in ticket and "RAM: 31.1 ГБ" in ticket
    assert "План питания: High Performance" in ticket and "Масштаб экрана: 100 %" in ticket
    assert "1. Открыть в Р7-Офис 2026.3.2 (сборка 3229) файл `r7-test-50k.xlsx`" in ticket
    assert "«Выделение всех ячеек (Ctrl+A)», повторов подряд: 6" in ticket
    # Таблица цифр: регрессия помечена, вторая операция — без изменений.
    assert "| Выделение всех ячеек (Ctrl+A) | 1,000 (MAD 0,010) | 1,500 (MAD 0,015) | 6/6 | +50 % |" in ticket
    assert "| **РЕГРЕССИЯ** |" in ticket
    assert "| Добавление нового листа | 0,500 (MAD 0,005) | 0,500 (MAD 0,005) | 6/6 | +0 % |" in ticket
    assert "без изменений |" in ticket
    assert "- База: предупреждений не было" in ticket
    assert "- Проверяемая: ноутбук работает от батареи" in ticket
    assert "- `performance_full_1.json`" in ticket and "- `ticket.md`" in ticket


def test_ticket_without_regression(tmp_path):
    base = _report(tmp_path, "performance_full_1.json", "v1", [_result(OP, 1.0)])
    cur = _report(tmp_path, "performance_full_2.json", "v2", [_result(OP, 0.5)])   # ускорение
    out = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=None)
    ticket = _members(out)["ticket.md"].decode("utf-8")
    assert ticket.startswith("# Сравнение v1 → v2: регрессий нет")
    assert "Статистически значимых замедлений между сборками не найдено" in ticket
    assert "УСКОРЕНИЕ" in ticket and "**РЕГРЕССИЯ**" not in ticket


def test_ticket_lists_several_regressions_strongest_first(tmp_path):
    base = _report(tmp_path, "performance_full_1.json", "v1",
                   [_result(OP, 1.0), _result(OP2, 1.0)])
    cur = _report(tmp_path, "performance_full_2.json", "v2",
                  [_result(OP, 1.2), _result(OP2, 2.0)])
    out = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=None)
    ticket = _members(out)["ticket.md"].decode("utf-8")
    assert ticket.startswith(f"# Регрессия {OP2}, {OP}: v1 → v2 (+100 %, +20 %)")


def test_old_json_without_runs_gives_not_enough_runs(tmp_path):
    """Отчёт схемы 1: только time, без runs/mad/system — тикет собирается,
    вердикт «недостаточно прогонов», стенд прочерками."""
    base = _report(tmp_path, "performance_full_1.json", "v1",
                   [{"name": OP, "time": 1.0, "error": None}], system=None)
    cur_data = {"version": "v2", "results": [{"name": OP, "time": 3.0}]}
    cur = tmp_path / "performance_full_2.json"
    cur.write_text(json.dumps(cur_data), encoding="utf-8")
    out = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=None)
    ticket = _members(out)["ticket.md"].decode("utf-8")
    assert ticket.startswith("# Сравнение v1 → v2: регрессий нет")
    assert "| 1,000 | 3,000 | 0/0 | +200 % | — | недостаточно прогонов |" in ticket
    assert "CPU: —" in ticket and "Отпечаток стенда: —" in ticket
    assert "схема замера 1" in ticket
    assert "смешаны файлы разных версий схемы" in ticket


def test_ticket_notes_different_stands(pair, tmp_path):
    base, cur = pair
    data = json.loads(cur.read_text(encoding="utf-8"))
    data["system"]["cpu_model"] = "Intel i5"
    data["system"]["fingerprint"] = "abc123"
    cur.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    base_data = json.loads(base.read_text(encoding="utf-8"))
    base_data["fingerprint"] = "def456"
    base.write_text(json.dumps(base_data, ensure_ascii=False), encoding="utf-8")
    ticket = _members(evidence.build_evidence_pack(base, cur, tmp_path / "ev",
                                                   log_file=None))["ticket.md"].decode("utf-8")
    assert "> Отпечатки стендов разные" in ticket
    assert "> У базы и проверяемой сборки разный CPU: AMD64 Family 26 против Intel i5." in ticket
    assert "База (2026.3.1):" in ticket and "Отпечаток стенда: abc123" in ticket


def test_ticket_marks_errored_and_dependent_ops(tmp_path):
    base = _report(tmp_path, "performance_full_1.json", "v1",
                   [_result(OP, 1.0), _result(OP2, 1.0, runs_independent=False)])
    cur = _report(tmp_path, "performance_full_2.json", "v2",
                  [_result(OP, 0.0, error="упало"), _result(OP2, 2.0)])
    rows = {c["op"]: c for c in evidence.op_comparisons(evidence.load_report(base),
                                                         evidence.load_report(cur))}
    assert rows[OP]["verdict"] == "нет данных" and rows[OP]["delta_pct"] is None
    assert rows[OP2]["verdict"] == "зависимые повторы" and rows[OP2]["delta_pct"] == 100.0


def test_op_comparisons_keeps_base_order_and_common_ops_only():
    base = {"results": [_result(OP2, 1.0), _result(OP, 1.0), _result("только база", 1.0)]}
    cur = {"results": [_result(OP, 1.0), _result(OP2, 1.0), _result("только новая", 1.0)]}
    assert [c["op"] for c in evidence.op_comparisons(base, cur)] == [OP2, OP]


def test_report_label_with_and_without_build():
    assert evidence.report_label({"version": "2026.3.2", "build": "3229"}) == "2026.3.2 (сборка 3229)"
    assert evidence.report_label({"version": "2026.3.2.3229", "build": "3229"}) == "2026.3.2.3229"
    assert evidence.report_label({}, "имя файла") == "имя файла"


def test_log_tail_missing_and_short(tmp_path):
    assert evidence.log_tail(tmp_path / "нет.log") is None
    assert evidence.log_tail(None) is None
    short = tmp_path / "s.log"
    short.write_text("a\nb\n", encoding="utf-8")
    assert evidence.log_tail(short, lines=5) == "a\nb\n"


def test_open_step_says_fresh_launch_not_undo(tmp_path):
    # Открытие — независимые холодные старты: в шагах «новый запуск Р7», без отката правки.
    base = _report(tmp_path, "performance_full_1.json", "v1", [_result("Открытие файла", 8.0)])
    cur = _report(tmp_path, "performance_full_2.json", "v2", [_result("Открытие файла", 12.0)])
    out = evidence.build_evidence_pack(base, cur, tmp_path / "ev", log_file=None)
    ticket = _members(out)["ticket.md"].decode("utf-8")
    assert "Закрыть Р7-Офис и открыть файл заново, повторов: 6" in ticket
    assert "правку откатывать" not in ticket
