"""Настройки на машину: r7_settings.json рядом с программой (config.BASE_DIR).

Инструмент разъезжается по ПК команды, и на каждом своё: Р7 на другом
диске, отчёты в общей папке, другое число повторов. Файл правится руками
или мастером первого запуска; отсутствующий или битый файл — настройки
по умолчанию, программа из-за него не падает (битый — одно предупреждение
в журнал). Настройки интерфейса (ui_settings.json) и выбор тестов
(selected_tests.json) живут отдельно и сюда не переезжают.

Ключи DEFAULTS — контракт для остального кода и других веток:
    r7_path             — путь к DesktopEditors.exe; имеет приоритет над реестром
    reports_folder      — папка отчётов вместо BASE_DIR/Reports
    default_runs        — повторы по умолчанию для тестов правки
    team_reports_folder — общая папка команды: копия каждого полного JSON и
                          HTML в подпапку «<hostname>-<отпечаток>», тренды и
                          сравнение читают оттуда отчёты коллег (r7.team_folder)
    changelog_url_template — шаблон ссылки на changelog сборки Р7 для отчёта,
                          поля {version} и {build}: «https://…/{version}»
    manage_power_plan   — на время прогона включать план питания «Высокая
                          производительность» и возвращать прежний (r7.stand);
                          false — план не трогать
    trace_on_regression — после `python -m r7 run` снимать трассу (r7.trace)
                          с операций, где сравнение с эталоном дало регрессию
                          или «вероятную регрессию»; то же, что --trace-regressions
    plugins_enabled     — загружать тесты из plugins/*.py (r7.plugins,
                          docs/plugins.md). Плагин выполняется с правами
                          инструмента, то есть часто от администратора:
                          только доверенные файлы. false или --no-plugins —
                          не загружать
Прочие ключи (first_run_done и т. п.) хранятся как есть.
"""
import json

from r7 import config, logfile

SETTINGS_FILE = "r7_settings.json"

DEFAULTS = {"r7_path": None, "reports_folder": None, "default_runs": None,
            "team_reports_folder": None, "changelog_url_template": None,
            "manage_power_plan": True, "trace_on_regression": False,
            "plugins_enabled": True}


def settings_path():
    """Путь к файлу настроек: читается при каждом вызове, чтобы тесты могли
    подменить config.BASE_DIR."""
    return config.BASE_DIR / SETTINGS_FILE


def load_settings():
    """Настройки с подставленными умолчаниями. Никогда не бросает: нет
    файла — умолчания молча, битый файл или не словарь — умолчания и одно
    предупреждение в журнал."""
    path = settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return dict(DEFAULTS)
    except Exception as e:  # битый JSON, нет прав на чтение — работаем по умолчанию
        logfile.get_logger().warning("r7_settings.json не прочитан (%s: %s) — настройки "
                                     "по умолчанию", type(e).__name__, e)
        return dict(DEFAULTS)
    if not isinstance(data, dict):
        logfile.get_logger().warning("r7_settings.json: ожидался объект JSON, а не %s — "
                                     "настройки по умолчанию", type(data).__name__)
        return dict(DEFAULTS)
    return {**DEFAULTS, **data}


def save_settings(data):
    """Пишет настройки целиком. False — не записалось (папка только для
    чтения и т. п.); причина — в журнале, вызывающему падать не нужно."""
    path = settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:  # папка только для чтения — настройки не запомнятся
        logfile.get_logger().warning("r7_settings.json не записан (%s: %s)",
                                     type(e).__name__, e)
        return False


def get(key):
    """Действующее значение ключа: из файла, если задано, иначе из DEFAULTS
    (у неизвестного ключа умолчание — None)."""
    value = load_settings().get(key)
    return DEFAULTS.get(key) if value is None else value
