"""界面配色、字体与 ttk 样式（深色为主）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Dict, Tuple

# --------------------------------------------------------------------------
# 配色
# --------------------------------------------------------------------------
DARK: Dict[str, str] = {
    "bg": "#1b1d24",
    "card": "#252833",
    "card2": "#2e3240",
    "fg": "#e9ecf3",
    "muted": "#98a0b3",
    "border": "#3a3f4f",
    "accent": "#4f8cff",
    "accent_dark": "#3a6fd8",
    "ok": "#37c98b",
    "warn": "#ffb020",
    "danger": "#ff5c5c",
    "danger_dark": "#d84343",
    "entry": "#1f222b",
    "sel": "#39405a",
}

LIGHT: Dict[str, str] = {
    "bg": "#f2f4f8",
    "card": "#ffffff",
    "card2": "#eef1f7",
    "fg": "#1f2330",
    "muted": "#6b7280",
    "border": "#d3d8e3",
    "accent": "#2f6fe4",
    "accent_dark": "#2559bd",
    "ok": "#1fa26c",
    "warn": "#c8830a",
    "danger": "#d64545",
    "danger_dark": "#b33535",
    "entry": "#ffffff",
    "sel": "#d6e2ff",
}

COLORS: Dict[str, str] = dict(DARK)

FONTS: Dict[str, Tuple] = {}
SCALE = 1.0
DPI = 96

_PREFERRED_FAMILIES = ["Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI"]


def dp(value: float) -> int:
    """把 96dpi 下的像素换算成当前缩放下应有的像素。"""
    return max(1, int(round(value * SCALE)))


def init(root: tk.Misc, theme: str = "dark") -> None:
    """初始化缩放、字体与 ttk 样式。必须在建控件之前调用一次。"""
    global SCALE, DPI
    # 就地更新，保证其它模块里 ``from .theme import COLORS`` 拿到的也是新配色
    COLORS.clear()
    COLORS.update(LIGHT if theme == "light" else DARK)

    DPI = _system_dpi(root)
    SCALE = max(1.0, DPI / 96.0)
    try:
        root.tk.call("tk", "scaling", DPI / 72.0)
    except Exception:
        pass

    family = _pick_family(root)
    FONTS.clear()
    FONTS.update({
        "base": (family, 10),
        "small": (family, 9),
        "bold": (family, 10, "bold"),
        "heading": (family, 12, "bold"),
        "title": (family, 15, "bold"),
        "big": (family, 13, "bold"),
        "mono": ("Consolas", 9),
    })
    _configure_styles(root)


def _system_dpi(root: tk.Misc) -> int:
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32")
        try:
            dpi = int(user32.GetDpiForSystem())
            if dpi > 0:
                return dpi
        except Exception:
            pass
        hdc = user32.GetDC(None)
        gdi32 = ctypes.WinDLL("gdi32")
        LOGPIXELSX = 88
        dpi = int(gdi32.GetDeviceCaps(hdc, LOGPIXELSX))
        user32.ReleaseDC(None, hdc)
        if dpi > 0:
            return dpi
    except Exception:
        pass
    try:
        return int(round(root.winfo_fpixels("1i")))
    except Exception:
        return 96


def _pick_family(root: tk.Misc) -> str:
    try:
        available = set(tkfont.families(root))
    except Exception:
        available = set()
    for name in _PREFERRED_FAMILIES:
        if name in available:
            return name
    return "TkDefaultFont"


def _configure_styles(root: tk.Misc) -> None:
    c = COLORS
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    def s(name: str, **kw) -> None:
        try:
            style.configure(name, **kw)
        except Exception:
            pass

    s(".", background=c["bg"], foreground=c["fg"], font=FONTS["base"])
    s("TFrame", background=c["bg"])
    s("Card.TFrame", background=c["card"])
    s("Card2.TFrame", background=c["card2"])
    s("Body.TFrame", background=c["card"])
    s("TLabel", background=c["bg"], foreground=c["fg"], font=FONTS["base"])
    s("Card.TLabel", background=c["card"], foreground=c["fg"], font=FONTS["base"])
    s("Head.TLabel", background=c["card"], foreground=c["fg"], font=FONTS["heading"])
    s("Title.TLabel", background=c["bg"], foreground=c["fg"], font=FONTS["title"])
    s("Hint.TLabel", background=c["card"], foreground=c["muted"], font=FONTS["small"])
    s("HintBg.TLabel", background=c["bg"], foreground=c["muted"], font=FONTS["small"])
    s("Ok.TLabel", background=c["card"], foreground=c["ok"], font=FONTS["small"])
    s("Warn.TLabel", background=c["card"], foreground=c["warn"], font=FONTS["small"])
    s("Danger.TLabel", background=c["card"], foreground=c["danger"], font=FONTS["small"])
    s("Mono.TLabel", background=c["card"], foreground=c["fg"], font=FONTS["mono"])

    s("TButton", font=FONTS["base"], padding=(dp(10), dp(5)))
    s("Small.TButton", font=FONTS["small"], padding=(dp(7), dp(3)))
    s("Accent.TButton", font=FONTS["base"], padding=(dp(12), dp(6)))

    s("TEntry", fieldbackground=c["entry"], foreground=c["fg"], insertcolor=c["fg"],
      bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], padding=dp(4))
    s("TCombobox", fieldbackground=c["entry"], background=c["entry"], foreground=c["fg"],
      arrowcolor=c["fg"], bordercolor=c["border"], padding=dp(3))
    root.option_add("*TCombobox*Listbox.background", c["card2"])
    root.option_add("*TCombobox*Listbox.foreground", c["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
    root.option_add("*TCombobox*Listbox.font", FONTS["base"])

    s("TCheckbutton", background=c["card"], foreground=c["fg"], font=FONTS["base"],
      focuscolor=c["card"], indicatorcolor=c["entry"], bordercolor=c["border"],
      lightcolor=c["border"], darkcolor=c["border"], padding=(dp(2), dp(2)))
    style.map("TCheckbutton",
              indicatorcolor=[("selected", c["accent"]), ("!selected", c["entry"])],
              background=[("active", c["card"])])
    s("Card.TCheckbutton", background=c["card"], foreground=c["fg"], font=FONTS["base"],
      focuscolor=c["card"], indicatorcolor=c["entry"], bordercolor=c["border"],
      lightcolor=c["border"], darkcolor=c["border"], padding=(dp(2), dp(2)))
    style.map("Card.TCheckbutton",
              indicatorcolor=[("selected", c["accent"]), ("!selected", c["entry"])],
              background=[("active", c["card"])])
    s("TRadiobutton", background=c["card"], foreground=c["fg"], font=FONTS["base"],
      focuscolor=c["card"], indicatorcolor=c["entry"], bordercolor=c["border"],
      lightcolor=c["border"], darkcolor=c["border"])
    style.map("TRadiobutton",
              indicatorcolor=[("selected", c["accent"]), ("!selected", c["entry"])],
              background=[("active", c["card"])])
    s("TScale", background=c["card"], troughcolor=c["entry"], bordercolor=c["card"],
      lightcolor=c["accent"], darkcolor=c["accent"])
    s("Horizontal.TScale", background=c["card"], troughcolor=c["entry"])

    s("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(dp(6), dp(4), dp(6), 0))
    s("TNotebook.Tab", background=c["card"], foreground=c["muted"], padding=(dp(16), dp(8)),
      font=FONTS["base"], borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", c["accent"]), ("active", c["card2"])],
              foreground=[("selected", "#ffffff"), ("active", c["fg"])])

    s("TLabelframe", background=c["card"], foreground=c["fg"], bordercolor=c["border"])
    s("TLabelframe.Label", background=c["card"], foreground=c["muted"], font=FONTS["small"])
    s("TSeparator", background=c["border"])
    s("Card.TSeparator", background=c["border"])

    s("Vertical.TScrollbar", background=c["card2"], troughcolor=c["bg"],
      bordercolor=c["bg"], arrowcolor=c["fg"])
    s("Horizontal.TScrollbar", background=c["card2"], troughcolor=c["bg"],
      bordercolor=c["bg"], arrowcolor=c["fg"])
    s("TProgressbar", background=c["accent"], troughcolor=c["entry"], bordercolor=c["entry"])


def tk_button(parent, text: str, command=None, kind: str = "normal", **kw) -> tk.Button:
    """统一样式的经典按钮（比 ttk 更容易上色）。"""
    c = COLORS
    palette = {
        "normal": (c["card2"], c["fg"]),
        "accent": (c["accent"], "#ffffff"),
        "ok": (c["ok"], "#ffffff"),
        "danger": (c["danger"], "#ffffff"),
        "ghost": (c["card"], c["muted"]),
        "warn": (c["warn"], "#20242e"),
    }.get(kind, (c["card2"], c["fg"]))
    bg, fg = palette
    font = kw.pop("font", FONTS.get("base", ("Segoe UI", 10)))
    padx = kw.pop("padx", dp(12))
    pady = kw.pop("pady", dp(6))
    button = tk.Button(
        parent, text=text, command=command, bg=bg, fg=fg,
        activebackground=bg, activeforeground=fg,
        relief="flat", bd=0, highlightthickness=0, cursor="hand2",
        font=font, padx=padx, pady=pady, **kw)
    hover = _lighter(bg, 1.18) if c is DARK else _darker(bg, 0.94)
    button.bind("<Enter>", lambda _e: button.configure(bg=hover))
    button.bind("<Leave>", lambda _e: button.configure(bg=bg))
    return button


def _clamp(value: float) -> int:
    return max(0, min(255, int(value)))


def _shade(hex_color: str, factor: float) -> str:
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return "#" + hex_color
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (_clamp(r * factor), _clamp(g * factor), _clamp(b * factor))


def _lighter(hex_color: str, factor: float) -> str:
    return _shade(hex_color, factor)


def _darker(hex_color: str, factor: float) -> str:
    return _shade(hex_color, factor)
