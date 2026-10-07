"""Пример плагина теста: «Жирный шрифт столбца A» (docs/plugins.md).

Лежит в docs/plugins/ и поэтому НЕ загружается: программа берёт только
plugins/*.py рядом с собой (в сборке — рядом с exe). Включить — скопировать
файл туда:

    copy docs\\plugins\\example_bold_column.py plugins\\

и перезапустить программу. Тест появится в конце группы «Операции в таблице»
с пометкой «плагин»; в наборах и в CLI — под именем NAME.

Что показывает пример:
  * подготовка вне замера (.prepare): рабочий лист фикстуры и выделение
    столбца A на всю высоту листа — результат не зависит от соседних тестов;
  * в замере одно действие — вызов api asc_setCellBold через ops.cdp_call
    (mutated выставляется до вызова: правило 7 соблюдается само);
  * запасного пути клавишами нет: Ctrl+B без проверенного выделения был бы
    слепым вводом (правило 8). CDP не сработал — исключение, тест получает
    ошибку, а не цифру;
  * уборки (.cleanup) нет: правку каждого повтора откатывает общий цикл
    повторов (_restore_history), как у встроенных тестов.
"""

NAME = "Жирный шрифт столбца A"


def register(ops):
    """Тесты плагина. Только строит функции: Р7 здесь трогать нельзя —
    register зовут и для списка тестов, когда Р7 ещё не запущен."""

    def prepare():
        ws = ops.prepare_work_sheet()               # без CDP — исключение
        rows = ws.get("rows") if isinstance(ws, dict) else None
        if not isinstance(rows, int) or rows < 1:
            raise RuntimeError("не известна высота рабочего листа")
        ops.select_range(f"A1:A{rows}")

    def bold_column():
        if ops.cdp_call(NAME, "asc_setCellBold", True):
            return
        raise RuntimeError("asc_setCellBold не выполнился через CDP — запасного "
                           "пути клавишами у теста нет")

    return [(NAME, ops.make_test(bold_column, prepare))]
