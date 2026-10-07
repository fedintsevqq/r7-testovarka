"""Значки интерфейса — глифы шрифта Windows Segoe MDL2 Assets.

Эмодзи в подписях кнопок рисовались по-разному в разных местах (цветные,
монохромные, разного размера). Здесь значок — картинка одного стиля и
размера в цвет текста темы: глиф рисуется Pillow в двойном размере и
уменьшается, чтобы края были гладкими. Нет Pillow или шрифта (Windows Server
без MDL2, урезанный стенд) — значков нет, кнопки остаются с текстом.
"""
from pathlib import Path

from r7 import windows

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    PIL_OK = True
except ImportError:
    PIL_OK = False

FONT_PATH = Path(r"C:\Windows\Fonts\segmdl2.ttf")

GLYPHS = {
    "play": 0xE768, "stop": 0xE71A, "pause": 0xE769,
    "install": 0xE896, "refresh": 0xE72C, "folder": 0xE838, "add": 0xE710,
    "hashes": 0xE72E, "batch": 0xE81E, "compare": 0xE9F9, "trends": 0xE9D2,
    "files": 0xE7C3, "versions": 0xE7B8, "perf": 0xEC4A, "clear": 0xE74D,
    "theme": 0xE9A9, "edit": 0xE70F, "save": 0xE74E, "check": 0xE73E,
    "uncheck": 0xE711, "report": 0xE9F9, "scenarios": 0xE9D9,
}

BASE_PX = 16          # размер значка при масштабе экрана 100 %


def scale_factor():
    """Масштаб экрана Windows (1.0 = 100 %), чтобы значки не мельчили на 150 %."""
    try:
        return max(1.0, windows.display_scale_factor() / 100)
    except Exception:  # нет shcore (старая Windows) — масштаб 100 %
        return 1.0


def available():
    return PIL_OK and FONT_PATH.exists()


def render(name, color, px):
    """PIL-картинка глифа name цвета color размером px×px, или None."""
    if not available() or name not in GLYPHS:
        return None
    big = px * 2
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    font = ImageFont.truetype(str(FONT_PATH), int(big * 0.9))
    draw = ImageDraw.Draw(img)
    glyph = chr(GLYPHS[name])
    left, top, right, bottom = draw.textbbox((0, 0), glyph, font=font)
    draw.text(((big - (right - left)) / 2 - left, (big - (bottom - top)) / 2 - top),
              glyph, font=font, fill=color)
    return img.resize((px, px), Image.LANCZOS)


class IconSet:
    """Кэш значков для одного окна Tk: Tk держит картинку, пока на неё есть
    ссылка в Python, поэтому все PhotoImage живут здесь."""

    def __init__(self, master, px=None):
        self.master = master
        self.px = px or round(BASE_PX * scale_factor())
        self._cache = {}

    def get(self, name, color):
        key = (name, color)
        if key not in self._cache:
            img = render(name, color, self.px)
            self._cache[key] = (ImageTk.PhotoImage(img, master=self.master)
                                if img is not None else None)
        return self._cache[key]
