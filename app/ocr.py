"""OCR 抽象层与各后端实现（连点器「智能识别」用）。

设计要点
--------
1. **接口稳定**：对外只暴露 ``OcrLine`` / ``OcrResult`` / ``OcrEngine`` 及其三个子类，
   以及 ``list_engines`` / ``get_engine`` / ``find_text`` / ``find_keywords``。
   调用方（GUI 线程）拿到的永远是一个 ``OcrResult``，**任何情况下都不抛异常**。
2. **常驻进程复用**：``WindowsOcrEngine`` 走 ``powershell.exe`` 常驻子进程
   （``app/ocr_bridge.ps1``），一行一个 JSON 通信。首次调用约 1 秒（启动 PS + 建引擎），
   之后单次 20~50ms。子进程崩溃/超时会被杀掉，下次调用自动重建。
3. **图像传输用 PNG**：Windows OCR 需要 ``SoftwareBitmap``，而管道是 base64 文本。
   实测 PNG 编码虽贵一点，但体积小 500 倍（1920x1080 截图约 20KB vs 未压缩 BMP 11MB），
   端到端 79ms vs 430ms，所以默认 PNG；协议里用 ``format`` 字段区分，BMP 也仍然支持。
4. **坐标**：桥返回的是发送图片的像素坐标；本模块把发送前放大的缩放比换算回原图坐标。
5. **中文空格**：同一行内相邻 word 若「前一个以非 ASCII 结尾 且 后一个以非 ASCII 开头」
   则不插空格，否则插一个空格。规则在 PS 端与 Python 端各实现一份（``join_words``）。

依赖：numpy、Pillow。没有网络，不安装任何东西。
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:  # Pillow 是硬依赖，但导入失败也不该让整个模块炸掉
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]


# --------------------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------------------

DEFAULT_LANG = "zh-Hans-CN"

# 小图放大：宽或高小于 SMALL_EDGE_TRIGGER 就放大
SMALL_EDGE_TRIGGER = 600      # 宽或高小于它就放大
SMALL_EDGE_TARGET = 800       # 放大到最短边 >= 这个值
MAX_UPSCALE = 16              # 放大倍数上限（极端小图的安全阀，正常不会碰到）

OCR_TIMEOUT_S = 15.0         # 单次识别超时，超时杀进程并重建
START_TIMEOUT_S = 20.0       # 进程启动 + 首条响应超时

BRIDGE_SCRIPT = Path(__file__).resolve().parent / "ocr_bridge.ps1"
POWERSHELL = os.environ.get("AUTOCLICKER_POWERSHELL", "powershell.exe")

_ENGINE_ORDER = ("windows", "rapidocr", "tesseract")


# --------------------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------------------

@dataclass
class OcrLine:
    """一行识别结果；x/y/w/h 是相对「传入 recognise 的那张图」左上角的像素框。"""

    text: str
    x: int
    y: int
    w: int
    h: int
    confidence: float = 1.0


@dataclass
class OcrResult:
    """一次识别的全部结果。"""

    text: str = ""
    lines: List[OcrLine] = field(default_factory=list)
    engine: str = ""

    def __bool__(self) -> bool:
        return bool(self.lines) or bool(self.text.strip())


# --------------------------------------------------------------------------------------
# 纯函数工具
# --------------------------------------------------------------------------------------

def _is_ascii(ch: str) -> bool:
    """数字/字母算 ASCII（与 PS 端 Test-Ascii 保持一致）。"""
    return ("0" <= ch <= "9") or ("A" <= ch <= "Z") or ("a" <= ch <= "z")


def join_words(words: Sequence[str]) -> str:
    """按规则拼接同一行内的 word。

    相邻两个 word：前一个以非 ASCII 结尾、后一个以非 ASCII 开头 -> 不插空格；
    其它情况插一个空格。这样「立」「即」「抢」「购」拼回「立即抢购」，
    而「Add」「to」「cart」拼回「Add to cart」。
    """
    out: List[str] = []
    prev_ascii = False
    for raw in words:
        text = raw or ""
        if not text:
            continue
        head_ascii = _is_ascii(text[0])
        tail_ascii = _is_ascii(text[-1])
        if out and not (not prev_ascii and not head_ascii):
            out.append(" ")
        out.append(text)
        prev_ascii = tail_ascii
    return "".join(out)


def _bmp_bgra_bytes(arr: np.ndarray) -> bytes:
    """HxWx3 RGB uint8 -> BMP 文件字节（BITMAPINFOHEADER, 32bpp BGRA, bottom-up）。

    故意不压缩：numpy 切片转换 + 一次 tobytes，比走 PNG 编码快得多，
    而桥那边也省掉一次 PNG 解码。
    """
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("图像必须是 HxWx3 的 RGB 数组")
    height, width = int(arr.shape[0]), int(arr.shape[1])
    src = np.ascontiguousarray(arr[:, :, :3], dtype=np.uint8)

    bgra = np.empty((height, width, 4), dtype=np.uint8)
    bgra[:, :, 0] = src[:, :, 2]   # B
    bgra[:, :, 1] = src[:, :, 1]   # G
    bgra[:, :, 2] = src[:, :, 0]   # R
    bgra[:, :, 3] = 255            # A

    pixels = np.ascontiguousarray(bgra[::-1]).tobytes()   # BMP 自下而上
    header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(pixels), 0, 0, 14 + 40)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 32, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


def _png_bytes(arr: np.ndarray) -> bytes:
    """HxWx3 RGB uint8 -> PNG 字节（BMP 传输不可用时兜底）。"""
    import io

    buf = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(arr[:, :, :3], dtype=np.uint8), mode="RGB").save(buf, format="PNG")
    return buf.getvalue()


def _encode_image(arr: np.ndarray, prefer: str = "png") -> Tuple[str, str]:
    """返回 ``(format, base64)``。

    默认走 PNG：实测一张 1920x1080 截图 PNG 只有约 20KB，
    而未经压缩的 BMP 要 11MB（base64 后更大），管道往返 79ms vs 430ms。
    BMP 仍然支持（``prefer="bmp"``，桥那边靠文件头自动识别），只是没有优势。
    """
    if prefer == "bmp":
        try:
            return "bmp", base64.b64encode(_bmp_bgra_bytes(arr)).decode("ascii")
        except Exception:
            pass
    try:
        return "png", base64.b64encode(_png_bytes(arr)).decode("ascii")
    except Exception:
        return "bmp", base64.b64encode(_bmp_bgra_bytes(arr)).decode("ascii")


def upscale_for_ocr(arr: np.ndarray) -> Tuple[np.ndarray, float]:
    """小图放大。返回 ``(新图, 缩放比)``；不需要放大时原样返回、比例为 1.0。

    规则：宽或高 < ``SMALL_EDGE_TRIGGER``（600）就按整数倍用 LANCZOS 放大，
    直到最短边 >= ``SMALL_EDGE_TARGET``（800）；倍数上限 ``MAX_UPSCALE``。
    用整数倍是为了让坐标换算没有取整误差。
    实测（见 tests/test_ocr.py + 开发期基准）：
      - 200x80 -> x8  = 1600x640（最短边 640，已到上限；约 58ms）
      - 140x50 -> x16 = 2240x800（受 MAX_UPSCALE 限制，正常尺寸不会碰到）
      - 10x10  -> x16 = 160x160
    注意：Windows OCR 对放大并不敏感（开发期测到 1x~15x 都能认对），
    所以这里不为了「快」而牺牲规范要求的放大倍数。
    """
    if arr is None or getattr(arr, "ndim", 0) != 3:
        return arr, 1.0
    height, width = int(arr.shape[0]), int(arr.shape[1])
    if height <= 0 or width <= 0:
        return arr, 1.0
    if width >= SMALL_EDGE_TRIGGER and height >= SMALL_EDGE_TRIGGER:
        return arr, 1.0
    if Image is None:
        return arr, 1.0

    shortest = min(width, height)
    if shortest <= 0:
        return arr, 1.0

    factor = max(2, -(-SMALL_EDGE_TARGET // shortest))   # 向上取整的整数倍
    factor = min(factor, MAX_UPSCALE)
    if factor <= 1:
        return arr, 1.0

    try:
        source = Image.fromarray(np.ascontiguousarray(arr[:, :, :3].astype(np.uint8)), mode="RGB")
        enlarged = source.resize((width * factor, height * factor), Image.LANCZOS)
        return np.asarray(enlarged, dtype=np.uint8), float(factor)
    except Exception:
        return arr, 1.0


def _normalize_engine_name(prefer: Optional[str]) -> Optional[str]:
    if prefer is None:
        return None
    name = str(prefer).strip().lower()
    if name in ("", "auto", "default"):
        return None
    aliases = {"win": "windows", "windowsocr": "windows", "windows_ocr": "windows",
               "rapid": "rapidocr", "rapid_ocr": "rapidocr", "onnx": "rapidocr",
               "tess": "tesseract", "tesseractocr": "tesseract"}
    return aliases.get(name, name)


# --------------------------------------------------------------------------------------
# 抽象基类
# --------------------------------------------------------------------------------------

class OcrEngine:
    """OCR 后端抽象。

    子类只要实现 ``_probe`` / ``_recognize_impl`` / ``_close_impl``。
    ``recognize`` 是唯一对外入口，负责：参数校验、异常兜底、结果规范化。
    """

    name: str = "base"

    def __init__(self, lang: str = DEFAULT_LANG) -> None:
        self.lang = lang or DEFAULT_LANG
        self.available = False
        self.last_error = ""
        try:
            self.available = bool(self._probe())
        except Exception as exc:  # 探测本身也绝不能抛
            self.available = False
            self.last_error = f"探测失败: {exc}"

    # ---------- 子类实现 ----------
    def _probe(self) -> bool:
        return False

    def _recognize_impl(self, arr: np.ndarray) -> OcrResult:
        return OcrResult(engine=self.name)

    def _close_impl(self) -> None:
        return None

    # ---------- 对外 ----------
    def recognize(self, image: Any) -> OcrResult:
        """识别一张 HxWx3 RGB uint8 图片。失败时返回空 ``OcrResult``，绝不抛异常。"""
        try:
            arr = self._to_array(image)
        except Exception as exc:
            self.last_error = f"图像转换失败: {exc}"
            return OcrResult(engine=self.name)

        if arr is None or arr.size == 0:
            return OcrResult(engine=self.name)

        try:
            result = self._recognize_impl(arr)
        except Exception as exc:  # 兜底：任何后端异常都不能冒到 GUI
            self.available = False
            self.last_error = f"识别失败: {exc}"
            return OcrResult(engine=self.name)

        if result is None:
            return OcrResult(engine=self.name)
        if not result.engine:
            result.engine = self.name

        # 统一裁剪到原图范围内并保证类型正确
        height, width = int(arr.shape[0]), int(arr.shape[1])
        for line in result.lines:
            line.x = max(0, min(int(line.x), max(0, width - 1)))
            line.y = max(0, min(int(line.y), max(0, height - 1)))
            line.w = max(0, int(line.w))
            line.h = max(0, int(line.h))
            if not line.text:
                line.text = ""
        return result

    def close(self) -> None:
        """释放资源；可重复调用，绝不抛异常。"""
        try:
            self._close_impl()
        except Exception:
            pass

    # ---------- 内部 ----------
    @staticmethod
    def _to_array(image: Any) -> Optional[np.ndarray]:
        if image is None:
            return None
        if isinstance(image, np.ndarray):
            arr = image
        elif Image is not None and isinstance(image, Image.Image):
            arr = np.asarray(image.convert("RGB"))
        elif isinstance(image, (bytes, bytearray)):
            if Image is None:
                return None
            import io

            arr = np.asarray(Image.open(io.BytesIO(bytes(image))).convert("RGB"))
        else:  # 任何支持 __array__ 的对象
            arr = np.asarray(image)

        if arr.ndim == 2:                      # 灰度 -> RGB
            arr = np.stack([arr] * 3, axis=-1)
        elif arr.ndim == 3 and arr.shape[2] == 4:
            arr = arr[:, :, :3]
        if arr.ndim != 3 or arr.shape[2] < 3:
            return None
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        return np.ascontiguousarray(arr[:, :, :3])

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name!r} available={self.available}>"


# --------------------------------------------------------------------------------------
# Windows OCR（PowerShell 常驻桥）
# --------------------------------------------------------------------------------------

class WindowsOcrEngine(OcrEngine):
    """走 ``powershell.exe`` + ``Windows.Media.Ocr`` 的常驻桥。"""

    name = "windows"

    def __init__(self, lang: str = DEFAULT_LANG, timeout: float = OCR_TIMEOUT_S) -> None:
        # 这些实例属性先于 super().__init__() 设置：
        # _probe() 内部会用到 timeout，而 recognize() 会用到 _lock。
        # 这样即使探测过程中出现意料之外的异常，对象也仍然是可用的（不会 AttributeError）。
        self.timeout = float(timeout)
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._next_id = 0
        self._pending: Dict[int, Tuple[threading.Event, Dict[str, Any]]] = {}
        self._reader: Optional[threading.Thread] = None
        self._err_thread: Optional[threading.Thread] = None
        self._stderr_tail: List[str] = []
        super().__init__(lang=lang)
        atexit.register(self.close)

    # ---------- 探测 ----------
    def _probe(self) -> bool:
        if os.name != "nt":
            self.last_error = "非 Windows 平台，Windows OCR 不可用"
            return False
        if not BRIDGE_SCRIPT.is_file():
            self.last_error = f"找不到桥脚本: {BRIDGE_SCRIPT}"
            return False
        exe = shutil.which(POWERSHELL) or POWERSHELL
        if not exe:
            self.last_error = "找不到 powershell.exe"
            return False
        # 真正建一次进程，并等它的握手，确认桥能干活（失败只影响 available，不抛）
        if not self._ensure_process():
            return False
        return self._await_handshake()

    def _await_handshake(self) -> bool:
        """等桥发来的启动握手（id=0）。

        只有握手成功才认为可用：进程起来了但脚本解析失败 / 引擎建不起来时，
        桥会在握手行里带 ``ok=false``，探测阶段就能发现，而不是等到第一次识别才暴露。
        """
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                self.last_error = self._startup_error("OCR 桥启动后立即退出")
                return False
            request_id = 0
            event = threading.Event()
            box: Dict[str, Any] = {}
            self._pending[request_id] = (event, box)

        if not event.wait(START_TIMEOUT_S):
            with self._lock:
                self._pending.pop(request_id, None)
            self.last_error = self._startup_error(f"等待 OCR 桥握手超时（{START_TIMEOUT_S:.0f}s）")
            self._kill_process()
            return False

        with self._lock:
            self._pending.pop(request_id, None)

        data = box.get("data")
        if not isinstance(data, dict) or not data.get("ok", False):
            message = box.get("error") or (data or {}).get("error") or "OCR 桥握手失败"
            self.last_error = self._startup_error(str(message))
            self._kill_process()
            return False
        return True

    def _startup_error(self, message: str) -> str:
        """把 stderr 尾部拼进错误信息，便于定位（例如 PowerShell 语法错误）。"""
        with self._lock:
            tail = [line for line in self._stderr_tail[-5:] if line.strip()]
        if tail:
            return message + " | stderr: " + " / ".join(tail)[:400]
        return message

    # ---------- 进程管理 ----------
    def _powershell_exe(self) -> str:
        return shutil.which(POWERSHELL) or POWERSHELL

    def _ensure_process(self) -> bool:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return True
            self._kill_process()

            cmd = [
                self._powershell_exe(),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", str(BRIDGE_SCRIPT),
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception as exc:
                self._proc = None
                self.last_error = f"启动 OCR 桥失败: {exc}"
                self.available = False
                return False

            self._next_id = 1
            self._pending.clear()
            self._stderr_tail = []

            self._reader = threading.Thread(target=self._read_loop, name="ocr-bridge-out", daemon=True)
            self._reader.start()
            self._err_thread = threading.Thread(target=self._drain_stderr, name="ocr-bridge-err", daemon=True)
            self._err_thread.start()
            return True

    def _read_loop(self) -> None:
        """把 stdout 的 JSON 行分发给等待中的请求。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                line = (raw or "").strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except Exception:
                    # 非 JSON（理论上不该有）忽略，继续读
                    continue
                if not isinstance(data, dict):
                    continue
                req_id = data.get("id")
                try:
                    key = int(req_id)
                except Exception:
                    key = None
                with self._lock:
                    waiter = self._pending.pop(key, None)
                if waiter is not None:
                    event, box = waiter
                    box["data"] = data
                    event.set()
        except Exception:
            pass
        finally:
            # 进程结束：唤醒所有等待者，避免调用方卡满超时
            self._fail_pending("OCR 桥进程已退出")

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in proc.stderr:
                text = (raw or "").rstrip()
                if text:
                    self._stderr_tail.append(text)
                    if len(self._stderr_tail) > 40:
                        del self._stderr_tail[: len(self._stderr_tail) - 40]
        except Exception:
            pass

    def _fail_pending(self, reason: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for event, box in pending:
            box["error"] = reason
            event.set()

    def _kill_process(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        self._fail_pending("OCR 桥被关闭")
        try:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass

    # ---------- 通信 ----------
    def _request(self, payload_b64: str, image_format: str, timeout: float) -> Dict[str, Any]:
        """发一条请求并等响应。返回响应 dict；失败抛 RuntimeError。"""
        with self._lock:
            if not self._ensure_process():
                raise RuntimeError(self.last_error or "OCR 桥不可用")
            proc = self._proc
            assert proc is not None and proc.stdin is not None
            req_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            box: Dict[str, Any] = {}
            self._pending[req_id] = (event, box)
            message = {
                "id": req_id,
                "image": payload_b64,
                "format": image_format,
                "lang": self.lang,
            }
            try:
                proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except Exception as exc:
                self._pending.pop(req_id, None)
                self._kill_process()
                raise RuntimeError(f"写入 OCR 桥失败: {exc}") from exc

        if not event.wait(timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            self._kill_process()          # 超时：杀掉，下次自动重建
            raise RuntimeError(f"OCR 桥响应超时（{timeout:.0f}s）")

        if "error" in box and box.get("error"):
            raise RuntimeError(str(box["error"]))
        data = box.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("OCR 桥返回了非预期数据")
        if not data.get("ok", False):
            raise RuntimeError(str(data.get("error") or "OCR 桥返回错误"))
        return data

    # ---------- 识别 ----------
    def _recognize_impl(self, arr: np.ndarray) -> OcrResult:
        # 小图先放大，坐标最后按同一比例换回
        working, scale = upscale_for_ocr(arr)

        try:
            image_format, payload = _encode_image(working, prefer="png")
        except Exception as exc:
            self.last_error = f"图像编码失败: {exc}"
            return OcrResult(engine=self.name)

        timeout = self.timeout if scale == 1.0 else max(self.timeout, self.timeout * 2)
        try:
            data = self._request(payload, image_format, timeout)
        except Exception as exc:
            # 启动/超时/解析失败：返回空结果并标记不可用（下次调用会重建）
            self.last_error = str(exc)
            self.available = False
            return OcrResult(engine=self.name)

        self.available = True
        return self._parse_response(data, scale)

    def _parse_response(self, data: Dict[str, Any], scale: float) -> OcrResult:
        lines: List[OcrLine] = []
        raw_lines = data.get("lines") or []
        if not isinstance(raw_lines, list):
            raw_lines = []

        for item in raw_lines:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "") or "")
            try:
                x = int(float(item.get("x", 0) or 0))
                y = int(float(item.get("y", 0) or 0))
                w = int(float(item.get("w", 0) or 0))
                h = int(float(item.get("h", 0) or 0))
            except Exception:
                x = y = w = h = 0
            if scale != 1.0 and scale > 0:
                # 坐标是放大图的，换算回原图（向下取整）
                x = int(x // scale)
                y = int(y // scale)
                w = int(w // scale)
                h = int(h // scale)
            lines.append(OcrLine(text=text, x=x, y=y, w=w, h=h))

        text = data.get("text")
        if not isinstance(text, str) or not text:
            text = "\n".join(line.text for line in lines)
        return OcrResult(text=text, lines=lines, engine=self.name)

    def _close_impl(self) -> None:
        with self._lock:
            self._kill_process()

    def restart(self) -> bool:
        """杀掉当前桥进程；下一次 ``recognize()`` 会自动重建。"""
        with self._lock:
            self._kill_process()
        return self._ensure_process()


# --------------------------------------------------------------------------------------
# RapidOCR（可选，装不到就 available=False）
# --------------------------------------------------------------------------------------

class RapidOcrEngine(OcrEngine):
    """rapidocr_onnxruntime 封装。没装就 ``available=False``，不安装任何东西。"""

    name = "rapidocr"

    def __init__(self, lang: str = DEFAULT_LANG) -> None:
        self._engine: Any = None
        super().__init__(lang=lang)

    def _probe(self) -> bool:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except Exception as exc:
            self.last_error = f"未安装 rapidocr_onnxruntime: {exc}"
            return False
        try:
            self._engine = RapidOCR()
        except Exception as exc:
            self.last_error = f"初始化 RapidOCR 失败: {exc}"
            self._engine = None
            return False
        return self._engine is not None

    def _recognize_impl(self, arr: np.ndarray) -> OcrResult:
        if self._engine is None:
            self.available = False
            return OcrResult(engine=self.name)
        try:
            raw = self._engine(arr)
        except Exception as exc:
            self.last_error = f"RapidOCR 识别失败: {exc}"
            return OcrResult(engine=self.name)

        lines: List[OcrLine] = []
        # RapidOCR 返回 (result, elapse)，result 是 [[box, text, score], ...]
        payload = raw[0] if isinstance(raw, (tuple, list)) and raw and isinstance(raw[0], list) else raw
        for item in payload or []:
            try:
                box, text, score = item[0], item[1], item[2]
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                lines.append(
                    OcrLine(
                        text=str(text),
                        x=int(min(xs)),
                        y=int(min(ys)),
                        w=int(max(xs) - min(xs)),
                        h=int(max(ys) - min(ys)),
                        confidence=float(score),
                    )
                )
            except Exception:
                continue
        return OcrResult(text="\n".join(line.text for line in lines), lines=lines, engine=self.name)

    def _close_impl(self) -> None:
        self._engine = None


# --------------------------------------------------------------------------------------
# Tesseract（可选，装不到就 available=False）
# --------------------------------------------------------------------------------------

class TesseractOcrEngine(OcrEngine):
    """pytesseract + tesseract 可执行文件封装。没装就 ``available=False``。"""

    name = "tesseract"

    def __init__(self, lang: str = "chi_sim+eng") -> None:
        self._pytesseract: Any = None
        super().__init__(lang=lang)

    def _probe(self) -> bool:
        exe = shutil.which("tesseract")
        if not exe:
            self.last_error = "PATH 里找不到 tesseract.exe"
            return False
        try:
            import pytesseract  # type: ignore
        except Exception as exc:
            self.last_error = f"未安装 pytesseract: {exc}"
            return False
        self._pytesseract = pytesseract
        try:
            pytesseract.pytesseract.tesseract_cmd = exe
            pytesseract.get_tesseract_version()
        except Exception as exc:
            self.last_error = f"tesseract 不可用: {exc}"
            return False
        return True

    def _recognize_impl(self, arr: np.ndarray) -> OcrResult:
        if self._pytesseract is None or Image is None:
            self.available = False
            return OcrResult(engine=self.name)
        try:
            image = Image.fromarray(np.ascontiguousarray(arr[:, :, :3], dtype=np.uint8), mode="RGB")
            data = self._pytesseract.image_to_data(
                image, lang=self.lang, output_type=self._pytesseract.Output.DICT
            )
        except Exception as exc:
            self.last_error = f"Tesseract 识别失败: {exc}"
            return OcrResult(engine=self.name)

        # 按 (block, par, line) 聚合成行，保留 Tesseract 自己的分词
        grouped: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
        count = len(data.get("text", []))
        for index in range(count):
            word = (data["text"][index] or "").strip()
            if not word:
                continue
            key = (
                int(data["block_num"][index]),
                int(data["par_num"][index]),
                int(data["line_num"][index]),
            )
            grouped.setdefault(key, []).append(
                {
                    "text": word,
                    "x": int(data["left"][index]),
                    "y": int(data["top"][index]),
                    "w": int(data["width"][index]),
                    "h": int(data["height"][index]),
                    "conf": float(data.get("conf", [0] * count)[index] or 0) / 100.0,
                }
            )

        lines: List[OcrLine] = []
        for key in sorted(grouped):
            words = sorted(grouped[key], key=lambda item: item["x"])
            text = join_words([word["text"] for word in words])
            left = min(word["x"] for word in words)
            top = min(word["y"] for word in words)
            right = max(word["x"] + word["w"] for word in words)
            bottom = max(word["y"] + word["h"] for word in words)
            conf = sum(word["conf"] for word in words) / max(1, len(words))
            lines.append(OcrLine(text=text, x=left, y=top, w=right - left, h=bottom - top, confidence=conf))
        return OcrResult(text="\n".join(line.text for line in lines), lines=lines, engine=self.name)

    def _close_impl(self) -> None:
        self._pytesseract = None


# --------------------------------------------------------------------------------------
# 工厂 / 查询辅助
# --------------------------------------------------------------------------------------

_ENGINE_CLASSES = {
    "windows": WindowsOcrEngine,
    "rapidocr": RapidOcrEngine,
    "tesseract": TesseractOcrEngine,
}


def _make_engine(name: str, lang: str = DEFAULT_LANG) -> OcrEngine:
    cls = _ENGINE_CLASSES.get(name, WindowsOcrEngine)
    try:
        return cls(lang=lang)
    except Exception:
        pass
    # 构造都失败时给一个「不可用」的 Windows 引擎兜底。
    # 必须把 WindowsOcrEngine 依赖的实例属性补齐，否则 recognize() 可能抛 AttributeError
    # （调用方在 GUI 线程里用，抛异常会崩界面）。
    try:
        engine = WindowsOcrEngine.__new__(WindowsOcrEngine)
        engine.timeout = OCR_TIMEOUT_S
        engine._proc = None
        engine._lock = threading.RLock()
        engine._next_id = 0
        engine._pending = {}
        engine._reader = None
        engine._err_thread = None
        engine._stderr_tail = []
        OcrEngine.__init__(engine, lang=DEFAULT_LANG)
        return engine
    except Exception:
        return OcrEngine(lang=DEFAULT_LANG)


def list_engines() -> List[Tuple[str, bool]]:
    """``[(名字, 是否可用)]``，固定顺序 windows > rapidocr > tesseract。"""
    result: List[Tuple[str, bool]] = []
    for name in _ENGINE_ORDER:
        engine = _make_engine(name)
        result.append((name, bool(engine.available)))
        try:
            engine.close()
        except Exception:
            pass
    return result


def get_engine(prefer: Optional[str] = None) -> OcrEngine:
    """按 prefer 取引擎；``None``/``"auto"`` 时按 windows > rapidocr > tesseract 选第一个可用的。

    全部不可用时返回 ``available=False`` 的 ``WindowsOcrEngine``，不抛异常。
    """
    if prefer is None or str(prefer).strip().lower() in ("", "auto", "default"):
        for name in _ENGINE_ORDER:
            engine = _make_engine(name)
            if engine.available:
                return engine
            try:
                engine.close()
            except Exception:
                pass
        # 都不可用：返回不可用的 Windows 引擎（惰性重建，之后仍可能恢复）
        return _make_engine("windows")

    name = _normalize_engine_name(prefer) or "windows"
    if name not in _ENGINE_CLASSES:
        return _make_engine("windows")
    return _make_engine(name)


def find_text(
    result: OcrResult,
    keyword: str,
    ignore_case: bool = True,
    ignore_space: bool = True,
) -> List[OcrLine]:
    """在 ``result.lines`` 里找包含 keyword 的行，返回命中的行（可能多行）。"""
    hits: List[OcrLine] = []
    if result is None or not keyword:
        return hits

    def normalize(text: str) -> str:
        value = text or ""
        if ignore_space:
            value = "".join(value.split())
        if ignore_case:
            value = value.lower()
        return value

    needle = normalize(keyword)
    if not needle:
        return hits
    for line in result.lines or []:
        if needle in normalize(line.text):
            hits.append(line)
    return hits


def find_keywords(
    result: OcrResult,
    keywords: List[str],
    mode: str = "any",
    **kw: Any,
) -> List[Tuple[str, OcrLine]]:
    """按关键字列表查找。

    - ``mode="any"``：返回所有命中的 ``(keyword, line)``（同一 keyword 命中多行会各算一条）。
    - ``mode="all"``：只有每个 keyword 都至少命中一行，才返回全部命中，否则返回 ``[]``。
    """
    hits: List[Tuple[str, OcrLine]] = []
    if result is None or not keywords:
        return hits

    matched_all = True
    for keyword in keywords:
        found = find_text(result, keyword, **kw)
        if not found:
            matched_all = False
            continue
        for line in found:
            hits.append((keyword, line))

    if str(mode).lower() == "all":
        return hits if matched_all else []
    return hits


__all__ = [
    "OcrLine",
    "OcrResult",
    "OcrEngine",
    "WindowsOcrEngine",
    "RapidOcrEngine",
    "TesseractOcrEngine",
    "list_engines",
    "get_engine",
    "find_text",
    "find_keywords",
    "join_words",
    "upscale_for_ocr",
    "DEFAULT_LANG",
]
