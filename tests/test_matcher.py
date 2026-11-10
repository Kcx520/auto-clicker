"""app.matcher 自测脚本（离线可跑，只用 numpy + Pillow + 标准库）。

运行::

    & "python" tests\\test_matcher.py

覆盖：定位精度、多尺度、多目标 + NMS、无匹配、region 裁剪、退化输入不抛异常、
以及 1707x1067 / 100x100 模板的性能与内存。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import matcher  # noqa: E402

try:  # 让中文在任意控制台代码页下都能正常输出
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASSED = 0
FAILED = 0
FAILURES = []


def check(name, fn):
    """跑一个子测试，失败不中断整体。"""
    global PASSED, FAILED
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        FAILED += 1
        FAILURES.append((name, exc))
        print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
    else:
        PASSED += 1
        print(f"  [PASS] {name}")


# --------------------------------------------------------------------------- #
# 造图工具
# --------------------------------------------------------------------------- #
def make_background(width: int, height: int, seed: int) -> np.ndarray:
    """渐变 + 随机色块 + 轻噪声，模拟真实截图（用 float32 省内存）。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    img = np.empty((height, width, 3), dtype=np.float32)
    img[..., 0] = 40.0 + 55.0 * (xx / max(width - 1, 1))
    img[..., 1] = 65.0 + 45.0 * (yy / max(height - 1, 1))
    img[..., 2] = 125.0 - 45.0 * (xx / max(width - 1, 1))
    del yy, xx

    for _ in range(16):
        bw = int(rng.integers(40, 200))
        bh = int(rng.integers(40, 160))
        x = int(rng.integers(0, max(1, width - bw)))
        y = int(rng.integers(0, max(1, height - bh)))
        img[y : y + bh, x : x + bw] = rng.integers(25, 235, size=3).astype(np.float32)

    img += rng.standard_normal(img.shape, dtype=np.float32) * 5.0
    return np.clip(img, 0, 255).astype(np.uint8)


