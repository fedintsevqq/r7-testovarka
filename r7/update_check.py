"""Проверка новой версии инструмента в GitHub Releases.

check_for_update(current) спрашивает у api.github.com последний релиз и
возвращает {"latest": "1.2.0", "url": ...}, если его тег новее current,
иначе None. Любая ошибка — нет сети, таймаут, не тот JSON, странный тег —
тоже None: функция никогда не бросает и не должна ронять или задерживать
интерфейс. Вызывать из фонового daemon-потока; результат в виджеты — через
_ui_call (см. MainWindowMixin._start_update_check).

Сравниваются только теги вида vX.Y.Z (ведущая «v» необязательна).
Предварительные версии (v1.2.0-rc1, v1.2.0b1), черновики и релизы с
пометкой prerelease пропускаются: ссылку на них показывать не надо.

Модуль не импортирует tkinter. requests — необязателен (как и у коннектора
CDP): без него берётся urllib из стандартной библиотеки.
"""
import json
import re
import urllib.request

from r7.version import __version__

try:
    import requests
except ImportError:
    requests = None

RELEASES_API_URL = "https://api.github.com/repos/fedintsevqq/r7-testovarka/releases/latest"
DEFAULT_TIMEOUT_SEC = 3.0
_HEADERS = {"Accept": "application/vnd.github+json",
            "User-Agent": f"R7-Testovarka/{__version__}"}
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def parse_version(tag):
    """'v1.2.0' / '1.2.0' → (1, 2, 0); всё остальное (пусто, префикс
    или суффикс вроде '-rc1', две компоненты) → None."""
    m = _SEMVER_RE.match(str(tag or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(latest_tag, current):
    """True, если latest_tag — корректный тег и он строго новее current.
    Неразборчивый тег или current — False."""
    latest, cur = parse_version(latest_tag), parse_version(current)
    if latest is None or cur is None:
        return False
    return latest > cur


def fetch_latest_release(timeout=DEFAULT_TIMEOUT_SEC):
    """GET последнего релиза: dict из JSON GitHub. Ошибки сети и разбора
    пробрасываются — их глушит check_for_update."""
    if requests is not None:
        resp = requests.get(RELEASES_API_URL, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    req = urllib.request.Request(RELEASES_API_URL, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_update(current=__version__, timeout=DEFAULT_TIMEOUT_SEC, fetch=None):
    """{"latest": "X.Y.Z", "url": html_url} при более новом релизе, иначе None.
    Никогда не бросает. fetch — подмена сетевого вызова для тестов."""
    fetch = fetch or fetch_latest_release
    try:
        release = fetch(timeout=timeout)
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            return None
        tag = release.get("tag_name")
        if not is_newer(tag, current):
            return None
        return {"latest": ".".join(str(x) for x in parse_version(tag)),
                "url": str(release.get("html_url") or "")}
    except Exception:
        # Нет сети, таймаут, HTTP-ошибка, не JSON — проверка молча пропускается:
        # интерфейс от неё не зависит, а журналить «нет интернета» при каждом
        # запуске на стенде без сети — шум.
        return None
