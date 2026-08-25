"""Тесты фонового семплера ресурсов и детектора утечек (этап 2, H3).

ResourceSampler — реальный threading.Thread, но тесты не запускают его в
режиме реального времени: либо вызывают _sample_once() напрямую (без сна),
либо используют interval=0.01 и join() с коротким таймаутом. Живой Р7 не
нужен — get_procs/connector мокаются.
"""
import time
from unittest.mock import Mock

import pytest

import r7_Testovarka as r7mod


# ── _linear_slope ──────────────────────────────────────────────────────────

def test_linear_slope_perfect_line():
    # y = 2x + 1
    pts = [(0, 1), (1, 3), (2, 5), (3, 7)]
    assert r7mod._linear_slope(pts) == pytest.approx(2.0)


def test_linear_slope_flat_line_is_zero():
    pts = [(0, 10), (1, 10), (2, 10), (3, 10)]
    assert r7mod._linear_slope(pts) == pytest.approx(0.0)


def test_linear_slope_negative():
    pts = [(0, 10), (1, 8), (2, 6), (3, 4)]
    assert r7mod._linear_slope(pts) == pytest.approx(-2.0)


def test_linear_slope_none_with_single_point():
    assert r7mod._linear_slope([(1, 1)]) is None


def test_linear_slope_none_with_no_points():
    assert r7mod._linear_slope([]) is None


def test_linear_slope_none_when_all_x_equal():
    """Вертикальная выборка (все x совпадают) — наклон не определён, а не
    деление на ноль."""
    assert r7mod._linear_slope([(5, 1), (5, 2), (5, 3)]) is None


def test_linear_slope_tolerates_noise():
    """Реальные замеры не лежат на идеальной прямой — наклон должен
    улавливаться сквозь шум, а не требовать точного совпадения."""
    pts = [(0, 1.0), (1, 3.1), (2, 4.9), (3, 7.2), (4, 8.8)]
    slope = r7mod._linear_slope(pts)
    assert 1.8 < slope < 2.2


# ── detect_leak ─────────────────────────────────────────────────────────────

def _samples(n, heap_start=100.0, heap_step=0.0, doc_counts=None, interval_sec=60):
    """n замеров, растянутых на n*interval_sec секунд, heap растёт на
    heap_step МБ за замер. doc_counts — список того же размера, либо None
    (все замеры — 1 документ)."""
    now = time.time()
    docs = doc_counts or [1] * n
    return [
        {"t": now + i * interval_sec, "heap_mb": heap_start + i * heap_step,
         "rss_mb": None, "doc_count": docs[i]}
        for i in range(n)
    ]


def test_detect_leak_flags_growing_heap_with_stable_docs():
    # +1 МБ каждые 60 сек = 60 МБ/час — заведомо выше порога 5.0
    samples = _samples(40, heap_step=1.0)
    result = r7mod.detect_leak(samples)
    assert result["leak"] is True
    assert result["slope_mb_per_hour"] == pytest.approx(60.0, rel=0.05)
    assert "УТЕЧКА" in result["verdict"]


def test_detect_leak_no_leak_on_flat_heap():
    samples = _samples(40, heap_step=0.0)
    result = r7mod.detect_leak(samples)
    assert result["leak"] is False
    assert result["slope_mb_per_hour"] == pytest.approx(0.0, abs=0.01)


def test_detect_leak_not_flagged_when_doc_count_grows():
    """Рост heap из-за открытия новых документов — не утечка."""
    samples = _samples(40, heap_step=1.0, doc_counts=list(range(1, 41)))
    result = r7mod.detect_leak(samples)
    assert result["leak"] is False
    assert "документ" in result["verdict"]


def test_detect_leak_insufficient_samples():
    samples = _samples(5, heap_step=1.0)
    result = r7mod.detect_leak(samples, min_samples=30)
    assert result["leak"] is None
    assert result["n_samples"] == 5
    assert "недостаточно" in result["verdict"]


