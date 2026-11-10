"""智能识别触发：盯着屏幕找文字 / 找图片 / 找颜色，命中后自动去点指定位置。"""

from __future__ import annotations

import random
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from . import screen as S
from . import winapi as W
from .config import Config
from .clicker import resolve_position

try:  # OCR 模块由独立部分提供，缺失时不该让程序起不来
    from . import ocr as ocr_mod
except Exception:  # pragma: no cover
    ocr_mod = None  # type: ignore

try:
    from . import matcher as matcher_mod
except Exception:  # pragma: no cover
    matcher_mod = None  # type: ignore


def new_task(kind: str = "text", **extra: Any) -> Dict[str, Any]:
    task: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:8],
        "name": "",
        "enabled": True,
        "kind": kind,                 # text / image / color
        "keywords": [],               # 文字识别
        "match_mode": "any",          # any / all
        "ignore_case": True,
        "ignore_space": True,
        "template_paths": [],         # 图片识别
        "threshold": 0.85,
        "scales": [1.0],
        "color": [255, 0, 0],         # 颜色识别
        "color_tolerance": 30,
        "region": None,               # [x, y, w, h]；None = 用全局范围
        "click_where": "position",    # position（点收藏的位置）/ found（点识别到的位置）
        "position_id": "",
        "found_offset_x": 0,
        "found_offset_y": 0,
        "button": "left",
        "action": "single",
        "cooldown_s": 3.0,
        "max_hits": 0,                # 0 = 不限次数
        "hits": 0,
        "stop_after_hit": False,      # 命中一次就停掉识别
        "last_hit_at": 0.0,
    }
    task.update(extra)
    if not task["name"]:
        task["name"] = describe_task(task)
    return task


def describe_task(task: Dict[str, Any]) -> str:
    kind = task.get("kind", "text")
    if kind == "text":
        words = " / ".join(task.get("keywords") or []) or "（未填文字）"
        return f"找文字「{words}」"
    if kind == "image":
        n = len(task.get("template_paths") or [])
        return f"找图片（{n} 张模板）"
    return f"找颜色 {task.get('color')}"


