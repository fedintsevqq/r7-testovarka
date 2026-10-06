## Что сделано

-

## Проверка

- юнит-тесты: `.venv/Scripts/python.exe -m pytest -q` — N passed
- живой набор: N/8 (`R7_LIVE=1 .venv/Scripts/python.exe -m pytest -m live tests/live -v`)
  или почему не нужен: …
- сверка с эталоном (если задеты воркеры или `r7_ops.py`):
  `tests/manual_baseline_run.py after` против `Reports/baseline/before/` — …
- схема отчёта: не менялась / поднята до N (`MEASURE_SCHEMA_VERSION`)
