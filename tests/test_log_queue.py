"""Журнал прогона из фоновых потоков (этап 4, шаг 3): виджет пишет только
главный поток, порядок строк сохраняется, метка времени — момента вызова."""
import threading
from datetime import datetime
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod


class _Text:
    """Заглушка tk.Text: запоминает вставки и поток, из которого они пришли."""

    def __init__(self):
        self.lines, self.threads = [], set()

    def insert(self, _index, *chunks):
        """chunks — пары (текст, тег), как у tk.Text.insert; строка — их
        склейка, тег строки — тег её последнего куска (сообщения)."""
        texts = chunks[0::2]
        tags = chunks[1::2] or (None,)
        self.lines.append(("".join(texts), tags[-1]))
        self.threads.add(threading.current_thread().name)

    def see(self, _index):
        pass

    def delete(self, *a):
        self.lines.clear()


@pytest.fixture
def app():
    a = r7mod.R7Testovarka.__new__(r7mod.R7Testovarka)
    a.test_log = _Text()
    a.root = Mock()
    return a


def _from_thread(fn):
    t = threading.Thread(target=fn, name="worker")
    t.start()
    t.join()


def test_background_message_waits_for_main_thread(app):
    _from_thread(lambda: app.add_test_log("⏳ из потока"))
    assert app.test_log.lines == []             # виджет из потока не тронут
    app._drain_test_log()
    assert [t for t, _ in app.test_log.lines][0].endswith("⏳ из потока\n")
    assert app.test_log.threads == {threading.current_thread().name}
    app.root.after.assert_called_with(app.LOG_DRAIN_MS, app._drain_test_log)


def test_main_thread_message_flushes_queue_first(app):
    _from_thread(lambda: [app.add_test_log(f"фон {i}") for i in range(3)])
    app.add_test_log("главный")
    texts = [t.split("  ", 1)[1].strip() for t, _ in app.test_log.lines]
    assert texts == ["фон 0", "фон 1", "фон 2", "главный"]


def test_stamp_is_taken_at_call_time(app, monkeypatch):
    stamps = iter([datetime(2026, 10, 7, 1, 2, 3)])

    class _DT:
        @staticmethod
        def now():
            return next(stamps)
    import r7.ui.main_window as mw
    monkeypatch.setattr(mw, "datetime", _DT)
    _from_thread(lambda: app.add_test_log("x"))
    app._drain_test_log(reschedule=False)
    assert app.test_log.lines[0][0].startswith("01:02:03  ")


def test_severity_tags_by_first_symbol(app):
    for msg in ("❌ сбой", "⚠️ внимание", "✅ ок", "📊 медиана 1.0", "   ⏱️ прогон 2",
                "🔌 WebDriver"):
        app.add_test_log(msg)
    assert [tag for _, tag in app.test_log.lines] == ["ERROR", "WARN", "OK", "RESULT",
                                                      "RESULT", "INFO"]


def test_drain_is_bounded_per_pass(app):
    _from_thread(lambda: [app.add_test_log(str(i)) for i in range(app.LOG_DRAIN_BATCH + 5)])
    app._drain_test_log(reschedule=False)
    assert len(app.test_log.lines) == app.LOG_DRAIN_BATCH
    app._drain_test_log(reschedule=False)
    assert len(app.test_log.lines) == app.LOG_DRAIN_BATCH + 5


def test_drain_survives_closed_window(app):
    app.root.after.side_effect = RuntimeError("application has been destroyed")
    app._drain_test_log()                       # не падает
