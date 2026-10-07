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
- Зависимости и PyInstaller:

```cmd
pip install -r requirements.txt
pip install pyinstaller
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

CI собирает .exe на каждый PR и push в main (`.github/workflows/build.yml`),
прогоняет `--self-check` и выкладывает файл артефактом `R7-Testovarka-exe`.

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
