"""配置读写。

设计要点：
- 便携模式：所有配置保存在程序目录下的 ``config/`` 里，拷贝整个文件夹就能搬家。
- 读取时用默认值深合并，缺字段永远不会 KeyError（便于以后加功能）。
- 保存用「先写临时文件再替换」，避免断电/崩溃把配置文件写坏。
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _app_root() -> Path:
    env = os.environ.get("AUTOCLICKER_HOME")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = _app_root()
CONFIG_DIR = ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "settings.json"
PROFILE_DIR = CONFIG_DIR / "profiles"
ASSETS_DIR = ROOT / "assets"
TEMPLATE_DIR = CONFIG_DIR / "templates"
MACRO_DIR = CONFIG_DIR / "macros"

APP_TITLE = "连点器 AutoClicker Pro"

DEFAULT_HOTKEYS: Dict[str, str] = {
    "toggle": "F6",             # 开始 / 暂停
    "once": "F7",               # 单次点击
    "delayed_start": "F8",      # 倒计时 3 秒开始
    "save_position": "F9",      # 保存当前鼠标位置到"位置 1"
    "toggle_window": "F10",     # 显示 / 隐藏主界面
    "emergency_stop": "ESC",    # 紧急停止
    "quit": "Ctrl+Alt+Q",       # 退出
    "hold_to_click": "MouseX2",  # 按住持续连点
}

HOTKEY_LABELS = {
    "toggle": "开始 / 暂停",
    "once": "单次点击",
    "delayed_start": "倒计时开始",
    "save_position": "保存鼠标位置",
    "toggle_window": "显示 / 隐藏界面",
    "emergency_stop": "紧急停止",
    "quit": "退出程序",
    "hold_to_click": "按住连点",
}


def new_position(name: str = "", x: int = 0, y: int = 0, **extra: Any) -> Dict[str, Any]:
    """新建一个位置卡片。"""
    data: Dict[str, Any] = {
        "id": uuid.uuid4().hex[:8],
        "name": name or "新位置",
        "x": int(x),
        "y": int(y),
        "relative": False,          # 是否跟随目标窗口
        "window_title": "",         # 记录时的窗口标题（用于窗口跟随 / 提示）
        "offset_x": 0,              # 相对窗口客户区偏移
        "offset_y": 0,
        "hotkey": "",               # 该位置专属热键（可空）
    }
    data.update(extra)
    return data


def default_positions() -> List[Dict[str, Any]]:
    return [
        new_position("位置 1", 0, 0),
        new_position("位置 2", 0, 0),
        new_position("位置 3", 0, 0),
        new_position("位置 4", 0, 0),
    ]


DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    # ---------------- 连点 ----------------
    "click": {
        "button": "left",            # left / right / middle / x1 / x2 / wheel_up / wheel_down
        "action": "single",          # single / double / triple / hold / drag
        "hold_ms": 60,               # 长按或每次按下保持的毫秒数
        "double_click_interval_ms": 40,
        "interval_mode": "cps",      # cps（每秒次数）/ interval（固定毫秒）/ random（随机毫秒区间）
        "cps": 10.0,
        "interval_ms": 100,
        "random_min_ms": 80,
        "random_max_ms": 150,
        "gaussian": False,           # 随机模式下用正态分布（更像真人）
        "limit_mode": "infinite",    # infinite / count / duration
        "max_count": 1000,
        "max_minutes": 5.0,
        "start_delay_s": 3.0,
        "target_mode": "fixed",      # fixed（用位置卡片）/ cursor（跟着鼠标走）
        "position_id": "",           # 绑定位置卡片的 id
        "position_index": 0,         # 兼容用：第几个位置
        # 拟人化
        "humanize": False,
        "jitter_percent": 15.0,
        "offset_px": 3,
        "micro_pause_every": 50,
        "micro_pause_min_s": 0.5,
        "micro_pause_max_s": 2.0,
        "miss_chance": 0.0,
        # 安全 / 目标限定
        "foreground_only": False,
        "background_mode": False,
        "target_window_title": "",
        # 拖拽参数
        "drag_dx": 150,
        "drag_dy": 0,
        "drag_duration_ms": 300,
        "preset": "precise",         # precise / human
    },
    # ---------------- 位置收藏 ----------------
    "positions": default_positions(),
    # ---------------- 智能识别 ----------------
    "recognize": {
        "enabled": False,
        "poll_interval_s": 0.5,
        "region": None,              # [x, y, w, h] 屏幕坐标；None = 全屏
        "stop_when_not_found": False,
        "notify_on_hit": True,
        "tasks": [],                 # 识别任务列表
        "ocr": {
            "engine": "auto",        # auto / windows / rapidocr / tesseract
            "lang": "zh-Hans-CN",
            "ignore_case": True,
            "ignore_space": True,
        },
    },
    # ---------------- 录制回放 ----------------
    "record": {
        "ignore_move": False,
        "speed": 1.0,
        "loop": 1,
        "ignore_delay": False,
        "macros": [],                # [{id,name,events:[...],created}]
    },
    # ---------------- 热键 ----------------
    "hotkeys": dict(DEFAULT_HOTKEYS),
    # ---------------- 安全 ----------------
    "safety": {
        "emergency_enabled": True,
        "corner_stop_enabled": True,
        "corner": "topleft",
        "corner_margin": 5,
        "cps_confirm_above": 50,
        "confirm_long_run": True,
    },
    # ---------------- 通用 ----------------
    "general": {
        "theme": "dark",
        "language": "zh",
        "minimize_to_tray": True,
        "mini_panel": False,
        "mini_on_top": True,
        "show_overlay": False,
        "autostart": False,
        "first_run": True,
        "active_profile": "默认配置",
        "window_geometry": "",
    },
    # ---------------- 计划任务 ----------------
    "schedule": {
        "enabled": False,
        "mode": "once",              # once（指定时刻）/ daily（每天）/ delay（N 分钟后）
        "time": "12:00",
        "delay_minutes": 5,
        "run_minutes": 0,            # 0 = 不限
    },
}


def _merge_inplace(target: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """就地合并（不换对象）。

    这点很重要：界面上很多地方（位置卡片、识别任务卡片）持有配置里 dict 的引用，
    如果合并时把整棵树换成新对象，那些引用就会指向旧数据，用户的修改会莫名其妙丢失。
    """
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_inplace(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """把 override 合并进 base 的副本；只对 dict 递归，list 直接替换。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """线程安全的配置对象。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else CONFIG_FILE
        self._lock = threading.RLock()
        self.data: Dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    # ---------- 读写 ----------
    def load(self) -> None:
        raw: Dict[str, Any] = {}
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as fh:
                    raw = json.load(fh) or {}
        except Exception:
            raw = {}
        with self._lock:
            self.data = _deep_merge(DEFAULT_CONFIG, raw)
            self._normalize()

    def save(self) -> None:
        with self._lock:
            payload = copy.deepcopy(self.data)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
        except Exception:
            # 配置保存失败不该让程序崩
            pass

    def _normalize(self) -> None:
        data = self.data
        positions = data.get("positions")
        if not isinstance(positions, list) or not positions:
            data["positions"] = default_positions()
        for item in data["positions"]:
            if not item.get("id"):
                item["id"] = uuid.uuid4().hex[:8]
        tasks = data.get("recognize", {}).get("tasks")
        if not isinstance(tasks, list):
            data["recognize"]["tasks"] = []
        hk = data.get("hotkeys", {})
        for key, value in DEFAULT_HOTKEYS.items():
            hk.setdefault(key, value)

    # ---------- 访问 ----------
    def get(self, path: str, default: Any = None) -> Any:
        """``cfg.get("click.cps")`` 形式的点号访问。"""
        with self._lock:
            node: Any = self.data
            for part in path.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return default
            return copy.deepcopy(node) if isinstance(node, (dict, list)) else node

    def get_ref(self, path: str, default: Any = None) -> Any:
        """返回配置里的**原始**列表/字典（不复制）。

        需要就地修改配置里的子对象时必须用它——比如识别任务要累加命中次数、
        录制列表要改名。用 ``get()`` 拿到的是副本，改了会悄悄丢掉。
        """
        with self._lock:
            node: Any = self.data
            for part in path.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return default
            return node

    def set(self, path: str, value: Any) -> None:
        with self._lock:
            parts = path.split(".")
            node = self.data
            for part in parts[:-1]:
                if not isinstance(node.get(part), dict):
                    node[part] = {}
                node = node[part]
            node[parts[-1]] = value

    def update(self, mapping: Dict[str, Any]) -> None:
        with self._lock:
            _merge_inplace(self.data, mapping)

    # ---------- 位置卡片 ----------
    def positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return self.data.setdefault("positions", default_positions())

    def find_position(self, position_id: str) -> Optional[Dict[str, Any]]:
        for item in self.positions():
            if item.get("id") == position_id:
                return item
        return None

    def active_position(self) -> Optional[Dict[str, Any]]:
        """当前连点选中的位置卡片。"""
        pid = self.get("click.position_id", "")
        if pid:
            found = self.find_position(pid)
            if found:
                return found
        positions = self.positions()
        index = int(self.get("click.position_index", 0) or 0)
        if 0 <= index < len(positions):
            return positions[index]
        return positions[0] if positions else None

    def add_position(self, name: str = "", x: int = 0, y: int = 0) -> Dict[str, Any]:
        item = new_position(name or f"位置 {len(self.positions()) + 1}", x, y)
        with self._lock:
            self.data.setdefault("positions", []).append(item)
        return item

    def remove_position(self, position_id: str) -> bool:
        with self._lock:
            positions = self.data.get("positions", [])
            for index, item in enumerate(positions):
                if item.get("id") == position_id:
                    positions.pop(index)
                    if self.data["click"].get("position_id") == position_id:
                        self.data["click"]["position_id"] = ""
                    return True
        return False

    # ---------- 配置档 ----------
    def profile_names(self) -> List[str]:
        names = ["默认配置"]
        try:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            for path in sorted(PROFILE_DIR.glob("*.json")):
                names.append(path.stem)
        except Exception:
            pass
        return names

    def save_profile(self, name: str) -> Path:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip() or "未命名"
        target = PROFILE_DIR / f"{safe}.json"
        with self._lock:
            payload = copy.deepcopy(self.data)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        self.set("general.active_profile", safe)
        self.save()
        return target

    def load_profile(self, name: str) -> bool:
        if name in ("", "默认配置"):
            self.set("general.active_profile", "默认配置")
            return True
        target = PROFILE_DIR / f"{name}.json"
        if not target.exists():
            return False
        try:
            with open(target, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            return False
        with self._lock:
            self.data = _deep_merge(DEFAULT_CONFIG, raw)
            self._normalize()
            self.data["general"]["active_profile"] = name
        self.save()
        return True

    def export_to(self, path: Path) -> None:
        with self._lock:
            payload = copy.deepcopy(self.data)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def import_from(self, path: Path) -> bool:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            return False
        with self._lock:
            self.data = _deep_merge(DEFAULT_CONFIG, raw)
            self._normalize()
        self.save()
        return True


# 全局单例（GUI 与各引擎共用）
config = Config()


def ensure_dirs() -> None:
    for path in (CONFIG_DIR, PROFILE_DIR, ASSETS_DIR, TEMPLATE_DIR, MACRO_DIR):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
