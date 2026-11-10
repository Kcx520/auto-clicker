"""生成程序图标 assets/icon.ico（多尺寸）与 assets/icon.png。"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

ACCENT = (79, 140, 255, 255)
ACCENT_DARK = (46, 92, 190, 255)
WHITE = (255, 255, 255, 255)
ORANGE = (255, 176, 32, 255)


def draw_icon(size: int = 256) -> Image.Image:
    image = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = size * 4
    radius = int(s * 0.22)

    # 圆角底板（蓝）
    draw.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=ACCENT)
    # 底部加深，做出一点层次
    draw.rounded_rectangle([0, int(s * 0.62), s - 1, s - 1], radius=radius,
                           fill=ACCENT_DARK)
    draw.rounded_rectangle([0, int(s * 0.62), s - 1, int(s * 0.80)], radius=0,
                           fill=ACCENT_DARK)

    # 鼠标外形
    body_w, body_h = int(s * 0.34), int(s * 0.50)
    bx = int((s - body_w) / 2)
    by = int(s * 0.22)
    draw.rounded_rectangle([bx, by, bx + body_w, by + body_h],
                           radius=int(body_w * 0.48), fill=WHITE)
    # 鼠标中缝
    draw.line([(s // 2, by + int(body_h * 0.04)), (s // 2, by + int(body_h * 0.36))],
              fill=ACCENT, width=max(2, int(s * 0.022)))
    draw.line([(bx + int(body_w * 0.06), by + int(body_h * 0.40)),
               (bx + body_w - int(body_w * 0.06), by + int(body_h * 0.40))],
              fill=(210, 224, 255, 255), width=max(2, int(s * 0.02)))

    # 点击波纹（右上角）
    cx, cy = int(s * 0.74), int(s * 0.74)
    for index, r in enumerate((int(s * 0.10), int(s * 0.17), int(s * 0.24))):
        box = [cx - r, cy - r, cx + r, cy + r]
        draw.arc(box, start=200, end=340, fill=ORANGE, width=max(3, int(s * 0.026)))
    draw.ellipse([cx - int(s * 0.035), cy - int(s * 0.035),
                  cx + int(s * 0.035), cy + int(s * 0.035)], fill=ORANGE)

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    os.makedirs(ASSETS, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    base = draw_icon(256)
    ico_path = os.path.join(ASSETS, "icon.ico")
    base.save(ico_path, format="ICO", sizes=[(v, v) for v in sizes])
    png_path = os.path.join(ASSETS, "icon.png")
    base.save(png_path, format="PNG")
    print("已生成", ico_path, os.path.getsize(ico_path), "字节")
    print("已生成", png_path, os.path.getsize(png_path), "字节")
    for value in (16, 32, 48):
        draw_icon(value).save(os.path.join(ASSETS, f"icon_{value}.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
