"""Самопроверка сборки (r7.selfcheck): из исходников — всё на месте."""
from r7 import selfcheck


def test_selfcheck_passes_from_source():
    lines = []
    assert selfcheck.run(out=lines.append) == 0
    assert lines[-1] == "OK" and "шаблоны" in lines[0]


def test_selfcheck_reports_missing_dependency(monkeypatch):
    monkeypatch.setattr(selfcheck, "REQUIRED", selfcheck.REQUIRED + ("нет_такого_модуля",))
    lines = []
    assert selfcheck.run(out=lines.append) == 1
    assert any("нет_такого_модуля" in x for x in lines)
