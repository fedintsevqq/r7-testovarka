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

- Python 3.9+ (64-bit)
- Установленные зависимости:

```cmd
pip install -r requirements.txt
pip install pyinstaller
```

---

## Быстрая сборка (одна команда)

```cmd
pyinstaller --onefile --name="R7-Testovarka" --uac-admin --console ^
  --hidden-import=win32gui ^
  --hidden-import=win32con ^
  --hidden-import=win32api ^
  --hidden-import=pywintypes ^
  r7_Testovarka.py
```

---

## Сборка через spec-файл (рекомендуется)

```cmd
pyinstaller R7-Testovarka.spec
```

Результат окажется в папке `dist\R7-Testovarka.exe`.

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
