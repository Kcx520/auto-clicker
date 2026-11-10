"""模板找图（纯 numpy + Pillow 实现，不依赖 OpenCV / scipy / skimage）。

公开接口
--------
``to_gray``       任意常见图像 -> float32 灰度图（0~255）
``find_template`` 在 image 中按 scales 多尺度搜索 template
``find_best``     find_template 的便捷版，只返回分数最高的一个
``load_image``    Pillow 读图 -> RGB uint8 ndarray
``save_image``    ndarray -> PNG
``crop``          安全裁剪（自动 clamp，宽高至少 1，永不抛异常）

算法
----
1. 归一化互相关（NCC，零均值）::

       score(u,v) = Σ (T-mean(T))·(I_uv-mean(I_uv)) / (std(T)·std(I_uv)·N)

   由于 ``Σ(T - mean(T)) == 0``，分子退化为 ``Σ T'·I_uv``，可以用 FFT 互相关
   一次算完整个得分图::

       C[p,q] = Σ_{i,j} I[(p+i) mod H, (q+j) mod W] · T'[i,j]

   FFT 尺寸直接取图像的 ``(H, W)``：当 ``p ≤ H-h`` 且 ``q ≤ W-w`` 时不会发生
   环绕，左上角 ``(H-h+1, W-w+1)`` 区域就是标准的 "valid" 线性互相关。这样既
   不用补零放大频谱，也省内存。

2. 局部均值 / 局部均方用积分图（cumsum）算，每个窗口 O(1)，与模板大小无关。
3. 多尺度共用同一张图像频谱（图像 FFT 只算一次），模板在每个尺度上单独补零成
   ``(H, W)`` 后做 ``rfft2``。
4. 只在「整理候选点 / 非极大值抑制」这类小规模地方用 Python 循环，匹配本身
   完全没有逐像素循环；峰值内存远低于 300MB。
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

__all__ = [
    "to_gray",
    "find_template",
    "find_best",
    "load_image",
    "save_image",
    "crop",
]

# 分数比较的容差（浮点误差）
_EPS = 1e-9
# 每个尺度最多保留多少个候选点（防止阈值过低时候选爆炸）
_MAX_CANDIDATES = 8192
# IoU 超过该值的候选视为同一个目标（非极大值抑制）
_NMS_IOU = 0.5
# 低于该标准差视为「完全恒定」，不参与打分
_MIN_STD = 1e-6


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def to_gray(img: np.ndarray) -> np.ndarray:
    """``HxWx3/4``（RGB 或 BGR 都当成三通道处理）或 ``HxW`` -> float32 灰度图。

    返回值范围为 0~255（不做归一化）。三通道按亮度加权（0.299/0.587/0.114），
    四通道忽略 alpha（只取前三通道）。
    """
    if not isinstance(img, np.ndarray):
        raise TypeError("img 必须是 numpy.ndarray")

    arr = img
    if arr.ndim == 2:
        out = arr
    elif arr.ndim == 3:
        channels = arr.shape[2]
        if channels == 1:
            out = arr[:, :, 0]
        elif channels == 3:
            r = arr[:, :, 0]
            g = arr[:, :, 1]
            b = arr[:, :, 2]
            out = 0.299 * r + 0.587 * g + 0.114 * b
        elif channels == 4:
            # 忽略 alpha，只按三通道处理
            out = to_gray(arr[:, :, :3])
        else:
            raise ValueError(f"不支持的通道数: {channels}")
    else:
        raise ValueError(f"不支持的数组维度: {arr.ndim}")

    out = np.asarray(out, dtype=np.float32)
    if out.dtype.kind == "f" and out.size and not np.isfinite(out).all():
        out = np.nan_to_num(out, copy=True, nan=0.0, posinf=255.0, neginf=0.0)
    return out


def crop(img: np.ndarray, region: Tuple[int, int, int, int]) -> np.ndarray:
    """按 ``(x, y, w, h)`` 裁剪，自动 clamp 到图像范围内。

    永远不抛异常，且返回结果宽高至少为 1（越界时返回边缘处的 1x1）。
    """
    try:
        arr = np.asarray(img)
    except Exception:
        return np.zeros((1, 1), dtype=np.uint8)

    if arr.ndim < 2 or arr.shape[0] < 1 or arr.shape[1] < 1:
        return np.zeros((1, 1), dtype=arr.dtype if arr.dtype.kind else np.uint8)

    height, width = int(arr.shape[0]), int(arr.shape[1])
    try:
        x, y, w, h = (int(round(float(v))) for v in region)
    except Exception:
        # 非法 region：退化成左上角 1x1
        x, y, w, h = 0, 0, 1, 1

    x0 = min(max(x, 0), width - 1)
    y0 = min(max(y, 0), height - 1)
    x1 = min(max(x + w, x0 + 1), width)
    y1 = min(max(y + h, y0 + 1), height)

    out = arr[y0:y1, x0:x1]
    if out.ndim >= 2 and out.shape[0] >= 1 and out.shape[1] >= 1:
        return out
    return np.zeros((1, 1), dtype=arr.dtype)


def load_image(path: str) -> np.ndarray:
    """用 Pillow 读图，返回 RGB uint8 ndarray（统一 3 通道）。"""
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        return np.array(rgb, dtype=np.uint8)


def save_image(path: str, img: np.ndarray) -> None:
    """用 Pillow 存成 PNG。uint8 原样保存，其它类型会截断到 0~255。"""
    arr = np.asarray(img)
    if arr.dtype != np.uint8:
        arr = np.clip(np.nan_to_num(arr.astype(np.float64)), 0, 255).astype(np.uint8)

    if arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]
    if arr.ndim not in (2, 3) or (arr.ndim == 3 and arr.shape[2] not in (3, 4)):
        raise ValueError(f"无法保存的图像形状: {arr.shape}")
    arr = np.ascontiguousarray(arr)  # Pillow 要求 C 连续，切片视图先拷一份
    pil = Image.fromarray(arr)  # uint8: 2D->L, 3 通道->RGB, 4 通道->RGBA

    directory = os.path.dirname(os.path.abspath(str(path)))
    if directory:
        os.makedirs(directory, exist_ok=True)
    pil.save(str(path), format="PNG")


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _normalize_scales(scales: Any) -> List[float]:
    """把 scales 整理成合法的正浮点列表；非法项直接丢弃。"""
    if scales is None:
        return [1.0]
    try:
        raw = list(scales)
    except TypeError:
        raw = [scales]

    out: List[float] = []
    for item in raw:
        try:
            value = float(item)
        except Exception:
            continue
        if not math.isfinite(value) or value <= 0.0:
            continue
        out.append(value)
    return out


def _resolve_region(
    region: Any, height: int, width: int
) -> Optional[Tuple[int, int, int, int]]:
    """把 ``(x, y, w, h)`` 解析成 clamp 后的 ``(x0, y0, x1, y1)``。

    - 非法输入（不是 4 个数）-> ``None``，调用方忽略 region
    - 合法但空 / 完全在图外 -> ``(0, 0, 0, 0)``，调用方返回空结果
    """
    try:
        seq = list(region)
    except TypeError:
        return None
    if len(seq) != 4:
        return None
    try:
        x, y, w, h = (int(round(float(v))) for v in seq)
    except Exception:
        return None

    if w <= 0 or h <= 0:
        return (0, 0, 0, 0)
    if x >= width or y >= height or x + w <= 0 or y + h <= 0:
        return (0, 0, 0, 0)  # 和图像没有任何交集

    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(width, x + w)
    y1 = min(height, y + h)
    if x1 <= x0 or y1 <= y0:
        return (0, 0, 0, 0)
    return (x0, y0, x1, y1)


def _resize_gray(gray: np.ndarray, width: int, height: int) -> np.ndarray:
    """把 float32 灰度模板缩放到 ``(width, height)``（Pillow 双线性，带失败兜底）。"""
    width = max(1, int(width))
    height = max(1, int(height))
    if gray.shape[0] == height and gray.shape[1] == width:
        return gray
    try:
        image = Image.fromarray(np.ascontiguousarray(gray, dtype=np.float32))
        image = image.resize((width, height), Image.BILINEAR)
        return np.asarray(image, dtype=np.float32)
    except Exception:
        return _resize_nearest(gray, width, height)


def _resize_nearest(gray: np.ndarray, width: int, height: int) -> np.ndarray:
    """最近邻缩放兜底（Pillow 不可用时才会走到）。"""
    src_h, src_w = gray.shape[:2]
    ys = np.minimum((np.arange(height) * src_h // max(height, 1)), src_h - 1)
    xs = np.minimum((np.arange(width) * src_w // max(width, 1)), src_w - 1)
    return np.ascontiguousarray(gray[ys][:, xs], dtype=np.float32)


def _local_max_mask(score: np.ndarray) -> np.ndarray:
    """3x3 邻域局部极大值掩码（候选点太多时才用）。"""
    padded = np.pad(score, 1, mode="constant", constant_values=-np.inf)
    mask = score >= padded[:-2, 1:-1]
    mask &= score >= padded[2:, 1:-1]
    mask &= score >= padded[1:-1, :-2]
    mask &= score >= padded[1:-1, 2:]
    mask &= score >= padded[:-2, :-2]
    mask &= score >= padded[2:, 2:]
    mask &= score >= padded[:-2, 2:]
    mask &= score >= padded[2:, :-2]
    return mask


def _candidate_indices(score: np.ndarray, threshold: float, cap: int) -> np.ndarray:
    """取 ``score >= threshold`` 的扁平下标，最多 cap 个（超了就只留高分点）。"""
    flat = score.ravel()
    if flat.size == 0:
        return np.empty(0, dtype=np.int64)
    limit = np.float32(threshold - _EPS)
    count = int(np.count_nonzero(flat >= limit))
    if count == 0:
        return np.empty(0, dtype=np.int64)
    if count <= cap:
        return np.flatnonzero(flat >= limit)

    # 候选太多：先做局部极大值过滤，再按分数取前 cap 个
    idx = np.flatnonzero((flat >= limit) & _local_max_mask(score).ravel())
    if idx.size > cap:
        values = flat[idx]
        picked = np.argpartition(values, -cap)[-cap:]
        idx = idx[picked]
    return idx


def _intersection_over_union(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """两个框的 IoU。"""
    ax0, ay0 = a["x"], a["y"]
    ax1, ay1 = ax0 + a["w"], ay0 + a["h"]
    bx0, by0 = b["x"], b["y"]
    bx1, by1 = bx0 + b["w"], by0 + b["h"]

    iw = min(ax1, bx1) - max(ax0, bx0)
    ih = min(ay1, by1) - max(ay0, by0)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = float(iw * ih)
    union = float(a["w"] * a["h"] + b["w"] * b["h"]) - inter
    if union <= 0:
        return 0.0
    return inter / union


def _suppress(results: List[Dict[str, Any]], max_results: int,
              iou_threshold: float = _NMS_IOU) -> List[Dict[str, Any]]:
    """按分数从高到低做非极大值抑制（重叠超过阈值的只留分最高的那个）。"""
    results.sort(key=lambda item: -item["score"])
    kept: List[Dict[str, Any]] = []
    for item in results:
        if len(kept) >= max_results:
            break
        if all(_intersection_over_union(item, other) <= iou_threshold for other in kept):
            kept.append(item)
    return kept


# --------------------------------------------------------------------------- #
# 主算法
# --------------------------------------------------------------------------- #
def _find_template_impl(
    image: np.ndarray,
    template: np.ndarray,
    threshold: float,
    scales: Any,
    max_results: int,
    region: Optional[Sequence[int]],
) -> List[Dict[str, Any]]:
    if not isinstance(image, np.ndarray) or not isinstance(template, np.ndarray):
        return []
    if image.ndim not in (2, 3) or template.ndim not in (2, 3):
        return []
    if image.size == 0 or template.size == 0:
        return []

    threshold = float(threshold)
    max_results = int(max_results)
    if max_results <= 0:
        return []

    scale_list = _normalize_scales(scales)
    if not scale_list:
        return []

    img = to_gray(image)
    tpl = to_gray(template)
    if img.ndim != 2 or tpl.ndim != 2 or img.size == 0 or tpl.size == 0:
        return []

    height, width = int(img.shape[0]), int(img.shape[1])
    tpl_h, tpl_w = int(tpl.shape[0]), int(tpl.shape[1])
    off_x = off_y = 0

    if region is not None:
        box = _resolve_region(region, height, width)
        if box == (0, 0, 0, 0):
            return []
        if box is not None:
            x0, y0, x1, y1 = box
            img = img[y0:y1, x0:x1]
            off_x, off_y = x0, y0
            height, width = int(img.shape[0]), int(img.shape[1])

    if tpl_h > height or tpl_w > width:
        return []

    img = np.ascontiguousarray(img, dtype=np.float32)

    # ---- 图像频谱：所有尺度共用 ----
    img64 = img.astype(np.float64)
    img_fft = np.fft.rfft2(img64)

    # ---- 积分图：局部和 / 局部平方和（float64 累积，避免大数相减丢精度）----
    cum = np.zeros((height + 1, width + 1), dtype=np.float64)
    cum[1:, 1:] = np.cumsum(np.cumsum(img64, axis=0), axis=1)
    square = img64 * img64
    cum2 = np.zeros((height + 1, width + 1), dtype=np.float64)
    cum2[1:, 1:] = np.cumsum(np.cumsum(square, axis=0), axis=1)
    del square, img64

    results: List[Dict[str, Any]] = []
    done_sizes = set()

    for scale in scale_list:
        tpl_w2 = max(1, int(round(tpl_w * scale)))
        tpl_h2 = max(1, int(round(tpl_h * scale)))
        if tpl_h2 > height or tpl_w2 > width:
            continue
        if (tpl_w2, tpl_h2) in done_sizes:
            continue
        done_sizes.add((tpl_w2, tpl_h2))

        patch = _resize_gray(tpl, tpl_w2, tpl_h2)
        patch = np.ascontiguousarray(patch, dtype=np.float32)
        if patch.shape[0] != tpl_h2 or patch.shape[1] != tpl_w2:
            continue

        # 零均值模板 + 模板标准差
        zero = patch - np.float32(patch.mean())
        tpl_std = float(np.sqrt(np.mean(np.square(zero, dtype=np.float64))))
        if not math.isfinite(tpl_std) or tpl_std < _MIN_STD:
            continue  # 模板完全恒定，无法定位

        # ---- 分子：FFT 互相关 ----
        padded = np.zeros((height, width), dtype=np.float64)
        padded[:tpl_h2, :tpl_w2] = zero
        spec = np.fft.rfft2(padded)
        del padded
        np.conjugate(spec, out=spec)
        spec *= img_fft
        corr = np.fft.irfft2(spec, s=(height, width))
        del spec
        numerator = np.ascontiguousarray(
            corr[: height - tpl_h2 + 1, : width - tpl_w2 + 1], dtype=np.float32
        )
        del corr

        # ---- 分母：局部均值 / 局部标准差 ----
        local_sum = (
            cum[tpl_h2:, tpl_w2:] - cum[:-tpl_h2, tpl_w2:]
            - cum[tpl_h2:, :-tpl_w2] + cum[:-tpl_h2, :-tpl_w2]
        ).astype(np.float32)
        local_sq = (
            cum2[tpl_h2:, tpl_w2:] - cum2[:-tpl_h2, tpl_w2:]
            - cum2[tpl_h2:, :-tpl_w2] + cum2[:-tpl_h2, :-tpl_w2]
        ).astype(np.float32)

        window = np.float32(tpl_h2 * tpl_w2)
        local_sum /= window                 # 局部均值
        local_sq /= window                  # 局部均方
        local_sq -= local_sum * local_sum   # 局部方差
        np.maximum(local_sq, 0.0, out=local_sq)
        np.sqrt(local_sq, out=local_sq)     # 局部标准差
        valid = local_sq >= _MIN_STD        # 恒定区域不打分
        local_sq *= np.float32(window * tpl_std)
        np.maximum(local_sq, _MIN_STD, out=local_sq)

        score = numerator / local_sq
        np.clip(score, -1.0, 1.0, out=score)
        score[~valid] = 0.0
        del numerator, local_sum, local_sq, valid

        # ---- 收集候选点 ----
        map_w = width - tpl_w2 + 1
        indices = _candidate_indices(score, threshold, _MAX_CANDIDATES)
        for flat_index in indices:
            y, x = divmod(int(flat_index), map_w)
            value = float(score[y, x])
            if value < threshold - _EPS:
                continue
            results.append(
                {
                    "x": int(x) + off_x,
                    "y": int(y) + off_y,
                    "w": int(tpl_w2),
                    "h": int(tpl_h2),
                    "score": value,
                    "scale": float(scale),
                }
            )
        del score

    if not results:
        return []
    return _suppress(results, max_results)


def find_template(
    image: np.ndarray,
    template: np.ndarray,
    threshold: float = 0.85,
    scales: Tuple[float, ...] = (1.0,),
    max_results: int = 5,
    region: Optional[Tuple[int, int, int, int]] = None,
) -> List[Dict[str, Any]]:
    """在 image 中找 template（多尺度 + 非极大值抑制）。

    参数
    ----
    image / template : ``HxW`` 或 ``HxWx3/4`` 的 uint8 数组
    threshold        : 分数阈值，低于它的候选一律丢弃
    scales           : 模板缩放倍率，例如 ``(1.0, 0.9, 1.1)``
    max_results      : 最多返回多少个结果
    region           : ``(x, y, w, h)``，只在 image 的这个子区域里搜索（越界自动裁剪）

    返回
    ----
    ``[{"x", "y", "w", "h", "score", "scale"}, ...]``，按 score 从高到低；
    x/y 是 image 坐标系下匹配区域左上角，w/h 是该 scale 下模板的宽高。
    任何异常输入都返回 ``[]``，不抛异常。
    """
    try:
        return _find_template_impl(
            image, template, threshold, scales, max_results, region
        )
    except Exception:
        return []


def find_best(
    image: np.ndarray,
    template: np.ndarray,
    threshold: float = 0.85,
    scales: Tuple[float, ...] = (1.0,),
    region: Optional[Tuple[int, int, int, int]] = None,
) -> Optional[Dict[str, Any]]:
    """返回分数最高的一个结果，或 ``None``。"""
    found = find_template(
        image, template, threshold=threshold, scales=scales, max_results=1, region=region
    )
    return found[0] if found else None
