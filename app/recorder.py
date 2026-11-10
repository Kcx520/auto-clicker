"""录制与回放：把你手动做一遍的操作录下来，之后一键重放。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from . import winapi as W

# 鼠标移动采样间隔：太密没意义还占内存
MOVE_MIN_GAP = 0.02


def new_macro(name: str = "新录制", events: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "events": events or [],
        "duration": 0.0,
    }


class Recorder:
    """录制缓存。

    ``feed`` 由低级钩子线程直接调用，所以必须极快：只做一次 append。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self.recording = False
        self._started = 0.0
        self._last_move = 0.0
        self.ignore_move = False
        self._pending_move: Optional[Dict[str, Any]] = None

    def start(self, ignore_move: bool = False) -> None:
        with self._lock:
            self._events = []
            self.recording = True
            self._started = time.perf_counter()
            self._last_move = 0.0
            self._pending_move = None
        self.ignore_move = bool(ignore_move)

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            self.recording = False
            events = list(self._events)
            duration = time.perf_counter() - self._started if self._started else 0.0
        macro = new_macro(time.strftime("录制 %m-%d %H:%M"), events)
        macro["duration"] = round(duration, 3)
        return macro

    def is_recording(self) -> bool:
        return self.recording

    def count(self) -> int:
        with self._lock:
            return len(self._events)

    # ------------------------------------------------------------------
    def feed(self, event: Dict[str, Any]) -> None:
        """钩子线程调用——保持轻量。"""
        if not self.recording:
            return
        try:
            kind = event.get("type", "")
            stamp = float(event.get("t", time.perf_counter()))
            if kind == "mouse_move":
                if self.ignore_move:
                    return
                if stamp - self._last_move < MOVE_MIN_GAP:
                    self._pending_move = event
                    return
                self._last_move = stamp
                self._pending_move = None
            elif self._pending_move is not None:
                with self._lock:
                    self._events.append(self._pending_move)
                self._pending_move = None
            record = dict(event)
            record["t"] = round(stamp - self._started, 6)
            with self._lock:
                self._events.append(record)
        except Exception:
            pass

    def flush_pending(self) -> None:
        event, self._pending_move = self._pending_move, None
        if event is not None:
            with self._lock:
                self._events.append(event)


class Player:
    """宏回放。"""

    def __init__(self, on_log: Optional[Callable[[str], None]] = None,
                 on_finish: Optional[Callable[[], None]] = None) -> None:
        self.on_log = on_log
        self.on_finish = on_finish
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.running = False
        self._lock = threading.RLock()

    def is_running(self) -> bool:
        return self.running

    def log(self, text: str) -> None:
        if self.on_log:
            try:
                self.on_log(text)
            except Exception:
                pass

    def play(self, macro: Dict[str, Any], speed: float = 1.0, loop: int = 1,
             ignore_delay: bool = False) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self.running = True
            self._thread = threading.Thread(
                target=self._run, args=(macro, max(0.05, float(speed or 1.0)),
                                        max(1, int(loop or 1)), bool(ignore_delay)),
                name="macro-player", daemon=True)
            self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self.running = False

    def _run(self, macro: Dict[str, Any], speed: float, loop: int, ignore_delay: bool) -> None:
        events = macro.get("events") or []
        if not events:
            self.log("这个录制是空的")
            self.running = False
            self._done()
            return
        self.log(f"回放「{macro.get('name', '')}」共 {len(events)} 个动作 ×{speed:g} 倍速")
        total = len(events)
        for round_index in range(loop):
            if self._stop.is_set():
                break
            previous = 0.0
            for index, event in enumerate(events):
                if self._stop.is_set():
                    break
                stamp = float(event.get("t", 0.0))
                if not ignore_delay:
                    wait = max(0.0, (stamp - previous) / speed)
                    if wait > 0 and self._stop.wait(wait):
                        break
                previous = stamp
                self._apply(event)
                if index % 25 == 0:
                    self.log(f"回放中 {index + 1}/{total}")
            if loop > 1:
                self.log(f"第 {round_index + 1}/{loop} 遍完成")
            if self._stop.is_set():
                break
            if round_index < loop - 1:
                time.sleep(0.2)
        self.running = False
        self._done()
        self.log("回放结束")

    def _done(self) -> None:
        if self.on_finish:
            try:
                self.on_finish()
            except Exception:
                pass

    @staticmethod
    def _apply(event: Dict[str, Any]) -> None:
        kind = event.get("type", "")
        try:
            if kind == "mouse_move":
                W.set_cursor_pos(int(event.get("x", 0)), int(event.get("y", 0)))
            elif kind == "mouse_down":
                W.set_cursor_pos(int(event.get("x", 0)), int(event.get("y", 0)))
                W.mouse_down(event.get("button") or "left")
            elif kind == "mouse_up":
                W.set_cursor_pos(int(event.get("x", 0)), int(event.get("y", 0)))
                W.mouse_up(event.get("button") or "left")
            elif kind == "wheel":
                delta = int(event.get("delta", 120) or 120)
                W.wheel(1 if delta > 0 else -1)
            elif kind == "key_down":
                W.key_down(int(event.get("vk", 0)))
            elif kind == "key_up":
                W.key_up(int(event.get("vk", 0)))
        except Exception:
            pass


def macro_summary(macro: Dict[str, Any]) -> str:
    events = macro.get("events") or []
    clicks = sum(1 for e in events if e.get("type") == "mouse_down")
    keys = sum(1 for e in events if e.get("type") == "key_down")
    duration = float(macro.get("duration", 0) or 0)
    return f"{len(events)} 个动作 · {clicks} 次点击 · {keys} 次按键 · {duration:.1f} 秒"


def export_macro(macro: Dict[str, Any], path: str) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(macro, fh, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def import_macro(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            data.setdefault("id", uuid.uuid4().hex[:8])
            data.setdefault("name", "导入的录制")
            return data
    except Exception:
        pass
    return None
