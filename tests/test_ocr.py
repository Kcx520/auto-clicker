"""OCR 模块自测。

运行（必须用这个 Python，路径里的反斜杠用 r-string 或正斜杠，避免转义问题）:

    python.exe tests/test_ocr.py

其中 python.exe 指:
    python

说明
----
- 本文件只使用 ASCII 字符（中文短语写成 ``\\uXXXX`` 转义），避免任何编码问题。
- 中文识别结果既打印到 stdout，也会以 UTF-8 写入临时文件；
  若控制台编码不是 UTF-8 导致显示为乱码，直接看那个文件即可。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import ocr as ocr_mod  # noqa: E402
from app.ocr import (  # noqa: E402
    OcrEngine,
    OcrLine,
    OcrResult,
    RapidOcrEngine,
    TesseractOcrEngine,
    WindowsOcrEngine,
    find_keywords,
    find_text,
    get_engine,
    list_engines,
)

# ---------------------------------------------------------------------------
# 被测中文短语（写成转义，源码保持纯 ASCII）
# ---------------------------------------------------------------------------
BUY_NOW = "\u7acb\u5373\u62a2\u8d2d"          # 立即抢购
START_GAME = "\u5f00\u59cb\u6e38\u620f"        # 开始游戏
CONFIRM = "\u786e\u5b9a"                       # 确定
NOT_PRESENT = "\u4e0d\u5b58\u5728\u7684"       # 不存在的

FONT_SIZE = 40
IMG_W, IMG_H = 700, 220
BUY_POS = (40, 30)          # 「立即抢购」绘制位置（左上角锚点）
START_POS = (40, 130)       # 「开始游戏 确定」绘制位置

# 识别结果 UTF-8 落盘位置（防止控制台乱码看不到真实中文）
DUMP_PATH = Path(tempfile.gettempdir()) / "autoclicker_ocr_test_output.txt"
_dump_lines: List[str] = []


def emit(message: str = "") -> None:
    """打印 + 记录 UTF-8 落盘。"""
    _dump_lines.append(message)
    try:
        print(message)
    except UnicodeEncodeError:
        sys.stdout.write(message.encode("utf-8", "replace").decode("ascii", "replace") + "\n")
    sys.stdout.flush()


def flush_dump() -> None:
    try:
        DUMP_PATH.write_text("\n".join(_dump_lines), encoding="utf-8")
    except Exception as exc:  # pragma: no cover
        print(f"[warn] could not write dump file: {exc}")


# ---------------------------------------------------------------------------
# 结果统计
# ---------------------------------------------------------------------------
_PASSED = 0
_FAILED: List[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _PASSED
    if condition:
        _PASSED += 1
        emit(f"  [PASS] {name}" + (f"  ({detail})" if detail else ""))
    else:
        _FAILED.append(name)
        emit(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))
    return condition


def section(title: str) -> None:
    emit("")
    emit("=" * 72)
    emit(title)
    emit("=" * 72)


# ---------------------------------------------------------------------------
# 测试图生成
# ---------------------------------------------------------------------------
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def pick_font(size: int = FONT_SIZE):
    for path in FONT_CANDIDATES:
        try:
            if os.path.isfile(path):
                return ImageFont.truetype(path, size), path
        except Exception:
            continue
    raise RuntimeError("no usable system font found in C:\\Windows\\Fonts")


def make_image(
    phrases: List[Tuple[str, Tuple[int, int]]],
    size: Tuple[int, int] = (IMG_W, IMG_H),
    font_size: int = FONT_SIZE,
):
    """白底黑字生成测试图，返回 (np.ndarray RGB, 字体路径, 每条短语的绘制框)。"""
    font, font_path = pick_font(font_size)
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    boxes: List[Tuple[int, int, int, int]] = []
    for text, (x, y) in phrases:
        draw.text((x, y), text, font=font, fill=(0, 0, 0))
        left, top, right, bottom = draw.textbbox((x, y), text, font=font)
        boxes.append((left, top, right - left, bottom - top))
    return np.asarray(image, dtype=np.uint8), font_path, boxes


def normalize(text: str) -> str:
    return "".join((text or "").split())


def find_line_containing(result: OcrResult, needle: str):
    """在识别结果里找包含 needle（忽略空白）的行，返回 (line, 是否完全相等)。"""
    target = normalize(needle)
    for line in result.lines:
        if normalize(line.text) == target:
            return line, True
    for line in result.lines:
        if target in normalize(line.text):
            return line, False
    return None, False


# ---------------------------------------------------------------------------
# 1. 引擎探测
# ---------------------------------------------------------------------------
def test_engine_probe() -> WindowsOcrEngine:
    section("1. list_engines() / get_engine() / WindowsOcrEngine 探测")
    engines = list_engines()
    emit(f"  list_engines() -> {engines}")
    check("list_engines 返回 3 个后端且名字/顺序正确",
          [n for n, _ in engines] == ["windows", "rapidocr", "tesseract"], str(engines))
    check("list_engines 里 rapidocr 不可用（环境未安装）",
          dict(engines).get("rapidocr") is False)
    check("list_engines 里 tesseract 不可用（环境未安装）",
          dict(engines).get("tesseract") is False)

    auto = get_engine("auto")
    emit(f"  get_engine('auto') -> {auto!r}")
    check("get_engine('auto') 选到 windows", isinstance(auto, WindowsOcrEngine) and auto.name == "windows")
    auto.close()

    picked = get_engine()
    check("get_engine(None) 选到 windows", isinstance(picked, WindowsOcrEngine))
    picked.close()

    forced = get_engine("tesseract")
    check("get_engine('tesseract') 返回 TesseractOcrEngine 且 available=False",
          isinstance(forced, TesseractOcrEngine) and forced.available is False)
    forced.close()

    forced2 = get_engine("rapidocr")
    check("get_engine('rapidocr') 返回 RapidOcrEngine 且 available=False",
          isinstance(forced2, RapidOcrEngine) and forced2.available is False)
    forced2.close()

    bogus = get_engine("no-such-engine")
    check("get_engine('乱写的名字') 不抛异常，回落到 windows",
          isinstance(bogus, WindowsOcrEngine))
    bogus.close()

    engine = WindowsOcrEngine()
    check("WindowsOcrEngine().available is True", engine.available is True,
          f"last_error={engine.last_error!r}")
    return engine


# ---------------------------------------------------------------------------
# 2. 基本识别 + 中文不拆空格
# ---------------------------------------------------------------------------
def test_recognize(engine: WindowsOcrEngine):
    section("2. recognize() 基本识别与中文拼接（不能出现「立 即 抢 购」）")
    image, font_path, boxes = make_image([
        (BUY_NOW, BUY_POS),
        (START_GAME, (40, 100)),
        (CONFIRM, (40, 170)),
    ])
    emit(f"  测试图: {image.shape[1]}x{image.shape[0]}  字体: {font_path}")
    emit(f"  绘制位置: {BUY_NOW!r} @ {BUY_POS}  框={boxes[0]}")

    t0 = time.perf_counter()
    result = engine.recognize(image)
    first_ms = (time.perf_counter() - t0) * 1000.0

    emit(f"  识别原文: {result.text!r}")
    emit(f"  识别行数: {len(result.lines)}   engine={result.engine!r}")
    for line in result.lines:
        emit(f"    line text={line.text!r} x={line.x} y={line.y} w={line.w} h={line.h} conf={line.confidence}")

    flat = normalize(result.text)
    check("text 去掉空白后包含「立即抢购」", BUY_NOW in flat, f"flat={flat!r}")
    check("「立即抢购」没有被拆成带空格的形式", "立 即" not in result.text)
    check("text 多行用 \\n 连接", len(result.lines) <= 1 or "\n" in result.text)

    line, exact = find_line_containing(result, BUY_NOW)
    if check("识别结果里能定位到「立即抢购」这一行", line is not None):
        assert line is not None
        left, top, width, height = boxes[0]
        dx = abs(line.x - left)
        dy = abs(line.y - top)
        emit(f"    真实绘制框 x={left} y={top} w={width} h={height}")
        emit(f"    识别出框     x={line.x} y={line.y} w={line.w} h={line.h}  误差 dx={dx} dy={dy}")
        check("命中行完全等于关键词（无多余空格）", exact)
        check("框在图片范围内",
              0 <= line.x < image.shape[1] and 0 <= line.y < image.shape[0]
              and line.w > 0 and line.h > 0)
        check("x 坐标误差 <= 40px", dx <= 40, f"dx={dx}")
        check("y 坐标误差 <= 40px", dy <= 40, f"dy={dy}")

    hits = find_text(result, BUY_NOW)
    check("find_text(result, '立即抢购') 非空", len(hits) >= 1, f"hits={[h.text for h in hits]}")
    if hits:
        check("find_text 返回的框和真实绘制位置误差 <= 40px",
              abs(hits[0].x - boxes[0][0]) <= 40 and abs(hits[0].y - boxes[0][1]) <= 40,
              f"({hits[0].x},{hits[0].y}) vs ({boxes[0][0]},{boxes[0][1]})")

    check("find_text 忽略空格: '立 即 抢 购' 也能命中",
          len(find_text(result, "\u7acb \u5373 \u62a2 \u8d2d")) >= 1)
    check("find_text 找不到不存在的关键词时返回 []",
          find_text(result, NOT_PRESENT) == [])

    both = find_keywords(result, [BUY_NOW, START_GAME], mode="any")
    emit(f"  find_keywords(any) -> {[(k, l.text) for k, l in both]}")
    check("find_keywords(mode='any') 命中 2 个关键词", len(both) == 2, f"len={len(both)}")
    check("find_keywords(any) 返回的关键词正是这两个",
          sorted(k for k, _ in both) == sorted([BUY_NOW, START_GAME]))

    all_hit = find_keywords(result, [BUY_NOW, START_GAME], mode="all")
    check("find_keywords(mode='all') 全部命中时返回 2 条", len(all_hit) == 2, f"len={len(all_hit)}")
    all_miss = find_keywords(result, [BUY_NOW, NOT_PRESENT], mode="all")
    check("find_keywords(mode='all') 有缺失时返回 []", all_miss == [], f"got={all_miss}")
    check("find_keywords 空关键词列表返回 []", find_keywords(result, [], mode="any") == [])
    check("find_text 空关键词返回 []", find_text(result, "") == [])

    return first_ms, result


# ---------------------------------------------------------------------------
# 3. 进程复用 / 耗时
# ---------------------------------------------------------------------------
def test_reuse_and_timing(engine: WindowsOcrEngine, first_ms: float):
    section("3. 常驻进程复用（第二次必须明显更快）")
    image, _, _ = make_image([(BUY_NOW, BUY_POS), (START_GAME, (40, 100)), (CONFIRM, (40, 170))])
    t0 = time.perf_counter()
    second = engine.recognize(image)
    second_ms = (time.perf_counter() - t0) * 1000.0
    emit(f"  第一次(含启动 PS + 建引擎): {first_ms:.1f} ms")
    emit(f"  第二次(复用进程):          {second_ms:.1f} ms")
    emit(f"  加速比: {first_ms / max(second_ms, 0.001):.1f}x")
    check("第二次调用明显更快（< 第一次的 50%）", second_ms < first_ms * 0.5,
          f"{second_ms:.1f} < {first_ms * 0.5:.1f}")
    check("复用后单次 < 250ms（GUI 轮询可接受）", second_ms < 250, f"{second_ms:.1f} ms")
    check("第二次仍然识别正确", BUY_NOW in normalize(second.text), repr(second.text))

    # 连续多次，统计典型耗时
    timings = []
    for _ in range(5):
        t0 = time.perf_counter()
        engine.recognize(image)
        timings.append((time.perf_counter() - t0) * 1000.0)
    timings.sort()
    emit(f"  单次识别耗时样本(ms): {[round(t, 1) for t in timings]}")
    emit(f"  中位数 {timings[len(timings) // 2]:.1f} ms   最小 {timings[0]:.1f} ms   最大 {timings[-1]:.1f} ms")
    return second_ms, timings


# ---------------------------------------------------------------------------
# 4. close() 后自动重建
# ---------------------------------------------------------------------------
def test_close_and_rebuild(engine: WindowsOcrEngine):
    section("4. close() 之后再次 recognize() 能自动重建进程")
    engine.close()
    engine.close()  # 重复 close 必须安全
    check("重复 close() 不抛异常", True)

    image, _, _ = make_image([(BUY_NOW, BUY_POS)])
    t0 = time.perf_counter()
    result = engine.recognize(image)
    rebuild_ms = (time.perf_counter() - t0) * 1000.0
    emit(f"  close() 后首次识别: {rebuild_ms:.1f} ms")
    emit(f"  识别原文: {result.text!r}")
    check("close() 后 recognize() 自动重建并识别成功",
          BUY_NOW in normalize(result.text), repr(result.text))
    check("重建后 available 恢复为 True", engine.available is True)
    return rebuild_ms


# ---------------------------------------------------------------------------
# 5. 小图放大
# ---------------------------------------------------------------------------
def test_small_image(engine: WindowsOcrEngine):
    section("5. 小图（200x80）自动放大 + 坐标换算")
    size = (200, 80)
    image, _, boxes = make_image([(BUY_NOW, (10, 8))], size=size, font_size=40)
    emit(f"  小图尺寸: {image.shape[1]}x{image.shape[0]}  绘制框={boxes[0]}")

    upscaled, scale = ocr_mod.upscale_for_ocr(image)
    emit(f"  upscale_for_ocr -> {upscaled.shape[1]}x{upscaled.shape[0]}  scale={scale}")
    check("宽或高小于 600 时会被放大", scale > 1.0, f"scale={scale}")
    check("放大倍数是整数", float(scale).is_integer(), f"scale={scale}")
    check("放大后尺寸 = 原尺寸 x 倍数",
          upscaled.shape[1] == size[0] * int(scale) and upscaled.shape[0] == size[1] * int(scale),
          f"{upscaled.shape[1]}x{upscaled.shape[0]}")
    check("放大循环确实有推进（最短边不小于原值）",
          min(upscaled.shape[0], upscaled.shape[1]) >= min(size[1], size[0]),
          f"{upscaled.shape[1]}x{upscaled.shape[0]}")

    # 规范里那句「例如放大到最短边 >= 800」：用足够小的图验证能真正达到 800
    tiny_img = make_image([(BUY_NOW, (10, 4))], size=(140, 50), font_size=28)[0]
    tiny_up, tiny_scale = ocr_mod.upscale_for_ocr(tiny_img)
    emit(f"  规格校验: 140x50 -> {tiny_up.shape[1]}x{tiny_up.shape[0]}  scale={tiny_scale}")
    check("140x50 放大后最短边 >= 800（符合规格）",
          min(tiny_up.shape[0], tiny_up.shape[1]) >= 800,
          f"{tiny_up.shape[1]}x{tiny_up.shape[0]} scale={tiny_scale}")

    # 极端小图仍然受 MAX_UPSCALE 保护，不会变成巨图
    tiny10, scale10 = ocr_mod.upscale_for_ocr(np.zeros((10, 10, 3), dtype=np.uint8))
    check("10x10 放大倍数受 MAX_UPSCALE 限制",
          tiny10.shape[0] == 10 * ocr_mod.MAX_UPSCALE and scale10 == float(ocr_mod.MAX_UPSCALE),
          f"{tiny10.shape[1]}x{tiny10.shape[0]} scale={scale10}")
    check("放大倍数受 MAX_UPSCALE 限制", scale <= ocr_mod.MAX_UPSCALE, f"scale={scale}")

    t0 = time.perf_counter()
    result = engine.recognize(image)
    small_ms = (time.perf_counter() - t0) * 1000.0
    emit(f"  耗时: {small_ms:.1f} ms")
    emit(f"  识别原文: {result.text!r}")
    for line in result.lines:
        emit(f"    line text={line.text!r} x={line.x} y={line.y} w={line.w} h={line.h}")

    check("200x80 小图也能识别出文字", bool(normalize(result.text)), repr(result.text))
    check("小图识别结果包含「立即抢购」", BUY_NOW in normalize(result.text), repr(result.text))

    line, _ = find_line_containing(result, BUY_NOW)
    if check("小图能定位到「立即抢购」行", line is not None):
        assert line is not None
        emit(f"    换算回原图坐标 x={line.x} y={line.y} w={line.w} h={line.h}"
             f"  (真实绘制 {boxes[0]})")
        # 关键：如果忘了按缩放比换算，坐标会变成 4 倍，直接超出原图
        check("小图坐标是原图坐标系（没有被放大倍数污染）",
              0 <= line.x < size[0] and 0 <= line.y < size[1] and line.w <= size[0],
              f"x={line.x} y={line.y} w={line.w} img={size}")
        check("小图 x 误差 <= 40px", abs(line.x - boxes[0][0]) <= 40,
              f"dx={abs(line.x - boxes[0][0])}")
        check("小图 y 误差 <= 40px", abs(line.y - boxes[0][1]) <= 40,
              f"dy={abs(line.y - boxes[0][1])}")

    # 大图不应该被放大
    _, scale_big = ocr_mod.upscale_for_ocr(np.zeros((800, 1000, 3), dtype=np.uint8))
    check("大图（1000x800）不会被放大", scale_big == 1.0, f"scale={scale_big}")
    _, scale_700 = ocr_mod.upscale_for_ocr(np.zeros((700, 700, 3), dtype=np.uint8))
    check("700x700（大于 600）不会被放大", scale_700 == 1.0, f"scale={scale_700}")
    # 极端小图不能放大到离谱
    _, scale_tiny = ocr_mod.upscale_for_ocr(np.zeros((10, 10, 3), dtype=np.uint8))
    check("10x10 极端小图放大倍数受上限保护", scale_tiny <= ocr_mod.MAX_UPSCALE, f"scale={scale_tiny}")


# ---------------------------------------------------------------------------
# 6. 健壮性
# ---------------------------------------------------------------------------
def test_robustness():
    section("6. 健壮性（坏输入 / 超时 / 异常路径都不许抛）")

    engine = WindowsOcrEngine()
    check("空数组返回空结果且不抛", isinstance(engine.recognize(np.zeros((0, 0, 3), dtype=np.uint8)), OcrResult))
    check("None 返回空结果且不抛", isinstance(engine.recognize(None), OcrResult))
    check("纯白图返回空 OcrResult（不抛异常）",
          isinstance(engine.recognize(np.full((700, 700, 3), 255, dtype=np.uint8)), OcrResult))
    check("uint16 图会被转换处理而不抛",
          isinstance(engine.recognize(np.full((700, 700, 3), 60000, dtype=np.uint16)), OcrResult))
    check("灰度图 (2D) 不抛", isinstance(engine.recognize(np.full((700, 700), 128, dtype=np.uint8)), OcrResult))
    check("RGBA 图不抛", isinstance(engine.recognize(np.zeros((700, 700, 4), dtype=np.uint8)), OcrResult))
    check("PIL Image 输入不抛",
          isinstance(engine.recognize(Image.new("RGB", (700, 700), (255, 255, 255))), OcrResult))
    check("完全非法对象不抛", isinstance(engine.recognize(object()), OcrResult))
    engine.close()

    # 超时必须被杀掉并返回空结果，且 available 变 False
    timing_engine = WindowsOcrEngine(timeout=0.001)
    image, _, _ = make_image([(BUY_NOW, BUY_POS)])
    t0 = time.perf_counter()
    timed_out = timing_engine.recognize(image)
    elapsed = (time.perf_counter() - t0) * 1000.0
    emit(f"  超时实验（timeout=1ms）: 返回 {timed_out!r} 用时 {elapsed:.0f} ms  last_error={timing_engine.last_error!r}")
    check("超时返回空 OcrResult", timed_out.text == "" and timed_out.lines == [])
    check("超时后 available 变 False", timing_engine.available is False)
    check("超时不会卡满 15 秒（进程已被杀）", elapsed < 12000, f"{elapsed:.0f} ms")

    # 超时杀进程之后必须能自愈
    timing_engine.timeout = 15.0
    recovered = timing_engine.recognize(image)
    emit(f"  超时后恢复识别: {recovered.text!r}")
    check("超时杀进程后下一次调用自动重建并成功", BUY_NOW in normalize(recovered.text),
          repr(recovered.text))
    check("恢复后 available 回到 True", timing_engine.available is True)
    timing_engine.close()

    # 桥脚本不存在（模拟探测失败）也不能抛
    original = ocr_mod.BRIDGE_SCRIPT
    try:
        ocr_mod.BRIDGE_SCRIPT = Path(r"C:\definitely\not\here\ocr_bridge.ps1")
        broken = WindowsOcrEngine()
        check("桥脚本缺失时 available=False（探测失败不抛）", broken.available is False,
              f"last_error={broken.last_error!r}")
        check("桥脚本缺失时 recognize() 返回空结果不抛",
              broken.recognize(np.zeros((700, 700, 3), dtype=np.uint8)).lines == [])
        broken.close()
    finally:
        ocr_mod.BRIDGE_SCRIPT = original

    # 不可用的引擎（tesseract/rapidocr）调用 recognize 必须安全
    for cls, label in ((TesseractOcrEngine, "TesseractOcrEngine"), (RapidOcrEngine, "RapidOcrEngine")):
        try:
            inst = cls()
            out = inst.recognize(np.zeros((700, 700, 3), dtype=np.uint8))
            check(f"{label}（未安装）recognize() 返回空结果不抛", out.lines == [])
            inst.close()
        except Exception as exc:
            check(f"{label}（未安装）recognize() 返回空结果不抛", False, f"raised {exc!r}")

    # 桥进程被外部杀掉后要能恢复
    victim = WindowsOcrEngine()
    victim._ensure_process()
    proc = victim._proc
    if proc is not None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        time.sleep(0.2)
        after_kill = victim.recognize(image)
        emit(f"  外部杀进程后识别: {after_kill.text!r}")
        check("桥进程被外部杀掉后能自动重建并识别", BUY_NOW in normalize(after_kill.text),
              repr(after_kill.text))
    victim.close()


# ---------------------------------------------------------------------------
# 7. 并发调用（GUI 线程 + 识别线程）
# ---------------------------------------------------------------------------
def test_concurrent():
    section("7. 并发调用（多线程共用一个引擎）")
    engine = WindowsOcrEngine()
    image, _, _ = make_image([(BUY_NOW, BUY_POS)])
    engine.recognize(image)  # 预热

    results: List[str] = []
    errors: List[str] = []

    def worker():
        try:
            out = engine.recognize(image)
            results.append(normalize(out.text))
        except Exception as exc:  # pragma: no cover
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    t0 = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    elapsed = (time.perf_counter() - t0) * 1000.0
    emit(f"  4 线程并发完成，用时 {elapsed:.0f} ms，结果: {results}")
    check("4 线程并发没有任何异常", not errors, str(errors))
    check("4 线程并发都识别正确", len(results) == 4 and all(BUY_NOW in r for r in results), str(results))
    engine.close()


# ---------------------------------------------------------------------------
# 纯函数单测（不依赖系统 OCR）
# ---------------------------------------------------------------------------
def test_pure_functions():
    section("8. 纯函数：join_words / find_text / find_keywords 边界")
    cases = [
        ([BUY_NOW[0], BUY_NOW[1], BUY_NOW[2], BUY_NOW[3]], BUY_NOW),
        (["Add", "to", "cart"], "Add to cart"),
        (["\u786e", "\u5b9a"], CONFIRM),
        (["Price", ":", "99"], "Price : 99"),
        (["abc"], "abc"),
        ([], ""),
        (["", "a", ""], "a"),
        (["\u4ef7\u683c", "99", "\u5143"], "\u4ef7\u683c 99 \u5143"),
    ]
    for words, expected in cases:
        got = ocr_mod.join_words(words)
        check(f"join_words({words!r}) == {expected!r}", got == expected, f"got={got!r}")

    sample = OcrResult(
        text=f"{BUY_NOW}\n{START_GAME} {CONFIRM}",
        lines=[
            OcrLine(BUY_NOW, 10, 10, 100, 30),
            OcrLine(f"{START_GAME} {CONFIRM}", 10, 60, 200, 30),
        ],
        engine="fake",
    )
    check("find_text 大小写不敏感", len(find_text(OcrResult(text="HELLO", lines=[OcrLine("HELLO", 0, 0, 1, 1)]), "hello")) == 1)
    check("find_text ignore_case=False 区分大小写",
          find_text(OcrResult(text="HELLO", lines=[OcrLine("HELLO", 0, 0, 1, 1)]), "hello", ignore_case=False) == [])
    check("find_keywords(any) 命中 2 条", len(find_keywords(sample, [BUY_NOW, START_GAME], mode="any")) == 2)
    check("find_keywords(all) 全中才返回", len(find_keywords(sample, [BUY_NOW, CONFIRM], mode="all")) == 2)
    check("find_keywords(all) 缺一返回 []", find_keywords(sample, [BUY_NOW, NOT_PRESENT], mode="all") == [])
    check("OcrLine.confidence 默认 1.0（Windows OCR 不给置信度）", OcrLine("x", 0, 0, 1, 1).confidence == 1.0)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    emit(f"Python: {sys.executable}")
    emit(f"numpy: {np.__version__}   Pillow: {Image.__version__}")
    emit(f"OCR 模块: {ocr_mod.__file__}")
    emit(f"桥脚本:   {ocr_mod.BRIDGE_SCRIPT}  (exists={ocr_mod.BRIDGE_SCRIPT.is_file()})")
    emit(f"真实中文结果也写入: {DUMP_PATH}")

    engine = None
    try:
        engine = test_engine_probe()
        if not engine.available:
            check("Windows OCR 必须可用", False, f"last_error={engine.last_error!r}")
            return 1
        first_ms, result = test_recognize(engine)
        test_reuse_and_timing(engine, first_ms)
        test_close_and_rebuild(engine)
        test_small_image(engine)
        engine.close()
        test_robustness()
        test_concurrent()
        test_pure_functions()
    except Exception:
        _FAILED.append("未捕获异常")
        emit("")
        emit(traceback.format_exc())
    finally:
        if engine is not None:
            try:
                engine.close()
            except Exception:
                pass

    section("汇总")
    emit(f"  通过: {_PASSED}    失败: {len(_FAILED)}")
    if _FAILED:
        for name in _FAILED:
            emit(f"    - {name}")
    emit(f"  详细输出（含真实中文）: {DUMP_PATH}")
    flush_dump()
    return 0 if not _FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
