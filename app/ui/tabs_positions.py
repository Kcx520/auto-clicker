"""「位置收藏」页签：把鼠标位置存成卡片，随时让鼠标自己过去点。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional

from .. import winapi as W
from ..clicker import resolve_position
from . import theme
from .theme import COLORS, FONTS, dp
from .widgets import (Card, CheckField, HotkeyButton, NumberField, ScrollFrame, TextField,
                      add_help, hint)


class PositionCard(Card):
    def __init__(self, parent, tab: "PositionsTab", position: Dict[str, Any], index: int):
        super().__init__(parent, title="")
        self.tab = tab
        self.app = tab.app
        self.cfg = tab.cfg
        self.position = position
        body = self.body

        head = ttk.Frame(body, style="Card.TFrame")
        head.grid(row=0, column=0, columnspan=6, sticky="ew")
        ttk.Label(head, text=f"#{index + 1}", style="Hint.TLabel").pack(side="left")
        self.name_field = TextField(head, "", position.get("name", ""), width=16,
                                    help="给这个位置起个名字，例如「确认按钮」")
        self.name_field.pack(side="left", padx=dp(6))
        self.var_relative = tk.BooleanVar(value=bool(position.get("relative")))
        check = ttk.Checkbutton(head, text="跟随窗口（窗口挪动后自动跟着走）",
                                variable=self.var_relative, style="Card.TCheckbutton",
                                command=self._toggle_relative)
        check.pack(side="left", padx=dp(8))
        add_help(check, "勾上以后，位置记录成窗口内的相对坐标。窗口移动或改变大小，点击位置自动跟着变")
        self.var_enabled = tk.BooleanVar(value=True)
        self._hwnd = int(position.get("hwnd") or 0)

        coords = ttk.Frame(body, style="Card.TFrame")
        coords.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(dp(6), 0))
        self.num_x = NumberField(coords, "X", position.get("x", 0), from_=-32768, to=32768,
                                 kind="int", width=7)
        self.num_x.pack(side="left")
        self.num_y = NumberField(coords, "Y", position.get("y", 0), from_=-32768, to=32768,
                                 kind="int", width=7)
        self.num_y.pack(side="left", padx=dp(10))
        theme.tk_button(coords, "拾取", self.pick, "ghost").pack(side="left", padx=dp(4))
        theme.tk_button(coords, "去这里", self.goto, "ghost").pack(side="left", padx=dp(4))
        theme.tk_button(coords, "测试点击", self.test_click, "accent").pack(side="left", padx=dp(4))
        theme.tk_button(coords, "设为当前目标", self.set_active, "ghost").pack(side="left",
                                                                          padx=dp(4))
        theme.tk_button(coords, "删除", self.delete, "danger").pack(side="left", padx=dp(10))

        hot = ttk.Frame(body, style="Card.TFrame")
        hot.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(dp(6), 0))
        ttk.Label(hot, text="专属热键", style="Card.TLabel").pack(side="left")
        self.hotkey_button = HotkeyButton(
            hot, lambda: self.position.get("hotkey", ""),
            lambda: self.app.begin_capture(f"position:{self.position.get('id')}",
                                           self.hotkey_button))
        self.hotkey_button.pack(side="left", padx=dp(6))
        ttk.Label(hot, text="（按一下就自动点这个位置）", style="Hint.TLabel").pack(side="left")

        self.window_label = ttk.Label(
            body, text=self._window_text(), style="Hint.TLabel", wraplength=dp(700),
            justify="left")
        self.window_label.grid(row=3, column=0, columnspan=6, sticky="w", pady=(dp(4), 0))
        body.columnconfigure(5, weight=1)

    def _window_text(self) -> str:
        title = self.position.get("window_title") or ""
        if not title:
            return "记录窗口：—"
        return f"记录窗口：{title}"

    def _toggle_relative(self) -> None:
        want = bool(self.var_relative.get())
        if want:
            hwnd = self._hwnd or W.foreground_window()
            title = self.position.get("window_title") or W.window_title(hwnd)
            if not title:
                self.app.log("要跟随窗口，请先把目标窗口切到最前面，再勾选", "warn")
                self.var_relative.set(False)
                return
            cx, cy, _, _ = W.client_rect_on_screen(hwnd)
            self.position["hwnd"] = hwnd
            self.position["window_title"] = title
            self.position["offset_x"] = self.num_x.get_int() - cx
            self.position["offset_y"] = self.num_y.get_int() - cy
            self.app.log(f"「{self.position.get('name')}」已设为跟随窗口：{title}", "ok")
        self.position["relative"] = want
        self.window_label.configure(text=self._window_text())
        self.save()

    def pick(self) -> None:
        def done(point):
            if point:
                self.num_x.set(point[0])
                self.num_y.set(point[1])
                hwnd = W.window_from_point(point[0], point[1])
                root = W.ancestor_window(hwnd)
                self._hwnd = root
                self.position["hwnd"] = root
                self.position["window_title"] = W.window_title(root)
                if self.var_relative.get():
                    cx, cy, _, _ = W.client_rect_on_screen(root)
                    self.position["offset_x"] = point[0] - cx
                    self.position["offset_y"] = point[1] - cy
                self.window_label.configure(text=self._window_text())
                self.save()
                self.tab.app.log(f"已拾取坐标 {point}")
        self.app.pick_point(done)

    def goto(self) -> None:
        point = resolve_position(self.merged())
        if point:
            W.set_cursor_pos(point[0], point[1])
            self.app.log(f"鼠标已移动到 ({point[0]}, {point[1]})")

    def test_click(self) -> None:
        point = resolve_position(self.merged())
        if not point:
            return
        W.click(self.cfg.get("click.button", "left"),
                int(self.cfg.get("click.hold_ms", 20) or 20), point[0], point[1])
        self.app.log(f"已在 ({point[0]}, {point[1]}) 点了一下")

    def set_active(self) -> None:
        self.cfg.set("click.position_id", self.position.get("id"))
        for index, item in enumerate(self.cfg.positions()):
            if item.get("id") == self.position.get("id"):
                self.cfg.set("click.position_index", index)
        self.save()
        self.tab.app.tab_click.refresh_positions()
        self.tab.app.marker.refresh()
        self.app.log(f"当前连点目标已设为「{self.position.get('name')}」", "ok")

    def delete(self) -> None:
        from tkinter import messagebox
        if not messagebox.askyesno("删除位置", f"确定删除「{self.position.get('name')}」吗？"):
            return
        self.cfg.remove_position(self.position.get("id", ""))
        self.cfg.save()
        self.tab.refresh()
        self.tab.app.tab_click.refresh_positions()
        self.app.log("位置已删除")

    def merged(self) -> Dict[str, Any]:
        data = dict(self.position)
        data["x"] = self.num_x.get_int()
        data["y"] = self.num_y.get_int()
        return data

    def collect(self) -> bool:
        """把输入框里的改动写回配置（自动保存用）。返回是否有改动。"""
        name = self.name_field.get() or "位置"
        x, y = self.num_x.get_int(), self.num_y.get_int()
        relative = bool(self.var_relative.get())
        changed = (self.position.get("name") != name or self.position.get("x") != x
                   or self.position.get("y") != y
                   or bool(self.position.get("relative")) != relative)
        self.position["name"] = name
        self.position["x"] = x
        self.position["y"] = y
        self.position["relative"] = relative
        return changed

    def save(self) -> None:
        self.position["name"] = self.name_field.get() or "位置"
        self.position["x"] = self.num_x.get_int()
        self.position["y"] = self.num_y.get_int()
        self.cfg.save()
        self.tab.app.tab_click.refresh_positions()
        try:
            self.app.marker.refresh()
        except Exception:
            pass

    def refresh_hotkey(self) -> None:
        self.hotkey_button.refresh()

    def show_captured(self, text: str) -> None:
        self.hotkey_button.show_captured(text)


class PositionsTab(ttk.Frame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, style="TFrame")
        self.app = app
        self.cfg = app.cfg
        self.cards: List[PositionCard] = []

        top = Card(self, title="位置收藏",
                   subtitle="鼠标放到目标处按 F9 直接存；或者点【拾取】再用鼠标点屏幕上任意位置。"
                            "存好的位置可以在「连点」和「智能识别」里使用。")
        top.pack(fill="x", pady=dp(6), padx=dp(2))
        bar = ttk.Frame(top.body, style="Card.TFrame")
        bar.pack(fill="x", pady=dp(2))
        theme.tk_button(bar, "＋ 添加位置", self.add_position, "accent").pack(side="left")
        theme.tk_button(bar, "全部清空", self.clear_all, "ghost").pack(side="left", padx=dp(8))
        self.check_overlay = CheckField(bar, "在屏幕上显示位置标记", False,
                                        command=self._toggle_overlay)
        self.check_overlay.pack(side="right")
        self.check_overlay.set(bool(self.cfg.get("general.show_overlay")))

        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)

        self.refresh()
        self.after(1200, self._autosave)

    def _autosave(self) -> None:
        """位置卡片里的输入是随时改的，定期自动落盘，免得忘了点保存就丢了。"""
        try:
            changed = False
            for card in self.cards:
                if card.collect():
                    changed = True
            if changed:
                self.cfg.save()
                self.app.tab_click.refresh_positions()
                try:
                    self.app.marker.refresh()
                except Exception:
                    pass
        except Exception as exc:
            self.app.log(f"位置自动保存出错：{exc}", "error")
        try:
            self.after(1200, self._autosave)
        except Exception:
            pass

    def _toggle_overlay(self) -> None:
        self.cfg.set("general.show_overlay", self.check_overlay.get())
        self.cfg.save()
        try:
            if self.check_overlay.get():
                self.app.marker.show()
            else:
                self.app.marker.hide()
        except Exception:
            pass
        try:
            self.app.tab_click.check_overlay.set(self.check_overlay.get())
        except Exception:
            pass

    def add_position(self) -> None:
        item = self.cfg.add_position()
        self.cfg.save()
        self.refresh()
        self.app.log("已添加一个新位置，点【拾取】把它设成你要的地方")

    def clear_all(self) -> None:
        from tkinter import messagebox
        if not messagebox.askyesno("清空位置", "确定删除所有位置吗？"):
            return
        self.cfg.set("positions", [])
        self.cfg.add_position("位置 1")
        self.cfg.save()
        self.refresh()
        self.app.tab_click.refresh_positions()

    def refresh(self) -> None:
        for card in self.cards:
            try:
                card.destroy()
            except Exception:
                pass
        self.cards = []
        positions = self.cfg.positions()
        if not positions:
            self.cfg.add_position("位置 1")
            positions = self.cfg.positions()
        for index, position in enumerate(positions):
            card = PositionCard(self.scroll.inner, self, position, index)
            card.pack(fill="x", pady=dp(5), padx=dp(2))
            self.cards.append(card)
        try:
            self.scroll._on_inner()
        except Exception:
            pass

    def refresh_hotkeys(self) -> None:
        for card in self.cards:
            card.refresh_hotkey()

    def show_captured(self, name: str, text: str) -> None:
        for card in self.cards:
            if name == f"position:{card.position.get('id')}":
                card.show_captured(text)
        self.refresh_hotkeys()

    def on_show(self) -> None:
        self.refresh()
