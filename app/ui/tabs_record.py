"""「录制回放」页签。"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List

from ..recorder import export_macro, import_macro, macro_summary
from . import theme
from .theme import COLORS, FONTS, dp
from .widgets import Card, CheckField, NumberField, ScrollFrame, SliderField, hint


class RecordTab(ttk.Frame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, style="TFrame")
        self.app = app
        self.cfg = app.cfg
        self.macro_rows: List[Dict[str, Any]] = []

        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        holder = scroll.inner

        card = Card(holder, title="录制一遍操作",
                    subtitle="点「开始录制」，然后正常操作一遍（点按钮、打字、滚轮都行），"
                             "回来点「停止录制」，以后就能一键重放。")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        bar = ttk.Frame(card.body, style="Card.TFrame")
        bar.pack(fill="x")
        self.btn_record = theme.tk_button(bar, "● 开始录制", self.toggle_record, "accent",
                                          font=FONTS["big"], padx=dp(18), pady=dp(8))
        self.btn_record.pack(side="left")
        self.check_ignore_move = CheckField(bar, "只记录点击和按键，不记录鼠标移动路径",
                                            bool(self.cfg.get("record.ignore_move")))
        self.check_ignore_move.pack(side="left", padx=dp(16))
        self.record_status = tk.StringVar(value="未在录制")
        ttk.Label(bar, textvariable=self.record_status, style="Hint.TLabel").pack(side="right")

        play = Card(holder, title="回放设置")
        play.pack(fill="x", pady=dp(6), padx=dp(2))
        body = play.body
        self.slider_speed = SliderField(body, "播放速度", float(self.cfg.get("record.speed", 1.0)),
                                        0.2, 5.0, kind="float", suffix="倍速",
                                        help="1 倍速 = 和你当时操作一样快；2 倍速 = 快一倍")
        self.slider_speed.grid(row=0, column=0, sticky="w", pady=dp(3))
        self.num_loop = NumberField(body, "循环播放", self.cfg.get("record.loop", 1), from_=1,
                                    to=100000, kind="int", width=6, suffix="遍")
        self.num_loop.grid(row=1, column=0, sticky="w", pady=dp(3))
        self.check_ignore_delay = CheckField(body, "忽略停顿（连续快速执行，不等待原来的间隔）",
                                             bool(self.cfg.get("record.ignore_delay")))
        self.check_ignore_delay.grid(row=2, column=0, sticky="w", pady=dp(3))

        self.list_card = Card(holder, title="我录好的动作")
        self.list_card.pack(fill="x", pady=dp(6), padx=dp(2))
        self.rows_holder = ttk.Frame(self.list_card.body, style="Card.TFrame")
        self.rows_holder.pack(fill="x")
        bottom = ttk.Frame(self.list_card.body, style="Card.TFrame")
        bottom.pack(fill="x", pady=(dp(8), 0))
        theme.tk_button(bottom, "从文件导入录制", self.import_macro_file, "ghost").pack(side="left")

        self.refresh()
        self.after(700, self._tick)

    # ------------------------------------------------------------------
    def toggle_record(self) -> None:
        if self.app.recorder.is_recording():
            self.app.stop_recording()
            self.refresh()
        else:
            self.app.start_recording()

    def refresh(self) -> None:
        for row in self.macro_rows:
            try:
                row["frame"].destroy()
            except Exception:
                pass
        self.macro_rows = []
        macros = self.cfg.get_ref("record.macros", []) or []
        if not macros:
            ttk.Label(self.rows_holder, text="还没有录制内容。", style="Hint.TLabel").pack(
                anchor="w", pady=dp(4))
            return
        for index, macro in enumerate(macros):
            frame = ttk.Frame(self.rows_holder, style="Card2.TFrame")
            frame.pack(fill="x", pady=dp(3))
            inner = ttk.Frame(frame, style="Card2.TFrame")
            inner.pack(fill="x", padx=dp(10), pady=dp(8))
            ttk.Label(inner, text=macro.get("name", "录制"), style="Card.TLabel",
                      font=FONTS["bold"]).pack(side="left")
            ttk.Label(inner, text=macro_summary(macro), style="Hint.TLabel").pack(side="left",
                                                                             padx=dp(12))
            theme.tk_button(inner, "删除", lambda m=macro: self.delete_macro(m), "danger").pack(
                side="right", padx=dp(3))
            theme.tk_button(inner, "导出", lambda m=macro: self.export_macro_file(m),
                            "ghost").pack(side="right", padx=dp(3))
            theme.tk_button(inner, "改名", lambda m=macro, f=inner: self.rename_macro(m),
                            "ghost").pack(side="right", padx=dp(3))
            theme.tk_button(inner, "▶ 回放", lambda m=macro: self.play(m), "accent").pack(
                side="right", padx=dp(3))
            self.macro_rows.append({"frame": frame, "macro": macro})

    # ------------------------------------------------------------------
    def play(self, macro: Dict[str, Any]) -> None:
        if self.app.player.is_running():
            self.app.player.stop()
            self.app.log("已停止回放", "warn")
            return
        self._save_settings()
        self.app.player.play(macro, speed=float(self.slider_speed.get()),
                             loop=self.num_loop.get_int(),
                             ignore_delay=self.check_ignore_delay.get())

    def on_playback_done(self) -> None:
        self.app.log("回放完成", "ok")

    def rename_macro(self, macro: Dict[str, Any]) -> None:
        from tkinter import simpledialog
        name = simpledialog.askstring("改名", "给这段录制起个名字：",
                                      initialvalue=macro.get("name", ""), parent=self)
        if name:
            macro["name"] = name
            self._save()
            self.refresh()

    def delete_macro(self, macro: Dict[str, Any]) -> None:
        if not messagebox.askyesno("删除录制", f"确定删除「{macro.get('name')}」吗？"):
            return
        macros = [m for m in (self.cfg.get("record.macros") or []) if m is not macro]
        self.cfg.set("record.macros", macros)
        self._save()
        self.refresh()

    def export_macro_file(self, macro: Dict[str, Any]) -> None:
        path = filedialog.asksaveasfilename(title="导出录制", defaultextension=".json",
                                            initialfile=f"{macro.get('name', 'macro')}.json",
                                            filetypes=[("录制文件", "*.json")])
        if path and export_macro(macro, path):
            self.app.log(f"已导出到 {path}", "ok")

    def import_macro_file(self) -> None:
        path = filedialog.askopenfilename(title="导入录制",
                                          filetypes=[("录制文件", "*.json")])
        if not path:
            return
        macro = import_macro(path)
        if not macro:
            self.app.log("这个文件不是有效的录制文件", "error")
            return
        macros = self.cfg.get("record.macros") or []
        macros.append(macro)
        self.cfg.set("record.macros", macros)
        self._save()
        self.refresh()
        self.app.log(f"已导入「{macro.get('name')}」", "ok")

    # ------------------------------------------------------------------
    def _save_settings(self) -> None:
        self.cfg.update({"record": {
            "ignore_move": self.check_ignore_move.get(),
            "speed": self.slider_speed.get(),
            "loop": self.num_loop.get_int(),
            "ignore_delay": self.check_ignore_delay.get(),
        }})
        self._save()

    def _save(self) -> None:
        self.cfg.save()

    def _tick(self) -> None:
        try:
            self._save_settings()
            if self.app.recorder.is_recording():
                self.btn_record.configure(text="■ 停止录制", bg=COLORS["danger"])
                self.record_status.set(f"正在录制…已记录 {self.app.recorder.count()} 个动作")
            else:
                self.btn_record.configure(text="● 开始录制", bg=COLORS["accent"])
                self.record_status.set("未在录制")
        except Exception:
            pass
        try:
            self.after(700, self._tick)
        except Exception:
            pass
