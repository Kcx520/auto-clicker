"""可复用界面组件。"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import theme
from .theme import COLORS, FONTS, dp


# --------------------------------------------------------------------------
# 卡片
# --------------------------------------------------------------------------
class Card(ttk.Frame):
    """带标题的圆角卡片容器；内容加到 ``self.body``。"""

    def __init__(self, parent, title: str = "", subtitle: str = "", padding: int = 14, **kw):
        super().__init__(parent, style="Card.TFrame", **kw)
        self.columnconfigure(0, weight=1)
        pad = dp(padding)
        row = 0
        if title:
            head = ttk.Label(self, text=title, style="Head.TLabel")
            head.grid(row=row, column=0, sticky="w", padx=pad, pady=(pad, dp(2)))
            row += 1
        if subtitle:
            sub = ttk.Label(self, text=subtitle, style="Hint.TLabel", wraplength=dp(760),
                            justify="left")
            sub.grid(row=row, column=0, sticky="w", padx=pad, pady=(0, dp(4)))
            row += 1
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.body.grid(row=row, column=0, sticky="nsew", padx=pad, pady=(dp(4), pad))
        self.body.columnconfigure(1, weight=1)
        self.rowconfigure(row, weight=1)

    def grid_row(self) -> int:
        return self.body.grid_size()[1]


# --------------------------------------------------------------------------
# 滚动容器
# --------------------------------------------------------------------------
class ScrollFrame(ttk.Frame):
    """竖直滚动容器；内容加到 ``self.inner``。"""

    def __init__(self, parent, **kw):
        super().__init__(parent, style="TFrame", **kw)
        self.canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self, style="TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.inner.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel(True))
        self.canvas.bind("<Leave>", lambda _e: self._bind_wheel(False))

    def _on_inner(self, _event=None) -> None:
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except Exception:
            pass

    def _on_canvas(self, event) -> None:
        try:
            self.canvas.itemconfigure(self._window, width=event.width)
        except Exception:
            pass

    def _bind_wheel(self, active: bool) -> None:
        if active:
            self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        else:
            self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass


# --------------------------------------------------------------------------
# 提示气泡
# --------------------------------------------------------------------------
class Tooltip:
    def __init__(self, widget: tk.Misc, text: str, delay: int = 450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        if self.text:
            self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _show(self) -> None:
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + dp(12)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + dp(6)
        except Exception:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        self._tip.attributes("-topmost", True)
        frame = tk.Frame(self._tip, bg=COLORS["border"], bd=0)
        frame.pack()
        tk.Label(frame, text=self.text, bg=COLORS["card2"], fg=COLORS["fg"],
                 font=FONTS["small"], justify="left", padx=dp(8), pady=dp(5),
                 wraplength=dp(420)).pack(padx=1, pady=1)

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


def add_help(widget: tk.Misc, text: str) -> tk.Misc:
    if text:
        Tooltip(widget, text)
    return widget


# --------------------------------------------------------------------------
# 表单行
# --------------------------------------------------------------------------
def row(parent, pady: int = 3) -> ttk.Frame:
    frame = ttk.Frame(parent, style="Card.TFrame")
    frame.pack(fill="x", pady=dp(pady))
    return frame


class NumberField(ttk.Frame):
    """数字输入框（带范围校验）。"""

    def __init__(self, parent, label: str, value: float = 0, from_: float = 0,
                 to: float = 1e9, kind: str = "int", width: int = 8,
                 suffix: str = "", help: str = "", decimals: int = 2):
        super().__init__(parent, style="Card.TFrame")
        self.kind = kind
        self.from_, self.to = from_, to
        self.decimals = decimals
        if label:
            name = ttk.Label(self, text=label, style="Card.TLabel")
            name.pack(side="left")
            add_help(name, help)
        self.var = tk.StringVar(value=self._fmt(value))
        self.entry = ttk.Entry(self, textvariable=self.var, width=width, justify="center")
        self.entry.pack(side="left", padx=dp(4))
        self.entry.bind("<FocusOut>", self._normalize)
        self.entry.bind("<Return>", self._normalize)
        if suffix:
            ttk.Label(self, text=suffix, style="Hint.TLabel").pack(side="left")
        if help:
            add_help(self.entry, help)

    def _fmt(self, value: Any) -> str:
        try:
            if self.kind == "int":
                return str(int(round(float(value))))
            return f"{float(value):.{self.decimals}f}".rstrip("0").rstrip(".")
        except Exception:
            return "0"

    def _normalize(self, _event=None) -> None:
        self.set(self.get())

    def get(self) -> float:
        try:
            value = float(self.var.get().strip())
        except Exception:
            value = self.from_
        value = max(self.from_, min(self.to, value))
        return float(int(round(value))) if self.kind == "int" else float(value)

    def get_int(self) -> int:
        return int(round(self.get()))

    def set(self, value: Any) -> None:
        try:
            value = max(self.from_, min(self.to, float(value)))
        except Exception:
            value = self.from_
        self.var.set(self._fmt(value))


class TextField(ttk.Frame):
    def __init__(self, parent, label: str = "", value: str = "", width: int = 18,
                 help: str = "", show: Optional[str] = None):
        super().__init__(parent, style="Card.TFrame")
        if label:
            name = ttk.Label(self, text=label, style="Card.TLabel")
            name.pack(side="left")
            add_help(name, help)
        self.var = tk.StringVar(value=value)
        self.entry = ttk.Entry(self, textvariable=self.var, width=width, show=show)
        self.entry.pack(side="left", padx=dp(4))
        if help:
            add_help(self.entry, help)

    def get(self) -> str:
        return self.var.get()

    def set(self, value: str) -> None:
        self.var.set(value)


class ComboField(ttk.Frame):
    """下拉框；``values`` 是显示文本，``keys`` 是对应的内部值。"""

    def __init__(self, parent, label: str, values: Sequence[str],
                 keys: Optional[Sequence[str]] = None, value: str = "",
                 width: int = 14, help: str = "", command: Optional[Callable[[str], None]] = None):
        super().__init__(parent, style="Card.TFrame")
        self.keys: List[str] = list(keys) if keys else list(values)
        self.values: List[str] = list(values)
        if label:
            name = ttk.Label(self, text=label, style="Card.TLabel")
            name.pack(side="left")
            add_help(name, help)
        self.var = tk.StringVar(value=self.label_for(value))
        self.combo = ttk.Combobox(self, textvariable=self.var, values=self.values,
                                  state="readonly", width=width)
        self.combo.pack(side="left", padx=dp(4))
        self._command = command
        self.combo.bind("<<ComboboxSelected>>", self._on_select)
        if help:
            add_help(self.combo, help)

    def label_for(self, key: str) -> str:
        try:
            index = self.keys.index(key)
            return self.values[index]
        except Exception:
            return self.values[0] if self.values else ""

    def _on_select(self, _event=None) -> None:
        if self._command:
            self._command(self.get())

    def get(self) -> str:
        try:
            return self.keys[self.values.index(self.var.get())]
        except Exception:
            return self.keys[0] if self.keys else ""

    def set(self, key: str) -> None:
        self.var.set(self.label_for(key))


class CheckField(ttk.Frame):
    def __init__(self, parent, label: str, value: bool = False, help: str = "",
                 command: Optional[Callable[[bool], None]] = None):
        super().__init__(parent, style="Card.TFrame")
        self.var = tk.BooleanVar(value=bool(value))
        self.check = ttk.Checkbutton(self, text=label, variable=self.var,
                                     style="Card.TCheckbutton", command=self._changed)
        self.check.pack(side="left")
        if help:
            add_help(self.check, help)
        self._command = command

    def _changed(self) -> None:
        if self._command:
            self._command(self.get())

    def get(self) -> bool:
        return bool(self.var.get())

    def set(self, value: bool) -> None:
        self.var.set(bool(value))


class SliderField(ttk.Frame):
    """滑块 + 数字框联动。"""

    def __init__(self, parent, label: str, value: float = 0, from_: float = 0,
                 to: float = 100, resolution: float = 1, kind: str = "int",
                 suffix: str = "", help: str = "", width: int = 6):
        super().__init__(parent, style="Card.TFrame")
        self.kind = kind
        if label:
            name = ttk.Label(self, text=label, style="Card.TLabel", width=12, anchor="w")
            name.pack(side="left")
            add_help(name, help)
        self.scale_var = tk.DoubleVar(value=float(value))
        self.scale = ttk.Scale(self, from_=from_, to=to, variable=self.scale_var,
                               orient="horizontal", length=dp(180), command=self._on_scale)
        self.scale.pack(side="left", padx=dp(4))
        self.number = NumberField(self, "", value, from_, to, kind=kind, width=width,
                                  suffix=suffix, help=help)
        self.number.pack(side="left")

    def _on_scale(self, _value=None) -> None:
        self.number.set(self.scale_var.get())

    def get(self) -> float:
        return self.number.get()

    def get_int(self) -> int:
        return self.number.get_int()

    def set(self, value: float) -> None:
        try:
            self.scale_var.set(float(value))
        except Exception:
            pass
        self.number.set(value)


class Collapsible(ttk.Frame):
    """可折叠的高级设置区。"""

    def __init__(self, parent, title: str = "高级设置", expanded: bool = False):
        super().__init__(parent, style="Card.TFrame")
        self.columnconfigure(0, weight=1)
        self._open = tk.BooleanVar(value=expanded)
        self.toggle = tk.Button(
            self, text=self._text(title), anchor="w", relief="flat", bd=0,
            bg=COLORS["card"], fg=COLORS["accent"], activebackground=COLORS["card"],
            activeforeground=COLORS["accent"], cursor="hand2", font=FONTS["bold"],
            command=self._toggle)
        self.toggle.grid(row=0, column=0, sticky="ew", pady=dp(2))
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.body.columnconfigure(1, weight=1)
        self._title = title
        if expanded:
            self.body.grid(row=1, column=0, sticky="ew")

    def _text(self, title: str) -> str:
        return ("▼  " if self._open.get() else "▶  ") + title

    def _toggle(self) -> None:
        self._open.set(not self._open.get())
        self.toggle.configure(text=self._text(self._title))
        if self._open.get():
            self.body.grid(row=1, column=0, sticky="ew")
        else:
            self.body.grid_remove()


# --------------------------------------------------------------------------
# 状态灯 / 日志
# --------------------------------------------------------------------------
class StatusDot(tk.Canvas):
    def __init__(self, parent, size: int = 14, **kw):
        super().__init__(parent, width=dp(size), height=dp(size), highlightthickness=0,
                         bg=COLORS["bg"], **kw)
        self._size = size
        self._dot = self.create_oval(dp(1), dp(1), dp(size - 1), dp(size - 1),
                                     fill=COLORS["muted"], outline="")

    def set_state(self, state: str) -> None:
        color = {
            "running": COLORS["ok"],
            "paused": COLORS["warn"],
            "stopped": COLORS["muted"],
            "error": COLORS["danger"],
            "waiting": COLORS["accent"],
        }.get(state, COLORS["muted"])
        try:
            self.itemconfigure(self._dot, fill=color)
        except Exception:
            pass


class LogBox(ttk.Frame):
    """线程安全的日志框：任何线程都能调 ``log()``。"""

    def __init__(self, parent, height: int = 6):
        super().__init__(parent, style="Card.TFrame")
        self._queue: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self.text = tk.Text(self, height=height, wrap="word", bd=0, relief="flat",
                            bg=COLORS["entry"], fg=COLORS["fg"], insertbackground=COLORS["fg"],
                            font=FONTS["mono"], padx=dp(8), pady=dp(6))
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=self.scroll.set, state="disabled")
        self.text.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.text.tag_configure("info", foreground=COLORS["fg"])
        self.text.tag_configure("ok", foreground=COLORS["ok"])
        self.text.tag_configure("warn", foreground=COLORS["warn"])
        self.text.tag_configure("error", foreground=COLORS["danger"])
        self.text.tag_configure("time", foreground=COLORS["muted"])
        self._auto = True
        self._drain()

    def log(self, message: str, level: str = "info") -> None:
        import time as _time
        self._queue.put((_time.strftime("%H:%M:%S"), f"[{level}] {message}"))

    def clear(self) -> None:
        self._queue.put(("", "__clear__"))

    def _drain(self) -> None:
        try:
            while True:
                stamp, message = self._queue.get_nowait()
                if message == "__clear__":
                    self.text.configure(state="normal")
                    self.text.delete("1.0", "end")
                    self.text.configure(state="disabled")
                    continue
                level = "info"
                body = message
                if message.startswith("["):
                    level, _, body = message[1:].partition("] ")
                self.text.configure(state="normal")
                self.text.insert("end", f"{stamp}  ", "time")
                self.text.insert("end", body + "\n", level)
                self.text.see("end")
                self.text.configure(state="disabled")
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.after(200, self._drain)
        except Exception:
            pass


# --------------------------------------------------------------------------
# 热键录入按钮
# --------------------------------------------------------------------------
class HotkeyButton(ttk.Frame):
    """点一下开始录制，然后按下你想用的键。"""

    def __init__(self, parent, get_value: Callable[[], str],
                 on_capture: Callable[[], None], width: int = 12):
        super().__init__(parent, style="Card.TFrame")
        self.get_value = get_value
        self.on_capture = on_capture
        self.button = tk.Button(self, text=get_value() or "未设置", width=width,
                                bg=COLORS["entry"], fg=COLORS["fg"], relief="flat", bd=0,
                                activebackground=COLORS["card2"], activeforeground=COLORS["fg"],
                                cursor="hand2", font=FONTS["base"], padx=dp(8), pady=dp(4),
                                command=self._start)
        self.button.pack()

    def _start(self) -> None:
        self.button.configure(text="请按键…", bg=COLORS["warn"], fg="#20242e")
        self.on_capture()

    def refresh(self) -> None:
        text = self.get_value() or "未设置"
        if text != "请按键…":
            self.button.configure(text=text, bg=COLORS["entry"], fg=COLORS["fg"])

    def show_captured(self, text: str) -> None:
        self.button.configure(text=text or "未设置", bg=COLORS["entry"], fg=COLORS["fg"])


# --------------------------------------------------------------------------
# 小组件
# --------------------------------------------------------------------------
def heading(parent, text: str) -> ttk.Label:
    return ttk.Label(parent, text=text, style="Head.TLabel")


def hint(parent, text: str, style: str = "Hint.TLabel", wrap: int = 780) -> ttk.Label:
    return ttk.Label(parent, text=text, style=style, wraplength=dp(wrap), justify="left")


def separator(parent) -> ttk.Separator:
    return ttk.Separator(parent, orient="horizontal")


def stat_label(parent, caption: str) -> Tuple[ttk.Frame, tk.StringVar]:
    frame = ttk.Frame(parent, style="Card.TFrame")
    ttk.Label(frame, text=caption, style="Hint.TLabel").pack(anchor="w")
    var = tk.StringVar(value="—")
    ttk.Label(frame, textvariable=var, style="Card.TLabel",
              font=FONTS["big"]).pack(anchor="w")
    return frame, var