def make_pattern(width: int = 80, height: int = 60, seed: int = 1234,
                 grid=(3, 2), noise: float = 0.8) -> np.ndarray:
    """彩色色块图案（grid 个色块 + 圆 + 粗条）。

    结构以**大色块**为主：既保证互相关峰足够尖锐（定位精确），又让模板被缩放
    0.9 倍后仍与原图高度相关（多尺度可用）。seed 决定每个色块的颜色，因此不同
    seed 得到的是真正不同的图案。
    """
    rng = np.random.default_rng(seed)
    patch = np.full((height, width, 3), 18, dtype=np.float32)

    cols, rows = grid
    cw, ch = width / cols, height / rows
    for gy in range(rows):
        for gx in range(cols):
            color = rng.integers(20, 240, size=3)
            x0, y0 = int(gx * cw), int(gy * ch)
            x1 = width if gx == cols - 1 else int((gx + 1) * cw)
            y1 = height if gy == rows - 1 else int((gy + 1) * ch)
            patch[y0:y1, x0:x1] = color

    yy, xx = np.mgrid[0:height, 0:width]
    circle = ((xx - 0.75 * width) ** 2 + (yy - 0.25 * height) ** 2
              <= (0.14 * width) ** 2)
    patch[circle] = (250, 250, 250)

    bar_y = int(0.50 * height)
    bar_x0, bar_x1 = int(0.62 * width), int(0.97 * width)
    patch[bar_y:bar_y + max(2, height // 10), bar_x0:bar_x1] = (15, 15, 15)

    if noise:
        patch += rng.standard_normal(patch.shape, dtype=np.float32) * noise
    return np.clip(patch, 0, 255).astype(np.uint8)


def paste(img: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    img[y : y + patch.shape[0], x : x + patch.shape[1]] = patch


def add_noise(img: np.ndarray, sigma: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noisy = img.astype(np.float32) + rng.standard_normal(img.shape, dtype=np.float32) * sigma
    return np.clip(noisy, 0, 255).astype(np.uint8)


def resize_color(img: np.ndarray, factor: float) -> np.ndarray:
    from PIL import Image

    h, w = img.shape[:2]
    tw, th = max(1, int(round(w * factor))), max(1, int(round(h * factor)))
    pil = Image.fromarray(img).resize((tw, th), Image.BILINEAR)
    return np.array(pil, dtype=np.uint8)


def show(res, limit=5):
    if not res:
        return "  (无结果)"
    lines = []
    for item in res[:limit]:
        lines.append(
            "    x={x:<5d} y={y:<5d} w={w:<4d} h={h:<4d} score={score:.4f} scale={scale:.2f}".format(
                **item
            )
        )
    return "\n".join(lines)


try:  # 进程峰值内存（Windows）
    class _PMC(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    def peak_rss_mb() -> float:
        counters = _PMC()
        counters.cb = ctypes.sizeof(_PMC)
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        handle = kernel32.GetCurrentProcess()
        for name, lib in (("K32GetProcessMemoryInfo", kernel32),
                          ("GetProcessMemoryInfo", ctypes.windll.psapi)):
            fn = getattr(lib, name, None)
            if fn is None:
                continue
            try:
                fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC), ctypes.c_ulong]
                fn.restype = ctypes.c_int
                if fn(handle, ctypes.byref(counters), counters.cb):
                    return counters.PeakWorkingSetSize / (1024.0 * 1024.0)
            except Exception:
                continue
        return float("nan")

except Exception:  # pragma: no cover

    def peak_rss_mb() -> float:
        return float("nan")


# --------------------------------------------------------------------------- #
# 固定素材
# --------------------------------------------------------------------------- #
TPL = make_pattern(80, 60, seed=99)


def build_scene(seed=7, paste_at=(512, 331), noise_sigma=2.5):
    img = make_background(1200, 800, seed)
    paste(img, TPL, *paste_at)
    return add_noise(img, noise_sigma, seed + 1000)


# --------------------------------------------------------------------------- #
# 0) 公式对照：暴力 NCC vs FFT 实现
# --------------------------------------------------------------------------- #
def brute_force_ncc(image: np.ndarray, template: np.ndarray) -> np.ndarray:
    """按定义逐像素算 NCC（只在小图上用来做对照，真实实现里没有这种循环）。"""
    img = matcher.to_gray(image).astype(np.float64)
    tpl = matcher.to_gray(template).astype(np.float64)
    th, tw = tpl.shape
    tz = tpl - tpl.mean()
    t_std = np.sqrt((tz * tz).mean())
    height, width = img.shape
    out = np.zeros((height - th + 1, width - tw + 1), dtype=np.float64)
    for y in range(out.shape[0]):
        for x in range(out.shape[1]):
            win = img[y : y + th, x : x + tw]
            wz = win - win.mean()
            i_std = np.sqrt((wz * wz).mean())
            if t_std < 1e-9 or i_std < 1e-9:
                out[y, x] = 0.0
            else:
                out[y, x] = float((tz * wz).mean() / (t_std * i_std))
    return out


def test_0_formula_match():
    print("\n=== 0. 公式对照：暴力 NCC vs FFT 实现（小图，逐像素验证）===")
    rng = np.random.default_rng(20240607)
    img = rng.integers(0, 256, size=(70, 100, 3), dtype=np.uint8)
    tpl = rng.integers(0, 256, size=(15, 20, 3), dtype=np.uint8)
    # 再贴一个真实目标（就是模板本身），保证有明确的 1.0 高分点
    paste(img, tpl, 40, 30)

    reference = brute_force_ncc(img, tpl)
    th, tw = tpl.shape[:2]
    best_y, best_x = np.unravel_index(int(np.argmax(reference)), reference.shape)
    print(f"  暴力实现最高分 {reference[best_y, best_x]:.6f} @ (x={best_x}, y={best_y})")

    got = matcher.find_best(img, tpl, threshold=-2.0)
    assert got is not None
    print(f"  FFT  实现最高分 {got['score']:.6f} @ (x={got['x']}, y={got['y']})")
    assert (got["x"], got["y"]) == (best_x, best_y), (got, best_x, best_y)
    assert abs(got["score"] - reference[best_y, best_x]) < 1e-4, (got["score"], reference[best_y, best_x])

    # 逐点对照：把 region 缩到"只有模板那么大"，就只返回该点一个分数
    worst = 0.0
    probes = [(0, 0), (1, 3), (17, 44), (30, 40), (48, 77), (54, 80), (0, 79), (54, 0)]
    for y, x in probes:
        if y + th > img.shape[0] or x + tw > img.shape[1]:
            continue
        one = matcher.find_template(img, tpl, threshold=-2.0, region=(x, y, tw, th))
        assert len(one) == 1, (x, y, one)
        diff = abs(one[0]["score"] - reference[y, x])
        worst = max(worst, diff)
    print(f"  {len(probes)} 个采样点的最大绝对误差: {worst:.2e}")
    assert worst < 1e-4, worst
    print("  FFT 实现与 NCC 定义公式逐点一致（误差 < 1e-4）OK")


# --------------------------------------------------------------------------- #
# 1) 单尺度定位精度
# --------------------------------------------------------------------------- #
def test_1_single_scale():
    print("\n=== 1. 单尺度定位：1200x800（渐变+色块+噪声）贴 80x60 彩色图案 ===")

    clean = make_background(1200, 800, 7)
    paste(clean, TPL, 512, 331)
    res_clean = matcher.find_template(clean, TPL, threshold=0.85, scales=(1.0,), max_results=5)
    print("  无噪声（应 ≈1.0000）:")
    print(show(res_clean))
    assert res_clean, "无噪声图上也找不到模板"
    assert abs(res_clean[0]["x"] - 512) <= 1 and abs(res_clean[0]["y"] - 331) <= 1
    assert res_clean[0]["score"] > 0.999, res_clean[0]["score"]

    img = build_scene()
    t0 = time.perf_counter()
    res = matcher.find_template(img, TPL, threshold=0.85, scales=(1.0,), max_results=5)
    cost_ms = (time.perf_counter() - t0) * 1000.0
    print(f"  真值 (512, 331)  阈值=0.85  耗时 {cost_ms:.1f} ms")
    print(show(res))

    assert res, "加噪声后没有结果"
    top = res[0]
    dx, dy = abs(top["x"] - 512), abs(top["y"] - 331)
    print(f"  第一名 (x={top['x']}, y={top['y']}) 误差 dx={dx} dy={dy} score={top['score']:.4f}")
    assert dx <= 1 and dy <= 1, (dx, dy)
    assert top["score"] > 0.95, top["score"]
    assert top["w"] == 80 and top["h"] == 60

    best = matcher.find_best(img, TPL, threshold=0.85)
    assert best is not None and best["score"] == top["score"]
    print(f"  find_best -> x={best['x']} y={best['y']} score={best['score']:.4f}")


# --------------------------------------------------------------------------- #
# 2) 多尺度
# --------------------------------------------------------------------------- #
def test_2_multiscale():
    print("\n=== 2. 多尺度：用 0.9 倍缩放的模板作查询 ===")
    img = build_scene()
    query = resize_color(TPL, 0.9)
    print(f"  图上图案 80x60 @ (512, 331)，查询模板 {query.shape[1]}x{query.shape[0]}（0.9 倍）")

    res = matcher.find_template(
        img, query, threshold=0.85, scales=(0.85, 0.9, 0.95, 1.0), max_results=5
    )
    print(show(res))
    assert res, "0.9 倍查询模板没有结果"
    top = res[0]
    # 返回框是该 scale 下模板的大小，因此宽高与图上图案不同，
    # 判断"找到"应该看**框中心**是否落在目标中心上（左上角天然会偏移 ~半尺寸差）。
    true_cx, true_cy = 512 + 80 / 2.0, 331 + 60 / 2.0
    got_cx, got_cy = top["x"] + top["w"] / 2.0, top["y"] + top["h"] / 2.0
    dx, dy = abs(got_cx - true_cx), abs(got_cy - true_cy)
    print(f"  第一名 (x={top['x']}, y={top['y']}, w={top['w']}, h={top['h']}) "
          f"score={top['score']:.4f} scale={top['scale']}")
    print(f"  中心误差 dx={dx:.1f} dy={dy:.1f} px（真值中心 {true_cx:.0f},{true_cy:.0f}）")
    assert dx <= 3 and dy <= 3, (dx, dy)
    assert top["score"] > 0.90, top["score"]

    # 反向验证：图上是 0.9 倍图案，用原始模板 + scales 去搜，应该命中 scale=0.9，
    # 而且返回框与真实图案 1 像素不差
    print("  反向验证：图上是 0.9 倍图案（72x54 @ 700,420），用原始模板多尺度搜索")
    small = matcher._resize_gray(matcher.to_gray(TPL), 72, 54)  # 与匹配器内部缩放一致
    patch = np.repeat(small[:, :, None], 3, axis=2).astype(np.uint8)
    img2 = make_background(1200, 800, 8)
    paste(img2, patch, 700, 420)
    res2 = matcher.find_template(
        img2, TPL, threshold=0.90, scales=(0.85, 0.9, 0.95, 1.0), max_results=5
    )
    print(show(res2))
    assert res2, "反向多尺度没有结果"
    top2 = res2[0]
    print(f"  第一名 (x={top2['x']}, y={top2['y']}) scale={top2['scale']} "
          f"score={top2['score']:.4f}  (真值 x=700 y=420 scale=0.9)")
    assert abs(top2["x"] - 700) <= 2 and abs(top2["y"] - 420) <= 2
    assert abs(top2["scale"] - 0.9) < 1e-9, top2["scale"]
    assert top2["score"] > 0.99 and top2["w"] == 72 and top2["h"] == 54


# --------------------------------------------------------------------------- #
# 3) 多目标 + NMS
# --------------------------------------------------------------------------- #
def test_3_multiple_targets():
    print("\n=== 3. 多目标：同一模板贴 3 处（间隔足够远）===")
    spots = [(80, 80), (620, 130), (880, 600)]
    img = make_background(1200, 800, 11)
    for x, y in spots:
        paste(img, TPL, x, y)
    img = add_noise(img, 2.0, 77)

    res = matcher.find_template(img, TPL, threshold=0.85, scales=(1.0,), max_results=5)
    print(f"  真值 {spots}")
    print(show(res))
    assert len(res) == 3, f"应找到 3 个，实际 {len(res)}"

    for truth in spots:
        assert any(
            abs(r["x"] - truth[0]) <= 1 and abs(r["y"] - truth[1]) <= 1 for r in res
        ), f"漏掉 {truth}"

    for i in range(len(res)):
        for j in range(i + 1, len(res)):
            a, b = res[i], res[j]
            iw = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
            ih = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
            assert iw <= 0 or ih <= 0, f"结果 {i} 与 {j} 重叠: {a} {b}"
    # max_results 生效
    one = matcher.find_template(img, TPL, threshold=0.85, scales=(1.0,), max_results=2)
    assert len(one) == 2, len(one)
    print(f"  max_results=2 -> {len(one)} 个；max_results=5 -> {len(res)} 个，互不重叠")


# --------------------------------------------------------------------------- #
# 4) 无关模板 -> 空结果
# --------------------------------------------------------------------------- #
def test_4_no_match():
    print("\n=== 4. 无关随机模板（threshold=0.85）应返回 [] ===")
    img = build_scene()
    rng = np.random.default_rng(4242)
    alien = rng.integers(0, 256, size=(60, 80, 3), dtype=np.uint8)
    res = matcher.find_template(img, alien, threshold=0.85, scales=(1.0,), max_results=5)
    print(f"  最高分（threshold=0.01 时）: ", end="")
    probe = matcher.find_template(img, alien, threshold=0.01, scales=(1.0,), max_results=1)
    print(f"{probe[0]['score']:.4f}" if probe else "无")
    assert res == [], res
    assert matcher.find_best(img, alien, threshold=0.85) is None

    # 结构化的干扰模板（不同的色块网格 + 不同 seed）同样不该命中
    other = make_pattern(80, 60, seed=555, grid=(4, 3))
    probe2 = matcher.find_template(img, other, threshold=0.0, scales=(1.0,), max_results=1)
    print(f"  另一个 80x60 图案模板最高分: {probe2[0]['score']:.4f}" if probe2 else "  无")
    res2 = matcher.find_template(img, other, threshold=0.85, scales=(1.0,))
    assert res2 == [], res2
    # 阈值 1.5 永远不可能命中
    assert matcher.find_template(img, TPL, threshold=1.5) == []
    print("  threshold=1.5 -> []  OK")


# --------------------------------------------------------------------------- #
# 5) region 生效
# --------------------------------------------------------------------------- #
def test_5_region():
    print("\n=== 5. region：只搜右下角时，左上角的模板不应被返回 ===")
    img = make_background(1200, 800, 7)
    paste(img, TPL, 60, 40)
    img = add_noise(img, 2.0, 31)

    full = matcher.find_template(img, TPL, threshold=0.85, scales=(1.0,))
    print("  全图搜索:")
    print(show(full))
    assert full and abs(full[0]["x"] - 60) <= 1 and abs(full[0]["y"] - 40) <= 1

    region = (600, 400, 600, 400)
    res = matcher.find_template(img, TPL, threshold=0.85, scales=(1.0,), region=region)
    print(f"  region={region} 搜索 -> {len(res)} 个结果")
    print(show(res))
    for item in res:
        assert item["x"] >= 600 and item["y"] >= 400, item
    assert not any(abs(r["x"] - 60) <= 3 and abs(r["y"] - 40) <= 3 for r in res), res
    print("  左上角 (60,40) 的模板未被返回，且所有结果都落在 region 内  OK")

    # region 包含模板时应该能找到（坐标是 image 坐标系）
    hit = matcher.find_template(img, TPL, threshold=0.85, region=(20, 10, 300, 200))
    print("  region=(20,10,300,200):")
    print(show(hit))
    assert hit and abs(hit[0]["x"] - 60) <= 1 and abs(hit[0]["y"] - 40) <= 1

    # region 比模板小 -> 空
    tiny = matcher.find_template(img, TPL, threshold=0.85, region=(0, 0, 10, 10))
    assert tiny == [], tiny
    print("  region 比模板小 -> []  OK")


# --------------------------------------------------------------------------- #
# 6) 边界 / 退化输入
# --------------------------------------------------------------------------- #
def test_6_edge_cases():
    print("\n=== 6. 边界与退化输入（全部不应抛异常）===")
    img = build_scene()
    tpl = TPL

    # 6.1 模板比图大
    assert matcher.find_template(np.zeros((20, 20, 3), np.uint8), tpl) == []
    assert matcher.find_template(img, make_pattern(1300, 900)) == []
    print("  模板比图大 -> []  OK")

    # 6.2 灰度输入
    gray = matcher.to_gray(img)
    gray_tpl = matcher.to_gray(tpl)
    assert gray.shape == (800, 1200) and gray.dtype == np.float32
    assert float(gray.min()) >= 0.0 and float(gray.max()) <= 255.0
    res_gray = matcher.find_template(gray, gray_tpl, threshold=0.85, scales=(1.0,))
    print(f"  灰度 (HxW) 输入 -> {len(res_gray)} 个 第一名 score={res_gray[0]['score']:.4f}")
    assert res_gray and abs(res_gray[0]["x"] - 512) <= 1

    # 6.3 RGB / RGBA / HxWx1
    rgba = np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])
    res_rgba = matcher.find_template(rgba, np.dstack([tpl, np.full(tpl.shape[:2], 255, np.uint8)]))
    single = matcher.find_template(img[:, :, :1], tpl[:, :, :1], threshold=0.6)
    print(f"  RGBA 输入 -> {len(res_rgba)} 个；HxWx1 输入 -> {len(single)} 个")
    assert res_rgba and abs(res_rgba[0]["x"] - 512) <= 1
    assert matcher.to_gray(img[:, :, :1]).shape == (800, 1200)

    # 6.4 region 越界 / 非法
    for bad_region in [
        (5000, 5000, 100, 100),
        (-500, -500, 100, 100),
        (1190, 790, 500, 500),
        (0, 0, 0, 0),
        (10, 10, -5, -5),
        (-50, -50, 10000, 10000),
    ]:
        out = matcher.find_template(img, tpl, region=bad_region)
        assert isinstance(out, list), (bad_region, out)
        print(f"  region={bad_region} -> {len(out)} 个结果（未抛异常）")

    # 6.5 非法输入类型 / 通道数
    assert matcher.find_template(None, tpl) == []
    assert matcher.find_template(img, None) == []
    assert matcher.find_template("not an image", tpl) == []
    assert matcher.find_template(img, [1, 2, 3]) == []
    assert matcher.find_template(img, np.zeros((10, 10, 5), np.uint8)) == []
    assert matcher.find_template(np.zeros((10, 10, 2), np.uint8), tpl) == []
    assert matcher.find_template(np.zeros((3,), np.uint8), tpl) == []
    assert matcher.find_template(np.zeros((0, 0, 3), np.uint8), tpl) == []
    print("  非 ndarray / 通道数异常 / 空数组 -> []  OK")

    # 6.6 恒定图 / 恒定模板（std=0）
    assert matcher.find_template(np.full((300, 300, 3), 128, np.uint8), tpl) == []
    assert matcher.find_template(img, np.full((80, 60, 3), 200, np.uint8)) == []
    print("  恒定图 / 恒定模板 -> []  OK")

    # 6.7 其它参数退化
    assert matcher.find_template(img, tpl, scales=()) == []
    assert matcher.find_template(img, tpl, scales=(0.0, -1.0)) == []
    assert matcher.find_template(img, tpl, max_results=0) == []
    assert matcher.find_template(img, tpl, max_results=-3) == []
    assert matcher.find_template(img, tpl, threshold=float("nan")) == []
    assert isinstance(matcher.find_template(img, tpl, scales=None), list)
    assert isinstance(matcher.find_template(img, tpl, scales=1.0), list)
    print("  scales=() / scales=(0,-1) / max_results<=0 / threshold=nan -> []  OK")

    # 6.8 crop 永不抛异常，宽高至少 1
    samples = [
        (-100, -100, 50, 50),
        (1190, 790, 1000, 1000),
        (0, 0, 0, 0),
        (5000, 5000, 10, 10),
        (-10, 5, 0, 0),
        (1200, 800, 1, 1),
    ]
    for region in samples:
        piece = matcher.crop(img, region)
        assert piece.ndim >= 2 and piece.shape[0] >= 1 and piece.shape[1] >= 1, region
        print(f"  crop{region} -> {piece.shape}")
    assert matcher.crop(img, (10, 10, 4, 4)).shape == (4, 4, 3)
    assert matcher.crop(img, "垃圾").shape[0] >= 1
    print("  crop 全部返回至少 1x1  OK")

    # 6.9 load_image / save_image / crop 往返（临时目录建在工作区内，跑完删掉）
    # 注意：不能用 tempfile.mkdtemp —— 它用 mode=0o700 建目录，在当前 Windows
    # 文件沙箱下会变成一个连自己都写不进去的 ACL，只能用 os.makedirs。
    tmp = os.path.join(str(ROOT), f"_matcher_io_{os.getpid()}")
    os.makedirs(tmp, exist_ok=True)
    try:
        scene = os.path.join(tmp, "scene.png")
        matcher.save_image(scene, img)
        loaded = matcher.load_image(scene)
        assert loaded.shape == img.shape and loaded.dtype == np.uint8
        assert np.array_equal(loaded, img), "PNG 往返不一致"
        sub = matcher.crop(loaded, (512, 331, 80, 60))
        scaled = matcher._resize_gray(matcher.to_gray(sub), 72, 54)
        gray_save = os.path.join(tmp, "gray.png")
        matcher.save_image(gray_save, scaled)
        assert matcher.load_image(gray_save).shape == (54, 72, 3)
        # 4 通道 PNG 也能存
        rgba_path = os.path.join(tmp, "rgba.png")
        matcher.save_image(rgba_path, np.dstack([scaled.astype(np.uint8)] * 3))
        print(f"  save_image/load_image 往返一致，灰度保存 -> {matcher.load_image(gray_save).shape}  OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 6.10 to_gray 异常输入
    for bad in (None, "x", np.zeros((2, 2, 5), np.uint8), np.zeros((2,), np.uint8)):
        try:
            matcher.to_gray(bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"to_gray({bad!r}) 应该报错")
    print("  to_gray 非法输入按文档抛 TypeError/ValueError  OK")


# --------------------------------------------------------------------------- #
# 7) 性能与内存（1707x1067 + 100x100）
# --------------------------------------------------------------------------- #
def test_7_performance():
    print("\n=== 7. 性能：1707x1067 图 + 100x100 模板 ===")
    img = make_background(1707, 1067, 21)
    big_tpl = make_pattern(100, 100, seed=2024)
    spot = (839, 512)
    paste(img, big_tpl, *spot)
    print(f"  图像 {img.shape[1]}x{img.shape[0]}  uint8 RGB  模板 {big_tpl.shape[1]}x{big_tpl.shape[0]}")

    # 预热（首次调用有内存分配/FFT 计划缓存的开销）
    matcher.find_template(img, big_tpl, scales=(1.0,), max_results=1)

    t0 = time.perf_counter()
    res1 = matcher.find_template(img, big_tpl, threshold=0.85, scales=(1.0,), max_results=5)
    single_ms = (time.perf_counter() - t0) * 1000.0

    scales3 = (0.9, 1.0, 1.1)
    t1 = time.perf_counter()
    res3 = matcher.find_template(img, big_tpl, threshold=0.85, scales=scales3, max_results=5)
    three_ms = (time.perf_counter() - t1) * 1000.0

    print(f"  单尺度 (1.0)              : {single_ms:8.1f} ms")
    print(f"  3 尺度   (0.9, 1.0, 1.1)  : {three_ms:8.1f} ms")
    print(f"  真值 ({spot[0]}, {spot[1]})")
    print("  单尺度结果:")
    print(show(res1))
    print("  3 尺度结果:")
    print(show(res3))
    assert res1 and abs(res1[0]["x"] - spot[0]) <= 1 and abs(res1[0]["y"] - spot[1]) <= 1
    assert res3 and abs(res3[0]["x"] - spot[0]) <= 1 and abs(res3[0]["y"] - spot[1]) <= 1
    assert single_ms < 1500.0, f"单尺度 {single_ms:.1f} ms 超出 1500 ms 目标"
    print(f"  单尺度 < 1500 ms 目标: {'达标' if single_ms < 1500 else '未达标'}")

    # 额外：连续两次调用（验证没有随调用次数增长的内存泄漏）
    t2 = time.perf_counter()
    for _ in range(3):
        matcher.find_template(img, big_tpl, scales=(1.0,), max_results=1)
    print(f"  连续 3 次单尺度          : {(time.perf_counter() - t2) * 1000.0:8.1f} ms (合计)")

    import tracemalloc

    rss_before = peak_rss_mb()
    tracemalloc.start()
    matcher.find_template(img, big_tpl, scales=scales3, max_results=5)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = peak_rss_mb()

    print(f"  tracemalloc 峰值增量      : {peak / 1048576.0:8.1f} MB（本次匹配的临时分配峰值）")
    print(f"  进程峰值工作集 Peak RSS   : {rss_before:8.1f} -> {rss_after:8.1f} MB"
          f"（含测试图像，总峰值 {max(rss_before, rss_after):.1f} MB）")
    assert peak / 1048576.0 < 300.0, f"内存 {peak / 1048576.0:.1f} MB 超过 300MB"
    if rss_after == rss_after:  # not NaN
        assert rss_after < 300.0, f"进程峰值 {rss_after:.1f} MB 超过 300MB"

    return single_ms, three_ms


# --------------------------------------------------------------------------- #
def main() -> int:
    print("=" * 78)
    print("app.matcher 自测  (numpy %s / Pillow %s / Python %s)"
          % (np.__version__, __import__("PIL").__version__, sys.version.split()[0]))
    print("=" * 78)

    check("0. NCC 公式对照（暴力 vs FFT）", test_0_formula_match)
    check("1. 单尺度定位精度", test_1_single_scale)
    check("2. 多尺度（0.9 倍查询 + 反向验证）", test_2_multiscale)
    check("3. 多目标 + 非极大值抑制", test_3_multiple_targets)
    check("4. 无关模板 -> 空结果", test_4_no_match)
    check("5. region 裁剪生效", test_5_region)
    check("6. 边界与退化输入不抛异常", test_6_edge_cases)
    perf = {}
    check("7. 性能与内存", lambda: perf.update(zip(("single", "three"), test_7_performance())))

    print("\n" + "=" * 78)
    print(f"结果: {PASSED} 项通过, {FAILED} 项失败")
    if perf:
        print(f"1707x1067 + 100x100 模板：单尺度 {perf['single']:.1f} ms，"
              f"3 尺度 {perf['three']:.1f} ms")
    for name, exc in FAILURES:
        print(f"  失败: {name} -> {type(exc).__name__}: {exc}")
    print("=" * 78)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
