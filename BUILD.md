# Сборка R7-Testovarka в .exe (портативный режим)

## Структура после сборки

```
R7-Testovarka\
├── R7-Testovarka.exe   ← единственный исполняемый файл
├── Distributives\      ← дистрибутивы .msi / .exe  (создаётся автоматически)
├── TestFiles\          ← тестовые .xlsx файлы       (создаётся автоматически)
└── Reports\            ← HTML-отчёты и JSON данные  (создаётся автоматически)
```

Папки `TestFiles` и `Reports` создаются программой сама при первом запуске.
`Distributives` тоже создаётся автоматически, но дистрибутивы нужно положить вручную.

---

## Требования

- Python 3.11–3.14 (64-bit)
- Зависимости и PyInstaller (в `requirements-dev.lock` есть и PyInstaller, и pip-audit):

```cmd
pip install --require-hashes -r requirements-dev.lock
```

---

## Сборка

```cmd
pyinstaller --noconfirm R7-Testovarka.spec
dist\R7-Testovarka.exe --self-check
```

`R7-Testovarka.spec` лежит в репозитории: в нём пакет `r7` целиком
(`collect_submodules`) и папка `templates` с шаблонами HTML-отчётов — без неё
собранный .exe падал бы на первом отчёте. Прямой вызов `pyinstaller --onefile
r7_Testovarka.py` шаблоны не включает, поэтому сборка — только через spec.

`--self-check` проверяет собранный файл без окна и без прав: все модули пакета
импортируются, шаблоны на месте, пробный отчёт собирается. Код выхода 0 — всё в
порядке.

Рядом с .exe CI кладёт `R7-Testovarka.exe.sha256` — по нему можно сверить скачанный файл.

CI собирает .exe на каждый PR и push в main (`.github/workflows/build.yml`),
прогоняет `--self-check` и выкладывает файл артефактом `R7-Testovarka-exe`.

### Архив для команды и релиз

Тем же workflow собирается `R7-Testovarka-<версия>-win64.zip` (артефакт
`R7-Testovarka-<версия>-win64`): `.exe`, его `.sha256`, `README-first-run.md`
(`docs/rollout/`), `LICENSE` и пустые папки `Distributives\`, `TestFiles\`,
`Reports\`. Рабочая фикстура (33 МБ) в архив не входит — её создаёт генератор,
см. README в архиве.

Версия — только в `r7/version.py` (`__version__`). Выпуск:

```bat
rem 1. поднять __version__ в r7/version.py, слить в main
rem 2. тег с той же версией — build.yml сверит их и упадёт при расхождении
git tag v1.2.0
git push origin v1.2.0
```

На теге `v*` workflow создаёт GitHub Release (если его ещё нет) и прикладывает
`.exe`, архив, оба `.sha256`, SBOM и лицензии (ниже). Запущенная программа
сравнивает свою версию с последним релизом и показывает в шапке ссылку
«Доступна версия X.Y.Z». Что вошло в версию — `CHANGELOG.md`: перед тегом
перенесите пункты из «Unreleased» под номер версии.

### SBOM и лицензии сторонних пакетов

На каждой сборке (и на PR) рядом с `.exe` появляются:

| Файл | Что в нём |
|---|---|
| `R7-Testovarka-<версия>-sbom.cdx.json` | SBOM в формате CycloneDX 1.6 по `requirements.lock`: все пакеты, из которых собран `.exe`, с версиями и хешами |
| `R7-Testovarka-<версия>-licenses.md` | таблица: пакет, версия, лицензия, ссылка |
| `R7-Testovarka-<версия>-THIRD-PARTY-LICENSES.txt` | полные тексты лицензий |

Инструменты (`cyclonedx-bom`, `pip-licenses`) ставятся из
`requirements-release.lock` с хешами, отдельно от `requirements-dev.lock`:
они тянут `jsonschema` и `lxml`, тестам это не нужно. Лицензии считаются по
чистому окружению из `requirements.lock`, а не по окружению сборки, где есть
ещё PyInstaller и инструменты CI. Пересборка lock-файла:

```bat
uv pip compile requirements-release.in --generate-hashes --universal --python-version 3.11 -o requirements-release.lock
```

Среди зависимостей `pyautogui` есть пакеты под GPLv3+ (`MouseInfo`,
`PyMsgBox`) — это видно в списке лицензий; решение о раздаче вне команды
принимать с учётом этого.

### Подпись .exe

Сертификата пока нет, поэтому `.exe` не подписан, и в сводке сборки стоит
«Подпись .exe: нет». Шаг подписи в `build.yml` уже есть и включается сам,
когда в репозитории заведены секреты:

| Секрет / переменная | Что положить |
|---|---|
| `SIGN_CERT` (секрет) | PFX с сертификатом подписи кода в base64: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx"))` |
| `SIGN_CERT_PASSWORD` (секрет) | пароль PFX |
| `SIGN_TIMESTAMP_URL` (переменная, необязательно) | сервер меток времени RFC 3161; по умолчанию `http://timestamp.digicert.com` |

Шаг берёт `signtool.exe` из Windows SDK раннера, подписывает SHA256 с меткой
времени (подпись живёт и после истечения сертификата), проверяет её
`signtool verify /pa` и удаляет PFX. Подпись идёт до самопроверки и до
подсчёта SHA256, так что хеш в релизе — от подписанного файла. В PR из
форков секретов нет — шаг пропускается.

Сертификат в облачном HSM (Azure Trusted Signing и подобные) в PFX не
выгружается — для него шаг придётся заменить на действие поставщика.

---

## Развёртывание

1. Скопируйте `dist\R7-Testovarka.exe` в любую папку.
2. Создайте рядом папку `Distributives\` и положите туда .msi / .exe дистрибутивы Р7-Офис.
3. Запустите `R7-Testovarka.exe` **от имени администратора** (UAC запросит разрешение автоматически).
4. Папки `TestFiles\` и `Reports\` создадутся при первом запуске.

---

## Примечания

- `.exe` можно перенести на другой компьютер — Python там не нужен.
- Все пути внутри программы относительные: данные всегда рядом с `.exe`.
- Если нужен тихий запуск без консоли, замените `--console` на `--noconsole` или `console=False` в spec.
- UPX в spec выключен (`upx=False`): упакованные неподписанные exe чаще ловят антивирус и SmartScreen.
- Подпись `.exe` — раздел «Подпись .exe» выше: шаг готов, ждёт сертификат.
