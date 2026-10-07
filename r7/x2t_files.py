"""Файл экспорта и конвертер x2t: проверка формата записанного файла,
учёт запусков x2t, дампы его падений и временные файлы.

X2tFilesMixin — методы, которые R7Testovarka получает наследованием.
"""
import os
import statistics
import time
import winreg
from pathlib import Path
from r7 import env
from r7.processes import X2tTracker


class X2tFilesMixin:
    """Проверка файла экспорта и учёт x2t — часть R7Testovarka."""

    EXPORT_LOCK_WAIT_SEC         = 5.0    # файл экспорта ещё держит Р7/x2t — ждать до
                                          # проверки формата (вне замера, эталон 06.10.2026)


    def _aggregate_x2t(self, run_x2t, idx, log_cb=None):
        """Сводка x2t по операции: медиана длительности конвертации по
        прогонам статистики и все упавшие запуски.

        Returns:
            dict | None: None — x2t в операции не запускался.
        """
        if not any(r["count"] for r in run_x2t):
            return None
        rows = [run_x2t[i] for i in idx if i < len(run_x2t)] or run_x2t
        failed = [c for r in run_x2t for c in r["failed_codes"]]
        agg = {"sec": round(statistics.median(r["sec"] for r in rows), 3),
               "cpu_sec": round(statistics.median(r["cpu_sec"] for r in rows), 3),
               "runs_per_rep": [r["count"] for r in run_x2t],
               "failed_codes": failed,
               "formats": sorted({f for r in run_x2t for f in r["formats"]})}
        if log_cb is not None:
            log_cb(f"   🔧 x2t: медиана конвертации {agg['sec']:.2f} с "
                   f"(CPU {agg['cpu_sec']:.2f} с), запусков на прогон "
                   f"{agg['runs_per_rep']}"
                   + (f"; УПАЛ с кодами {', '.join(failed)}" if failed else ""))
        return agg

    def _x2t(self, log_cb=None):
        """Отслеживатель x2t на всё время работы приложения (X2tTracker).

        Запускается лениво, при первом запуске Р7, и дальше работает в фоне
        (опрос ~1 мс раз в 50 мс). log_cb обновляется на каждый вызов — Batch
        и вкладка «Производительность» пишут в разные логи.
        """
        tracker = getattr(self, "_x2t_tracker", None)
        if tracker is None or not tracker.is_alive():
            if not env.PSUTIL_OK:
                return None
            tracker = X2tTracker(log_cb=log_cb or self.add_test_log)
            tracker.start()
            self._x2t_tracker = tracker
        elif log_cb is not None:
            tracker.log_cb = log_cb
        return tracker

    @classmethod
    def _check_export_format(cls, path, ext):
        """Совпадает ли содержимое файла экспорта с форматом ext.

        «Файл записан» значило только «размер > 0 и не растёт»; расширение в
        имени на формат не влияет (см. _uia_select_saveas_type), и если выбор
        типа в диалоге промахнулся, файл другого формата проходил как OK, а
        его время — как время экспорта (аудит 06.10.2026). Читаются только
        начало файла и каталог zip — миллисекунды даже на десятках МБ.

        Дописанный файл Р7/x2t иногда ещё держат открытым: чтение давало
        PermissionError, и это выдавалось за «формат не тот» (эталонный прогон
        06.10.2026, XLTX). Такой файл ждём до EXPORT_LOCK_WAIT_SEC — замер уже
        закончен по mtime, ожидание в цифру не попадает, — а не дождались —
        честное «не проверить», а не вердикт о формате.

        Returns:
            tuple[bool | None, str]: результат и пояснение; None — файл так и
            не удалось прочитать, формат не проверен.
        """
        import zipfile
        path = Path(path)
        deadline = time.perf_counter() + cls.EXPORT_LOCK_WAIT_SEC
        while True:
            try:
                return cls._check_export_format_once(path, ext, zipfile)
            except PermissionError as e:
                if time.perf_counter() >= deadline:
                    return None, (f"файл занят другим процессом "
                                  f"{cls.EXPORT_LOCK_WAIT_SEC:.0f} с: {e}")
                time.sleep(0.2)
            except OSError as e:
                return None, f"не прочитать: {e}"

    @staticmethod
    def _check_export_format_once(path, ext, zipfile):
        """Одна попытка _check_export_format; PermissionError и прочие
        OSError открытия уходят наверх."""
        with open(path, "rb") as f:
            head = f.read(4096)
        is_zip = head.startswith(b"PK\x03\x04")
        if ext == "pdf":
            return (head.startswith(b"%PDF-"),
                    "PDF" if head.startswith(b"%PDF-") else f"начало {head[:8]!r}, а не %PDF-")
        if ext in ("ods", "xltx", "xlsx", "odt", "docx"):
            if not is_zip:
                return False, f"не zip (начало {head[:8]!r})"
            try:
                with zipfile.ZipFile(path) as z:
                    names = set(z.namelist())
                    if ext in ("ods", "odt"):
                        mime = (z.read("mimetype").decode("ascii", "replace").strip()
                                if "mimetype" in names else "")
                        kind = "spreadsheet" if ext == "ods" else "text"
                        ok = mime == f"application/vnd.oasis.opendocument.{kind}"
                        return ok, mime or "нет mimetype — не OpenDocument"
                    types = (z.read("[Content_Types].xml").decode("utf-8", "replace")
                             if "[Content_Types].xml" in names else "")
            except PermissionError:
                raise
            except (zipfile.BadZipFile, KeyError, OSError) as e:
                return False, f"битый zip: {e}"
            if ext == "docx":
                # Документ (этап 5): тип основной части — wordprocessingml.
                if "wordprocessingml.document.main" in types:
                    return True, "документ Word"
                return False, "нет типа содержимого документа Word"
            if "spreadsheetml.template.main" in types:
                return (ext == "xltx",
                        "шаблон Excel" if ext == "xltx" else "шаблон xltx, а не книга")
            if "spreadsheetml.sheet.main" in types:
                # xlsx — экспорт корпуса (r7/corpus.py); у вкладки его нет.
                return (ext == "xlsx",
                        "книга Excel" if ext == "xlsx" else "обычная книга xlsx, а не шаблон")
            return False, "нет типа содержимого книги Excel"
        if ext == "csv":
            if is_zip or head.startswith(b"%PDF-"):
                return False, "двоичный файл (zip/PDF), а не текст"
            if head.startswith((b"\xff\xfe", b"\xfe\xff")):
                # UTF-16 с BOM: нули в нём — половинки символов, а не двоичные
                # данные (кодировку можно сменить в диалоге параметров CSV).
                return True, "текст UTF-16"
            if b"\x00" in head:
                return False, "двоичные нули — не текст CSV"
            return True, "текст"
        return True, "формат не проверяется"

    @staticmethod
    def _crash_dump_dir():
        """Папка, куда Windows пишет дампы упавших процессов.

        По умолчанию %LOCALAPPDATA%\\CrashDumps; её можно переопределить
        параметром DumpFolder в HKLM\\...\\Windows Error Reporting\\LocalDumps.
        """
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps")
            try:
                folder, _t = winreg.QueryValueEx(key, "DumpFolder")
            finally:
                winreg.CloseKey(key)
            if folder:
                return Path(os.path.expandvars(folder))
        except OSError:  # ключа LocalDumps нет — Windows пишет в папку по умолчанию
            pass
        return Path(os.environ.get("LOCALAPPDATA", ".")) / "CrashDumps"

    def _cleanup_x2t_crash_dumps(self, log_cb=None):
        """Удаляет дампы упавших x2t, чьё падение зафиксировал этот запуск.

        При каждом падении конвертера Windows сохраняет дамп процесса
        (x2t.exe.<PID>.dmp, ~272 МБ). Экспорт в ODS рабочей фикстуры падает
        всегда (DE-8304), и за день прогонов в CrashDumps набралось 10 дампов,
        2.6 ГБ на почти заполненном диске C: (30.09.2026). Код падения уже
        записан в отчёт (results[...]["x2t"]["failed_codes"]), для замеров
        дамп не нужен.

        Удаляются только файлы с PID процессов, которые X2tTracker видел
        упавшими — чужие дампы и дампы прошлых сессий не трогаются. Дамп,
        который Windows ещё дописывает, пропускается и удалится при следующей
        очистке.

        Returns:
            int: сколько файлов удалено.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        tracker = getattr(self, "_x2t_tracker", None)
        if tracker is None:
            return 0
        try:
            with tracker._lock:
                pids = {r["pid"] for r in tracker.runs
                        if r.get("exit_code") not in (None, 0)}
        except Exception:
            return 0
        pids -= getattr(self, "_x2t_dumps_removed", set())
        if not pids:
            return 0
        dump_dir = self._crash_dump_dir()
        removed, freed = 0, 0
        done = getattr(self, "_x2t_dumps_removed", None)
        if done is None:
            done = self._x2t_dumps_removed = set()
        for pid in pids:
            for dump in dump_dir.glob(f"x2t*.{pid}.dmp"):
                try:
                    size = dump.stat().st_size
                    dump.unlink()
                    removed += 1
                    freed += size
                    done.add(pid)
                except OSError:
                    pass    # Windows ещё пишет дамп — удалим при следующей очистке
        if removed:
            log_cb(f"🧹 Удалены дампы упавшего конвертера x2t: {removed} шт., "
                   f"{freed / 2**20:.0f} МБ (код падения — в отчёте)")
        return removed

    def _cleanup_x2t_temp_pdfs(self, log_cb=None):
        """Removes leftover temp_export_x2t_* files from %TEMP%.

        Name kept as-is (not renamed to _temp_exports) since it's referenced
        by tests/manual_cdp_smoke.py and this repo's own docs — L2 (этап 3)
        only widened the glob to the three formats added alongside PDF.

        Also matches the double-extension form `temp_export_x2t_*.<ext>.xlsx`:
        confirmed live (25.08.2026) that when the «Сохранить как» dialog's
        «Тип файла» selector stays on XLSX (its default for an .xlsx source
        document), typing a .ods/.csv/.xltx name into the filename field does
        NOT switch the selector — Р7 saves a plain XLSX copy and appends
        .xlsx on top of the typed extension. Without this glob those ~34 МБ
        copies of the source file silently accumulated in %TEMP% every run
        (11 of them found on this stand, ~370 МБ) since the narrower pattern
        never matched them.

        Safe to call even if save_as_format never ran or failed mid-save —
        glob simply matches nothing in that case.

        Args:
            log_cb: Callable for error logging; defaults to self.add_test_log.
        """
        if log_cb is None:
            log_cb = self.add_test_log
        self._cleanup_x2t_crash_dumps(log_cb=log_cb)
        temp_dir = Path(os.environ.get("TEMP", "."))
        # xlsx — экспорт корпуса (r7/corpus.py). Двойное расширение — копия
        # исходника в его формате (xlsx у таблиц, docx у документов), если тип
        # в диалоге не переключился.
        for ext in ("pdf", "ods", "csv", "xltx", "xlsx", "docx", "odt"):
            for pattern in (f"temp_export_x2t_*.{ext}", f"temp_export_x2t_*.{ext}.xlsx",
                            f"temp_export_x2t_*.{ext}.docx"):
                for leftover in temp_dir.glob(pattern):
                    # Ошибка на одном файле не должна оставлять остальные.
                    try:
                        leftover.unlink(missing_ok=True)
                    except OSError as e:
                        # До закрытия Р7 файл последнего экспорта занят — его
                        # удалит повторная очистка после закрытия.
                        if getattr(e, "winerror", None) != 32:
                            log_cb(f"⚠️ Не удалось удалить временный файл экспорта: {e}")
