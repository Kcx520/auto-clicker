"""迷你悬浮面板：只留开始/停止和计数，半透明置顶，可拖动。"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Dict

from .. import winapi as W
from . import theme
from .theme import COLORS, FONTS, dp


class MiniPanel(tk.Toplevel):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.app = app
        self.cfg = app.cfg
        self.overrideredirect(True)
        self.attributes("-topmost", bool(self.cfg.get("general.mini_on_top", True)))
        try:
            self.attributes("-alpha", 0.92)
        except Exception:
            pass
        self.configure(bg=COLORS["card"])

        frame = tk.Frame(self, bg=COLORS["card"], padx=dp(10), pady=dp(8))
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg=COLORS["card"])
        header.pack(fill="x")
        self.dot = tk.Canvas(header, width=dp(12), height=dp(12), bg=COLORS["card"],
                             highlightthickness=0)
        self.dot.pack(side="left")
        self._circle = self.dot.create_oval(dp(1), dp(1), dp(11), dp(11), fill=COLORS["muted"],
                                            outline="")
        tk.Label(header, text="连点器", bg=COLORS["card"], fg=COLORS["fg"],
                 font=FONTS["bold"]).pack(side="left", padx=dp(6))
        close = tk.Label(header, text="✕", bg=COLORS["card"], fg=COLORS["muted"],
                         font=FONTS["bold"], cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.close())

        self.var_main = tk.StringVar(value="0 次 · 0.0 次/秒")
        tk.Label(frame, textvariable=self.var_main, bg=COLORS["card"], fg=COLORS["fg"],
                 font=FONTS["base"]).pack(pady=(dp(6), dp(4)))

        buttons = tk.Frame(frame, bg=COLORS["card"])
        buttons.pack(fill="x")
        self.btn_toggle = theme.tk_button(buttons, "开始", self.app.toggle_clicking, "accent",
                                          padx=dp(10), pady=dp(5))
        self.btn_toggle.pack(side="left", fill="x", expand=True, padx=(0, dp(4)))
        theme.tk_button(buttons, "停止", lambda: self.app.emergency_stop("迷你面板停止"), "danger",
                        padx=dp(10), pady=dp(5)).pack(side="left", padx=(0, dp(4)))
        theme.tk_button(buttons, "主界面", self.app.show_window, "ghost",
                        padx=dp(8), pady=dp(5)).pack(side="left")

        for widget in (frame, header, self.dot):
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<B1-Motion>", self._do_move)
        tk.Label(frame, text="拖动我搬家", bg=COLORS["card"], fg=COLORS["muted"],
                 font=FONTS["small"]).pack(pady=(dp(4), 0))

        width, height = dp(240), dp(130)
        vx, vy, vw, vh = W.virtual_screen_rect()
        self.geometry(f"{width}x{height}+{vx + vw - width - dp(24)}+{vy + dp(80)}")
        self._offset = (0, 0)
        self.tick()
        self.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------
    def _start_move(self, event) -> None:
        self._offset = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _do_move(self, event) -> None:
        x = event.x_root - self._offset[0]
        y = event.y_root - self._offset[1]
        self.geometry(f"+{x}+{y}")

    def tick(self) -> None:
        try:
            snap = self.app.clicker.snapshot()
            running = snap.get("running")
            self.var_main.set(f"{snap.get('count', 0)} 次 · {snap.get('cps', 0)} 次/秒 · "
                              f"{snap.get('elapsed_text', '00:00')}")
            self.dot.itemconfigure(self._circle,
                                   fill=COLORS["ok"] if running else COLORS["muted"])
            self.btn_toggle.configure(text="停止" if running else "开始",
                                      bg=COLORS["danger"] if running else COLORS["accent"])
        except Exception:
            pass
        try:
            self.after(600, self.tick)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.app._mini = None  # noqa: SLF001
        except Exception:
            pass
        self.destroy()