def test_detect_leak_ignores_none_values():
    samples = _samples(40, heap_step=0.0)
    for s in samples[:10]:
        s["heap_mb"] = None  # первые 10 замеров без heap (CDP ещё не подключился)
    result = r7mod.detect_leak(samples)
    assert result["n_samples"] == 30


def test_detect_leak_custom_threshold():
    samples = _samples(40, heap_step=0.5)  # 30 МБ/час
    below = r7mod.detect_leak(samples, threshold_mb_per_hour=50.0)
    above = r7mod.detect_leak(samples, threshold_mb_per_hour=10.0)
    assert below["leak"] is False
    assert above["leak"] is True


def test_detect_leak_works_on_rss_key_too():
    samples = [{"t": time.time() + i * 60, "rss_mb": 500 + i * 2, "heap_mb": None,
               "doc_count": 1} for i in range(35)]
    result = r7mod.detect_leak(samples, key="rss_mb", threshold_mb_per_hour=10.0)
    assert result["leak"] is True


# ── ResourceSampler ──────────────────────────────────────────────────────────

def _fake_process(rss_mb):
    p = Mock()
    p.memory_info.return_value = Mock(rss=int(rss_mb * 1024 * 1024))
    return p


def test_sample_once_collects_rss():
    procs = [_fake_process(100), _fake_process(50)]
    sampler = r7mod.ResourceSampler(get_procs=lambda: procs)
    sampler._sample_once()
    assert len(sampler.samples) == 1
    assert sampler.samples[0]["rss_mb"] == pytest.approx(150.0)
    assert sampler.samples[0]["heap_mb"] is None


def test_sample_once_survives_get_procs_exception():
    def boom():
        raise RuntimeError("psutil упал")
    sampler = r7mod.ResourceSampler(get_procs=boom, log_cb=Mock())
    sampler._sample_once()  # не должно поднять исключение
    assert len(sampler.samples) == 1
    assert sampler.samples[0]["rss_mb"] is None


def test_sample_once_skips_heap_when_no_connector():
    sampler = r7mod.ResourceSampler(get_procs=lambda: [])
    sampler._sample_once()
    assert sampler.samples[0]["heap_mb"] is None
    assert sampler.samples[0]["doc_count"] is None


def test_sample_once_skips_heap_when_connector_not_connected():
    connector = Mock()
    connector.connected = False
    sampler = r7mod.ResourceSampler(get_procs=lambda: [], connector=connector)
    sampler._sample_once()
    connector.performance_metrics.assert_not_called()
    assert sampler.samples[0]["heap_mb"] is None


def test_sample_once_collects_heap_when_connected():
    connector = Mock()
    connector.connected = True
    connector.performance_metrics.return_value = {
        "JSHeapUsedSize": 200 * 1024 * 1024, "Documents": 2,
    }
    sampler = r7mod.ResourceSampler(get_procs=lambda: [], connector=connector)
    sampler._sample_once()
    assert sampler.samples[0]["heap_mb"] == pytest.approx(200.0)
    assert sampler.samples[0]["doc_count"] == 2


def test_sample_once_survives_connector_exception():
    connector = Mock()
    connector.connected = True
    connector.performance_metrics.side_effect = RuntimeError("ws closed")
    sampler = r7mod.ResourceSampler(get_procs=lambda: [], connector=connector, log_cb=Mock())
    sampler._sample_once()  # не должно поднять исключение
    assert sampler.samples[0]["heap_mb"] is None


def test_run_samples_immediately_then_on_interval():
    """Первая точка снимается сразу, не через interval — иначе короткий
    прогон рискует не набрать ни одной."""
    calls = []
    sampler = r7mod.ResourceSampler(get_procs=lambda: (calls.append(1) or []),
                                    interval=0.05)
    sampler.start()
    time.sleep(0.02)  # меньше interval — первая точка уже должна быть
    sampler.stop()
    sampler.join(timeout=2)
    assert len(calls) >= 1


