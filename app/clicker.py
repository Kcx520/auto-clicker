"""连点引擎。

一个后台线程按设定节奏注入鼠标事件，支持：
固定 CPS / 固定间隔 / 随机区间 / 正态分布、拟人化抖动、
次数与时长限制、启动倒计时、前台限定、后台点击、长按与拖拽。
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from . import winapi as W
from .config import Config

# 事件类型
EV_STATE = "state"        # 运行状态变化
EV_COUNT = "count"        # 计数更新
EV_LOG = "log"            # 日志
EV_FINISHED = "finished"  # 因次数/时长到达而结束


def precise_sleep(duration: float, stop_event: threading.Event, slack: float = 0.0012) -> bool:
    """比 time.sleep 更准的等待；返回 False 表示中途被停止。"""
    if duration <= 0:
        return not stop_event.is_set()
    end = time.perf_counter() + duration
    while True:
        remaining = end - time.perf_counter()
        if remaining <= 0:
            return True
        if stop_event.is_set():
            return False
        if remaining > 0.02:
            time.sleep(remaining - 0.015)
        elif remaining > slack:
            time.sleep(remaining * 0.5)
        # 最后一小段忙等，保证高 CPS 下节奏稳定


def resolve_position(position: Optional[Dict[str, Any]]) -> Optional[Tuple[int, int]]:
    """把位置卡片解析成屏幕坐标（支持跟随窗口）。"""
    if not position:
        return None
    if position.get("relative"):
        title = position.get("window_title") or ""
        hwnd = W.find_window_by_title(title) if title else 0
        if not hwnd:
            hwnd = int(position.get("hwnd") or 0)
        if hwnd and W.is_window(hwnd):
            cx, cy, _, _ = W.client_rect_on_screen(hwnd)
            return (cx + int(position.get("offset_x", 0)),
                    cy + int(position.get("offset_y", 0)))
    return int(position.get("x", 0)), int(position.get("y", 0))


class ClickStats:
    __slots__ = ("count", "started_at", "finished_at", "target_count", "duration_limit",
                 "misses", "cps_now")

    def __init__(self) -> None:
        self.count = 0
        self.misses = 0
        self.started_at = 0.0
        self.finished_at = 0.0
        self.target_count = 0
        self.duration_limit = 0.0
        self.cps_now = 0.0

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        end = self.finished_at or time.perf_counter()
        return max(0.0, end - self.started_at)

    def cps(self) -> float:
        elapsed = self.elapsed()
        return (self.count / elapsed) if elapsed > 0.05 else 0.0

    def remaining_text(self) -> str:
        if self.target_count:
            return f"剩 {max(0, self.target_count - self.count)} 次"
        if self.duration_limit:
            left = self.duration_limit - self.elapsed()
            if left <= 0:
                return "即将结束"
            minutes, seconds = divmod(int(left), 60)
            return f"剩 {minutes:02d}:{seconds:02d}"
        return "不限"


class Clicker:
    """连点器核心。线程安全，所有回调都在工作线程里触发。"""

    def __init__(self, config: Config, on_event: Optional[Callable[[str, Any], None]] = None) -> None:
        self.config = config
        self.on_event = on_event
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.running = False
        self.stats = ClickStats()
        self._countdown = 0.0
        self._suppress_move_ts = 0.0

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self.running

    def emit(self, kind: str, payload: Any = None) -> None:
        if self.on_event:
            try:
                self.on_event(kind, payload)
            except Exception:
                pass

    def start(self, delay: Optional[float] = None) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self.stats = ClickStats()
            delay_s = self.config.get("click.start_delay_s", 3.0) if delay is None else delay
            self._thread = threading.Thread(target=self._run, args=(float(delay_s or 0),),
                                            name="clicker", daemon=True)
            self.running = True
            self._thread.start()
        self.emit(EV_STATE, "starting")
        return True

    def stop(self, reason: str = "") -> bool:
        with self._lock:
            if not self.running:
                return False
            self._stop.set()
            self.running = False
        self.emit(EV_STATE, "stopping")
        return True

    def toggle(self) -> bool:
        if self.running:
            self.stop("手动停止")
            return False
        self.start()
        return True

    def click_once(self) -> None:
        """立刻点一次（忽略限制与延迟）。"""
        cfg = self.config
        position = cfg.active_position() if cfg.get("click.target_mode") == "fixed" else None
        self._do_click(position, once=True)
        self.emit(EV_COUNT, {"count": self.stats.count, "once": True})

    # ------------------------------------------------------------------
    # 工作线程
    # ------------------------------------------------------------------
    def _run(self, delay_s: float) -> None:
        cfg = self.config
        stats = self.stats
        mode = cfg.get("click.limit_mode", "infinite")
        target_count = int(cfg.get("click.max_count", 0) or 0) if mode == "count" else 0
        duration = float(cfg.get("click.max_minutes", 0) or 0) * 60.0 if mode == "duration" else 0.0
        stats.target_count = target_count
        stats.duration_limit = duration

        # 启动倒计时
        if delay_s > 0:
            self._countdown = delay_s
            end = time.perf_counter() + delay_s
            while True:
                left = end - time.perf_counter()
                if left <= 0 or self._stop.is_set():
                    break
                self.emit(EV_COUNT, {"countdown": left})
                time.sleep(min(0.1, max(0.01, left)))
            self._countdown = 0.0
            if self._stop.is_set():
                self._finish("已取消")
                return

        stats.started_at = time.perf_counter()
        self.emit(EV_STATE, "running")
        self.emit(EV_LOG, f"已开始（{'后台点击' if cfg.get('click.background_mode') else '正常点击'}）")

        tick = 0
        last_emit = 0.0
        while not self._stop.is_set():
            # 次数 / 时长限制
            if target_count and stats.count >= target_count:
                self._finish(f"已完成 {target_count} 次")
                return
            if duration and stats.elapsed() >= duration:
                self._finish(f"已运行 {cfg.get('click.max_minutes')} 分钟")
                return

            position = cfg.active_position() if cfg.get("click.target_mode", "fixed") == "fixed" else None
            ok = self._do_click(position)
            if ok:
                stats.count += 1
            else:
                stats.misses += 1
            tick += 1

            now = time.perf_counter()
            if now - last_emit >= 0.12:
                last_emit = now
                stats.cps_now = stats.cps()
                self.emit(EV_COUNT, self.snapshot())

            # 微停顿（拟人化）
            if cfg.get("click.humanize") and cfg.get("click.micro_pause_every", 0):
                every = int(cfg.get("click.micro_pause_every", 0) or 0)
                if every > 0 and tick % every == 0:
                    low = float(cfg.get("click.micro_pause_min_s", 0.5) or 0.5)
                    high = float(cfg.get("click.micro_pause_max_s", 2.0) or 2.0)
                    if not precise_sleep(random.uniform(min(low, high), max(low, high)), self._stop):
                        break

            interval = self._next_interval()
            if not precise_sleep(interval, self._stop):
                break

        self._finish("已停止")

    def _finish(self, reason: str) -> None:
        self.stats.finished_at = time.perf_counter()
        with self._lock:
            self.running = False
        self._stop.set()
        self.emit(EV_STATE, "stopped")
        self.emit(EV_FINISHED, {"reason": reason, "stats": self.snapshot()})
        self.emit(EV_LOG, reason)

    # ------------------------------------------------------------------
    # 单次点击
    # ------------------------------------------------------------------
    def _next_interval(self) -> float:
        cfg = self.config
        mode = cfg.get("click.interval_mode", "cps")
        if mode == "cps":
            cps = max(0.1, float(cfg.get("click.cps", 10) or 10))
            base = 1.0 / cps
        elif mode == "random":
            low = float(cfg.get("click.random_min_ms", 80) or 80) / 1000.0
            high = float(cfg.get("click.random_max_ms", 150) or 150) / 1000.0
            low, high = min(low, high), max(low, high)
            if cfg.get("click.gaussian"):
                mean = (low + high) / 2.0
                sigma = max(1e-4, (high - low) / 4.0)
                base = max(0.0005, random.gauss(mean, sigma))
            else:
                base = random.uniform(low, high)
        else:
            base = max(0.0002, float(cfg.get("click.interval_ms", 100) or 100) / 1000.0)

        if cfg.get("click.humanize"):
            jitter = float(cfg.get("click.jitter_percent", 0) or 0) / 100.0
            if jitter > 0:
                base *= 1.0 + random.uniform(-jitter, jitter)
        return max(0.0, base)

    def _do_click(self, position: Optional[Dict[str, Any]], once: bool = False) -> bool:
        cfg = self.config
        click_cfg = {
            "button": cfg.get("click.button", "left"),
            "action": cfg.get("click.action", "single"),
            "hold_ms": int(cfg.get("click.hold_ms", 60) or 0),
            "gap_ms": int(cfg.get("click.double_click_interval_ms", 40) or 40),
            "offset": int(cfg.get("click.offset_px", 0) or 0) if cfg.get("click.humanize") else 0,
            "miss": float(cfg.get("click.miss_chance", 0) or 0),
            "drag": (int(cfg.get("click.drag_dx", 0) or 0),
                     int(cfg.get("click.drag_dy", 0) or 0),
                     int(cfg.get("click.drag_duration_ms", 300) or 300)),
            "foreground_only": bool(cfg.get("click.foreground_only")),
            "background": bool(cfg.get("click.background_mode")),
            "title": cfg.get("click.target_window_title", "") or "",
        }

        # 随机漏点
        if not once and click_cfg["miss"] > 0 and random.random() < click_cfg["miss"]:
            return False

        # 目标坐标
        if cfg.get("click.target_mode", "fixed") == "fixed" and position:
            target = resolve_position(position)
        else:
            target = W.get_cursor_pos()

        if target is None:
            return False
        x, y = target

        if click_cfg["offset"] > 0:
            x += random.randint(-click_cfg["offset"], click_cfg["offset"])
            y += random.randint(-click_cfg["offset"], click_cfg["offset"])

        # 前台限定
        foreground = W.foreground_window()
        if click_cfg["foreground_only"] and click_cfg["title"]:
            if not W.window_matches(foreground, click_cfg["title"]):
                return False

        # 后台点击模式
        if click_cfg["background"]:
            hwnd = W.find_window_by_title(click_cfg["title"]) if click_cfg["title"] else 0
            if not hwnd:
                hwnd = W.ancestor_window(W.window_from_point(x, y))
            if hwnd and W.post_click(hwnd, x, y, click_cfg["button"]):
                return True
            # 找不到窗口就退回前台点击

        button = click_cfg["button"]
        action = click_cfg["action"]

        if button in ("wheel_up", "wheel_down"):
            self._self_move_note()
            W.wheel(1 if button == "wheel_up" else -1)
            return True

        if action == "drag":
            dx, dy, dur = click_cfg["drag"]
            self._self_move_note()
            W.drag(x, y, x + dx, y + dy, dur, button)
            return True

        self._note_move()
        if action == "double":
            W.double_click(button, x, y, click_cfg["gap_ms"], max(1, click_cfg["hold_ms"] // 3))
        elif action == "triple":
            for index in range(3):
                W.click(button, max(1, click_cfg["hold_ms"] // 3), x, y,
                        move_first=(index == 0))
                if index < 2:
                    time.sleep(click_cfg["gap_ms"] / 1000.0)
        elif action == "hold":
            W.set_cursor_pos(x, y)
            time.sleep(0.002)
            W.mouse_down(button)
            precise_sleep(max(0.001, click_cfg["hold_ms"] / 1000.0), self._stop)
            W.mouse_up(button)
        else:
            hold = click_cfg["hold_ms"] if click_cfg["hold_ms"] > 0 else 1
            if hold > 60:
                W.set_cursor_pos(x, y)
                time.sleep(0.002)
                W.mouse_down(button)
                precise_sleep(hold / 1000.0, self._stop)
                W.mouse_up(button)
            else:
                W.click(button, hold, x, y)
        return True

    def _note_move(self) -> None:
        self._suppress_move_ts = time.perf_counter()

    _self_move_note = _note_move

    def self_move_age(self) -> float:
        return time.perf_counter() - self._suppress_move_ts

    # ------------------------------------------------------------------
    # 状态快照（给界面轮询）
    # ------------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        stats = self.stats
        return {
            "running": self.running,
            "count": stats.count,
            "cps": round(stats.cps(), 2),
            "elapsed": stats.elapsed(),
            "elapsed_text": self._fmt_time(stats.elapsed()),
            "remaining": stats.remaining_text(),
            "countdown": self._countdown,
            "target_count": stats.target_count,
            "misses": stats.misses,
        }

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