class Recognizer:
    """识别调度器：独立线程轮询屏幕，命中就点。"""

    def __init__(self, config: Config, clicker,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_hit: Optional[Callable[[Dict[str, Any], Dict[str, Any]], None]] = None) -> None:
        self.config = config
        self.clicker = clicker
        self.on_log = on_log
        self.on_hit = on_hit
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.running = False
        self.polls = 0
        self.hit_count = 0
        self.last_error = ""
        self._engine = None
        self._engine_name = ""
        self._probe_started = False
        self._templates: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self.running

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self.running = True
            self.polls = 0
            self._thread = threading.Thread(target=self._loop, name="recognizer", daemon=True)
            self._thread.start()
        self._log("识别已启动")
        return True

    def stop(self) -> bool:
        with self._lock:
            if not self.running:
                return False
            self.running = False
            self._stop.set()
        self._log("识别已停止")
        return True

    def toggle(self) -> bool:
        return self.stop() if self.running else self.start()

    def _log(self, text: str) -> None:
        if self.on_log:
            try:
                self.on_log(text)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def engine(self):
        if self._engine is None and ocr_mod is not None:
            prefer = self.config.get("recognize.ocr.engine", "auto")
            try:
                self._engine = ocr_mod.get_engine(prefer)
                self._engine_name = getattr(self._engine, "name", "")
            except Exception as exc:
                self.last_error = f"OCR 初始化失败：{exc}"
                self._engine = None
        return self._engine

    def probe_async(self) -> None:
        """后台预热 OCR 引擎（构造它要花将近 1 秒，绝不能卡住界面线程）。"""
        with self._lock:
            if self._probe_started or self._engine is not None:
                return
            self._probe_started = True
        threading.Thread(target=self._probe, name="ocr-probe", daemon=True).start()

    def _probe(self) -> None:
        try:
            engine = self.engine()
            name = getattr(engine, "name", "") if engine is not None else ""
            ok = bool(getattr(engine, "available", False)) if engine is not None else False
            self._log(f"文字识别引擎：{name}{'' if ok else '（不可用）'}")
        except Exception as exc:
            self.last_error = str(exc)

    def engine_label(self) -> str:
        """只报告已探测的结果，不在界面线程里构造引擎。"""
        if self._engine is None:
            self.probe_async()
            return "正在检测…"
        if getattr(self._engine, "available", False):
            return self._engine_name or "已就绪"
        return f"{self._engine_name or 'OCR'}（不可用）"

    def _region(self, task: Optional[Dict[str, Any]] = None) -> Optional[Tuple[int, int, int, int]]:
        region = (task or {}).get("region") or self.config.get("recognize.region")
        if not region:
            return None
        try:
            x, y, w, h = (int(v) for v in region)
        except Exception:
            return None
        if w <= 1 or h <= 1:
            return None
        return x, y, w, h

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            interval = float(self.config.get("recognize.poll_interval_s", 0.5) or 0.5)
            started = time.perf_counter()
            try:
                self.poll_once()
            except Exception as exc:  # 任何异常都不该让识别线程死掉
                self.last_error = str(exc)
            self.polls += 1
            elapsed = time.perf_counter() - started
            wait = max(0.05, interval - elapsed)
            self._stop.wait(wait)

    # ------------------------------------------------------------------
    def tasks(self) -> List[Dict[str, Any]]:
        """返回**原始**任务对象（不是副本），这样命中次数、冷却时间才能累加。"""
        return [t for t in (self.config.get_ref("recognize.tasks", []) or [])
                if t.get("enabled", True)]

    def poll_once(self) -> List[Dict[str, Any]]:
        """跑一轮识别；返回本轮命中的列表 [{task, screen_x, screen_y, score}]。"""
        results: List[Dict[str, Any]] = []
        tasks = self.tasks()
        if not tasks:
            return results

        now = time.perf_counter()
        cache: Dict[Any, np.ndarray] = {}

        for task in tasks:
            if self._stop.is_set():
                break
            cooldown = float(task.get("cooldown_s", 0) or 0)
            if cooldown > 0 and now - float(task.get("last_hit_at", 0) or 0) < cooldown:
                continue
            max_hits = int(task.get("max_hits", 0) or 0)
            if max_hits and int(task.get("hits", 0) or 0) >= max_hits:
                continue

            region = self._region(task)
            key = tuple(region) if region else "full"
            if key not in cache:
                cache[key] = S.grab(region)
            image = cache[key]

            hit = None
            try:
                kind = task.get("kind", "text")
                if kind == "text":
                    hit = self._match_text(task, image, region)
                elif kind == "image":
                    hit = self._match_image(task, image, region)
                elif kind == "color":
                    hit = self._match_color(task, image, region)
            except Exception as exc:
                self.last_error = f"{describe_task(task)} 识别出错：{exc}"
                continue

            if hit:
                results.append(hit)

        for hit in results:
            self._fire(hit)
        return results

    # ------------------------------------------------------------------
    def _match_text(self, task, image: np.ndarray, region) -> Optional[Dict[str, Any]]:
        engine = self.engine()
        if engine is None or not getattr(engine, "available", False):
            self.last_error = "OCR 引擎不可用"
            return None
        result = engine.recognize(image)
        keywords = [k for k in (task.get("keywords") or []) if str(k).strip()]
        if not keywords:
            return None
        mode = task.get("match_mode", "any")
        if ocr_mod is not None and hasattr(ocr_mod, "find_keywords"):
            found = ocr_mod.find_keywords(
                result, keywords, mode,
                ignore_case=task.get("ignore_case", True),
                ignore_space=task.get("ignore_space", True))
        else:  # 兜底：自己找
            found = self._fallback_find(result, keywords, mode, task)
        if not found:
            return None
        keyword, line = found[0]
        bx, by = int(getattr(line, "x", 0)), int(getattr(line, "y", 0))
        bw, bh = int(getattr(line, "w", 0)), int(getattr(line, "h", 0))
        base_x = region[0] if region else 0
        base_y = region[1] if region else 0
        return {
            "task": task,
            "keyword": keyword,
            "text": getattr(line, "text", ""),
            "screen_x": base_x + bx,
            "screen_y": base_y + by,
            "w": bw, "h": bh,
            "score": 1.0,
        }

    @staticmethod
    def _fallback_find(result, keywords, mode, task):
        ignore_space = task.get("ignore_space", True)
        ignore_case = task.get("ignore_case", True)

        def norm(text: str) -> str:
            text = str(text or "")
            if ignore_space:
                text = "".join(text.split())
            return text.lower() if ignore_case else text

        hits = []
        for line in getattr(result, "lines", []) or []:
            for keyword in keywords:
                if norm(keyword) and norm(keyword) in norm(getattr(line, "text", "")):
                    hits.append((keyword, line))
                    break
        if not hits:
            return []
        if mode == "all":
            found_words = {h[0] for h in hits}
            if len(found_words) < len(keywords):
                return []
        return hits

    def _match_image(self, task, image: np.ndarray, region) -> Optional[Dict[str, Any]]:
        if matcher_mod is None:
            self.last_error = "找图模块不可用"
            return None
        paths = task.get("template_paths") or []
        threshold = float(task.get("threshold", 0.85) or 0.85)
        scales = tuple(task.get("scales") or (1.0,))
        best = None
        for path in paths:
            template = self._templates.get(path)
            if template is None:
                template = S.load_png(path)
                if template is None:
                    continue
                self._templates[path] = template
            try:
                found = matcher_mod.find_best(image, template, threshold=threshold, scales=scales)
            except Exception:
                found = None
            if found and (best is None or found["score"] > best["score"]):
                best = {**found, "path": path}
        if not best:
            return None
        base_x = region[0] if region else 0
        base_y = region[1] if region else 0
        return {
            "task": task,
            "keyword": "",
            "text": "",
            "screen_x": base_x + int(best["x"]),
            "screen_y": base_y + int(best["y"]),
            "w": int(best.get("w", 0)), "h": int(best.get("h", 0)),
            "score": float(best.get("score", 0.0)),
            "template": best.get("path", ""),
        }

    def _match_color(self, task, image: np.ndarray, region) -> Optional[Dict[str, Any]]:
        color = task.get("color") or [255, 0, 0]
        tol = int(task.get("color_tolerance", 30) or 0)
        target = np.array(color[:3], dtype=np.int16)
        arr = image[:, :, :3].astype(np.int16)
        diff = np.abs(arr - target).max(axis=2)
        mask = diff <= tol
        if not mask.any():
            return None
        ys, xs = np.nonzero(mask)
        cx, cy = int(xs.mean()), int(ys.mean())
        base_x = region[0] if region else 0
        base_y = region[1] if region else 0
        return {
            "task": task, "keyword": "", "text": "",
            "screen_x": base_x + cx, "screen_y": base_y + cy,
            "w": 1, "h": 1, "score": 1.0,
        }

    # ------------------------------------------------------------------
    def _fire(self, hit: Dict[str, Any]) -> None:
        task = hit["task"]
        if task.get("click_where", "position") == "found":
            target = (hit["screen_x"] + int(task.get("found_offset_x", 0) or 0),
                      hit["screen_y"] + int(task.get("found_offset_y", 0) or 0))
            if hit.get("w"):
                target = (target[0] + hit["w"] // 2, target[1] + hit["h"] // 2)
        else:
            position = self.config.find_position(task.get("position_id", ""))
            resolved = resolve_position(position)
            target = resolved if resolved else (hit["screen_x"], hit["screen_y"])

        button = task.get("button", "left")
        action = task.get("action", "single")
        hold = int(self.config.get("click.hold_ms", 60) or 60)

        # 避免"识别到 -> 点过去"被自己的急停/开关打断
        try:
            if self.clicker is not None:
                self.clicker._note_move()  # noqa: SLF001
        except Exception:
            pass

        try:
            if action == "double":
                W.double_click(button, target[0], target[1])
            elif action == "hold":
                W.set_cursor_pos(target[0], target[1])
                time.sleep(0.01)
                W.mouse_down(button)
                time.sleep(max(0.01, hold / 1000.0))
                W.mouse_up(button)
            else:
                W.click(button, hold, target[0], target[1])
        except Exception as exc:
            self.last_error = f"点击失败：{exc}"
            return

        task["hits"] = int(task.get("hits", 0) or 0) + 1
        task["last_hit_at"] = time.perf_counter()
        self.hit_count += 1
        self._log(f"命中 {describe_task(task)} → 点击 ({target[0]}, {target[1]})")
        if self.on_hit:
            try:
                self.on_hit(task, hit)
            except Exception:
                pass
        if task.get("stop_after_hit"):
            task["enabled"] = False
        if self.config.get("recognize.notify_on_hit", True):
            self._beep()

    @staticmethod
    def _beep() -> None:
        try:
            import winsound
            winsound.Beep(880, 80)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def test_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """立即检测一次（不点击），返回结果给界面显示。"""
        region = self._region(task)
        image = S.grab(region)
        info: Dict[str, Any] = {"ok": False, "message": "", "region": region}
        try:
            kind = task.get("kind", "text")
            if kind == "text":
                hit = self._match_text(task, image, region)
                engine = self.engine()
                if engine is None or not getattr(engine, "available", False):
                    info["message"] = "OCR 引擎不可用，请检查系统语言包"
                    return info
                if hit:
                    info.update(ok=True, x=hit["screen_x"], y=hit["screen_y"],
                                w=hit.get("w", 0), h=hit.get("h", 0),
                                message=f"找到「{hit['text'] or hit['keyword']}」")
                else:
                    result = engine.recognize(image)
                    sample = (getattr(result, "text", "") or "").replace("\n", " ")[:80]
                    info["message"] = f"没找到。这一屏识别到的文字：{sample or '（空）'}"
            elif kind == "image":
                hit = self._match_image(task, image, region)
                if hit:
                    info.update(ok=True, x=hit["screen_x"], y=hit["screen_y"],
                                w=hit.get("w", 0), h=hit.get("h", 0),
                                message=f"找到图片，相似度 {hit['score']:.0%}")
                else:
                    info["message"] = "没找到这张图片，可以降低相似度或换个范围"
            elif kind == "color":
                hit = self._match_color(task, image, region)
                if hit:
                    info.update(ok=True, x=hit["screen_x"], y=hit["screen_y"], w=1, h=1,
                                message="找到该颜色")
                else:
                    info["message"] = "没找到该颜色，可以调大容差"
            else:
                info["message"] = "未知任务类型"
        except Exception as exc:
            info["message"] = f"测试出错：{exc}"
        return info
