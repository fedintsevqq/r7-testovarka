"""Что видит пользователь (схема 10): первый кадр, самая длинная блокировка
интерфейса и JS-куча после операции.

Время операции эти метрики НЕ меняют: конец операции по-прежнему —
_wait_operation_done (на CDP-пути пинг редактора). Порядок на повтор:

  1. _ux_arm — ДО секундомера (рядом со снимком истории): сброс меток
     прошлого повтора, наблюдатель longtask (ставится один раз за жизнь
     страницы), JS-куча «до» через Performance.getMetrics;
  2. замер: операция через _op_js сама отмечает начало (уже снятое __t0),
     конец вызова api и ставит двойной requestAnimationFrame — после
     api_ms, одна метка времени во втором колбэке (r7_webdriver_connector,
     _UX_MARK_FN);
  3. _ux_collect — ПОСЛЕ конца замера и отложенной CDP-проверки, до отката
     истории: метки, longtask, JS-куча «после».

Поля повтора:
  ux_first_frame_ms  — от начала операции в странице до второго кадра
                       после возврата api (первый кадр уже отрисован), мс;
  ux_longest_task_ms — самая длинная задача главного потока (longtask,
                       ≥ 50 мс), закончившаяся после начала операции; 0 —
                       длинных задач не было; None — CEF не отдаёт longtask;
  js_heap_mb         — используемая JS-куча после операции, МБ
                       (JSHeapUsedSize из CDP Performance, запасной путь —
                       performance.memory). Пик между точками не виден:
                       непрерывный опрос внутри замера добавил бы работу;
  js_heap_delta_mb   — куча после минус до, МБ.
Клавиатурный путь и экспорт через _op_js не идут — поля кадра и задачи None.
Ответ None или мусор от CDP — None в полях, клавиш никто не шлёт.
"""
import math
import statistics

UX_KEYS = ("ux_first_frame_ms", "ux_longest_task_ms", "js_heap_mb", "js_heap_delta_mb")

_MB = 1024 * 1024


def _num(value):
    """Конечное неотрицательное число или None (bool — не число)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _heap_bytes(metrics, js_value):
    """JSHeapUsedSize из Performance.getMetrics, иначе performance.memory."""
    if isinstance(metrics, dict):
        v = _num(metrics.get("JSHeapUsedSize"))
        if v is not None:
            return v
    return _num(js_value)


def ux_from_marks(marks, heap_before=None, heap_after=None):
    """Поля повтора из ответа _UX_COLLECT_JS и снимков кучи (байты).

    Returns:
        dict: ключи UX_KEYS, значение None — снять не удалось.
    """
    out = dict.fromkeys(UX_KEYS)
    marks = marks if isinstance(marks, dict) else {}
    t0, frame = _num(marks.get("t0")), _num(marks.get("frame"))
    if t0 is not None and frame is not None and frame >= t0:
        out["ux_first_frame_ms"] = round(frame - t0, 1)
    if marks.get("longtask") is True and t0 is not None:
        longest = _num(marks.get("longest"))
        if longest is not None:
            out["ux_longest_task_ms"] = round(longest, 1)
    if heap_after is not None:
        out["js_heap_mb"] = round(heap_after / _MB, 1)
        if heap_before is not None:
            out["js_heap_delta_mb"] = round((heap_after - heap_before) / _MB, 1)
    return out


def aggregate_ux(run_ux, idx):
    """Медианы полей по прогонам статистики (те же индексы, что у времени).

    Args:
        run_ux: список dict | None по повторам.
        idx: индексы повторов, вошедших в медиану.

    Returns:
        dict: ключи UX_KEYS; None — ни у одного годного повтора поля нет.
    """
    out = {}
    for key in UX_KEYS:
        vals = [run_ux[i][key] for i in idx
                if i < len(run_ux) and isinstance(run_ux[i], dict)
                and run_ux[i].get(key) is not None]
        out[key] = round(statistics.median(vals), 1) if vals else None
    return out


class UxMetricsMixin:
    """Метрики интерфейса вокруг замера — часть R7Testovarka."""

    UX_CDP_TIMEOUT_SEC = 5.0   # round-trip вне замера; не ответил — поля None

    def _ux_heap_bytes(self, connector, js_value=None):
        try:
            metrics = connector.performance_metrics(timeout=self.UX_CDP_TIMEOUT_SEC)
        except Exception:  # куча — необязательная метрика
            metrics = None
        return _heap_bytes(metrics, js_value)

    def _ux_arm(self):
        """Взводит метрики перед повтором. Звать ДО секундомера.

        Returns:
            dict | None: состояние для _ux_collect; None — CDP нет.
        """
        connector = self._cdp_ops_connector()
        if connector is None:
            return None
        try:
            armed = connector.ux_arm(timeout=self.UX_CDP_TIMEOUT_SEC)
        except Exception:  # не взвелось — повтор идёт без метрик интерфейса
            armed = None
        js_heap = armed.get("heap") if isinstance(armed, dict) else None
        return {"armed": isinstance(armed, dict) and armed.get("armed") is True,
                "longtask": isinstance(armed, dict) and armed.get("longtask") is True,
                "heap_before": self._ux_heap_bytes(connector, js_heap)}

    def _ux_collect(self, state, log_cb=None):
        """Снимает метрики повтора. Звать ПОСЛЕ _wait_operation_done и
        _flush_pending_cdp_verify, до отката истории.

        Returns:
            dict | None: поля UX_KEYS; None — метрики не взводились.
        """
        if state is None:
            return None
        connector = self._cdp_ops_connector()
        if connector is None:
            return dict.fromkeys(UX_KEYS)
        # Сбор зовётся всегда: он же разоружает окно. Ответ взвода мог
        # потеряться (таймаут сокета), а страница осталась взведённой — тогда
        # её метки не наши, и читать их нельзя.
        try:
            marks = connector.ux_collect(timeout=self.UX_CDP_TIMEOUT_SEC)
        except Exception:  # метки не прочитались — поля кадра и задачи None
            marks = None
        if not state.get("armed"):
            marks = {"heap": marks.get("heap")} if isinstance(marks, dict) else None
        js_heap = marks.get("heap") if isinstance(marks, dict) else None
        ux = ux_from_marks(marks, state.get("heap_before"),
                           self._ux_heap_bytes(connector, js_heap))
        if log_cb is not None and any(ux[k] is not None for k in UX_KEYS[:2]):
            log_cb("   👁 интерфейс: " + ", ".join(
                f"{label} {ux[k]:.0f} мс" for k, label in
                (("ux_first_frame_ms", "первый кадр"),
                 ("ux_longest_task_ms", "самая длинная блокировка"))
                if ux[k] is not None))
        return ux