def test_stop_halts_sampling():
    sampler = r7mod.ResourceSampler(get_procs=lambda: [], interval=0.02)
    sampler.start()
    time.sleep(0.1)
    sampler.stop()
    sampler.join(timeout=2)
    n_after_stop = len(sampler.samples)
    time.sleep(0.1)
    assert len(sampler.samples) == n_after_stop  # не растёт после stop()


def test_snapshot_is_a_copy_not_live_reference():
    sampler = r7mod.ResourceSampler(get_procs=lambda: [])
    sampler._sample_once()
    snap = sampler.snapshot()
    sampler._sample_once()
    assert len(snap) == 1  # снимок не подрос вслед за sampler.samples
    assert len(sampler.samples) == 2


def test_snapshot_is_thread_safe_during_concurrent_sampling():
    """Не строгий тест на гонки (недетерминированно по природе), а дымовой:
    snapshot() не должен падать/бросать исключение, пока run() пишет в тот
    же список из другого потока."""
    sampler = r7mod.ResourceSampler(get_procs=lambda: [], interval=0.001)
    sampler.start()
    errors = []
    for _ in range(20):
        try:
            sampler.snapshot()
        except Exception as e:
            errors.append(e)
        time.sleep(0.001)
    sampler.stop()
    sampler.join(timeout=2)
    assert errors == []


def test_sampler_is_daemon_thread():
    """Не должен держать процесс живым, если приложение закрывается, пока
    соак-тест ещё крутится."""
    sampler = r7mod.ResourceSampler(get_procs=lambda: [])
    assert sampler.daemon is True


# ── допуск на дрейф doc_count (найдено живой проверкой 25.08.2026) ────────
# CDP-метрика "Documents" — внутренний счётчик DOM-документов CEF, не
# «сколько файлов открыл пользователь». На живом Р7 с одним открытым файлом
# и без единого действия пользователя она сама уехала 228 -> 232 за 4 сек
# простоя. Точное равенство было бы ложным сигналом "документ открыт заново"
# почти при каждом реальном прогоне.

def test_detect_leak_tolerates_small_doc_count_drift_like_live_r7():
    """Воспроизводит ровно то, что показал живой прогон: doc_count дрейфует
    228->232 (рост меньше 10%) при одном открытом файле — не должно
    блокировать вердикт об утечке."""
    samples = _samples(40, heap_step=1.0, doc_counts=[228, 229, 230, 231, 232] * 8)
    result = r7mod.detect_leak(samples)
    assert result["leak"] is True


def test_detect_leak_flags_doc_count_jump_beyond_tolerance():
    """Скачок заметно больше дрейфа (открыт второй документ) всё ещё должен
    сниматься с подозрения на утечку."""
    docs = [228] * 20 + [280] * 20  # скачок на 52 (>> 10% от 228)
    samples = _samples(40, heap_step=1.0, doc_counts=docs)
    result = r7mod.detect_leak(samples)
    assert result["leak"] is False
    assert "документ" in result["verdict"]


def test_detect_leak_missing_doc_count_does_not_block_verdict():
    """Без CDP (rss-only соак) doc_count всегда None — отсутствие сигнала
    не должно само по себе запрещать вердикт об утечке."""
    samples = [{"t": time.time() + i * 60, "heap_mb": 100 + i * 1.0,
               "rss_mb": None, "doc_count": None} for i in range(40)]
    result = r7mod.detect_leak(samples)
    assert result["leak"] is True


def test_detect_leak_custom_tolerance_fraction():
    docs = [100, 105, 110, 115, 120] * 8  # дрейф 20% от базы 100
    samples = _samples(40, heap_step=1.0, doc_counts=docs)
    strict = r7mod.detect_leak(samples, doc_stable_tolerance_frac=0.05)
    loose = r7mod.detect_leak(samples, doc_stable_tolerance_frac=0.30)
    assert strict["leak"] is False   # 20% дрейф вне допуска 5%
    assert loose["leak"] is True     # но в допуске 30%
