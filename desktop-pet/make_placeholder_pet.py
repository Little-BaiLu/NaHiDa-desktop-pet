#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成桌宠素材。

这不是纳西妲的官方美术，只是"能看"的占位角色：白发 + 绿色精灵耳 + 白绿衣裙，
配色向纳西妲靠，但有明显差异，避免与官方立绘混淆。

画法要点：
  * 扁平插画风：大色块 + 柔和阴影，不用硬描边（硬描边会变成"铁丝网"）
  * 按 SS 倍分辨率绘制后缩小 -> 边缘抗锯齿干净
  * 身体与眼睛分两层：眼睛层由程序每帧平移，实现目光跟随
  * 身体上眼睛位置画成"眼窝"（皮肤色 + 上眼睑弧），眼睛层移动时不会露底

运行:
    python make_placeholder_pet.py [输出目录]
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFilter

SS = 3                      # 超采样倍数
W, H = 320, 340             # 逻辑尺寸（pet.json 使用的坐标系）
CW, CH = W * SS, H * SS

# ---------------- 配色 ----------------
HAIR_HI = (250, 247, 235, 255)
HAIR = (234, 226, 206, 255)
HAIR_MID = (210, 199, 175, 255)
HAIR_SH = (183, 172, 148, 255)
HAIR_LINE = (163, 152, 130, 255)

SKIN = (255, 233, 219, 255)
SKIN_SH = (243, 199, 183, 255)
BLUSH = (250, 176, 178, 110)

GREEN_PALE = (186, 226, 186, 255)
GREEN = (124, 194, 138, 255)
GREEN_DEEP = (72, 140, 96, 255)
GREEN_DARK = (50, 104, 72, 255)

CLOTH = (252, 251, 247, 255)
CLOTH_SH = (224, 224, 214, 255)
LINEN = (250, 246, 236, 255)

# 眼睛在逻辑坐标系里的框（pet.json 的 eye_layout 由此推导）
EYE_W, EYE_H = 34, 36
EYE_CENTERS = [(120.0, 172.0), (200.0, 172.0)]
EYE_LAYOUT = [(int(cx - EYE_W / 2), int(cy - EYE_H / 2)) for cx, cy in EYE_CENTERS]


def S(v: float) -> float:
    return v * SS


def box(x0, y0, x1, y1):
    return [S(x0), S(y0), S(x1), S(y1)]


def poly(draw, pts, fill):
    draw.polygon([(S(x), S(y)) for x, y in pts], fill=fill)


def blurred(size, draw_fn, blur):
    """在独立图层上作画并高斯模糊，返回 (图层, 模糊半径)。"""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    return layer.filter(ImageFilter.GaussianBlur(S(blur)))


def make_body() -> Image.Image:
    img = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = W / 2

    # ---------- 1. 落影（让角色不像贴纸） ----------
    sh = blurred((CW, CH), lambda sd: (
        sd.ellipse(box(cx - 102, 96, cx + 102, 318), fill=(122, 112, 98, 62)),
        sd.ellipse(box(cx - 86, 300, cx + 86, 338), fill=(122, 112, 98, 74)),
    ), blur=7)
    img.alpha_composite(sh, (int(S(3)), int(S(6))))

    # ---------- 2. 后发 ----------
    d.ellipse(box(cx - 106, 80, cx + 106, 300), fill=HAIR_MID)
    d.ellipse(box(cx - 120, 148, cx + 120, 332), fill=HAIR)

    # ---------- 3. 脖子 + 身体 ----------
    d.rectangle(box(cx - 19, 214, cx + 19, 252), fill=SKIN_SH)
    d.rectangle(box(cx - 16, 214, cx + 16, 250), fill=SKIN)
    poly(d, [(cx - 62, 242), (cx + 62, 242), (cx + 92, 336), (cx - 92, 336)], CLOTH)
    poly(d, [(cx - 62, 242), (cx + 62, 242), (cx + 70, 272), (cx - 70, 272)], GREEN)
    poly(d, [(cx - 92, 314), (cx + 92, 314), (cx + 92, 336), (cx - 92, 336)], GREEN_DARK)
    poly(d, [(cx - 40, 242), (cx + 40, 242), (cx + 28, 266), (cx - 28, 266)], LINEN)
    d.line([(S(cx - 62), S(254)), (S(cx + 62), S(254))], fill=CLOTH_SH, width=int(S(1.3)))

    # ---------- 4. 耳朵 ----------
    for side in (-1, 1):
        tipx = cx + side * 128
        base_x = cx + side * 68
        poly(d, [(base_x, 152), (tipx, 92), (cx + side * 92, 168)], HAIR_MID)
        poly(d, [(base_x + side * 6, 154), (tipx - side * 12, 106), (cx + side * 90, 164)],
             GREEN_PALE)
        poly(d, [(base_x + side * 12, 156), (tipx - side * 26, 118), (cx + side * 88, 162)],
             GREEN)

    # ---------- 5. 脸（先画脸，再把头发剪影叠上去框住它） ----------
    d.ellipse(box(cx - 85, 84, cx + 85, 238), fill=SKIN)
    chin = blurred((CW, CH), lambda sd: sd.ellipse(
        box(cx - 70, 206, cx + 70, 244), fill=(226, 178, 168, 90)), blur=4)
    img.alpha_composite(chin)

    # ---------- 6. 头发（整头剪影 + 垂下的尖角刘海） ----------
    hair_pts = [
        (cx - 100, 158),
        (cx - 104, 96),
        (cx - 78, 56),
        (cx, 42),
        (cx + 78, 56),
        (cx + 104, 96),
        (cx + 100, 158),
        (cx + 76, 138),
        (cx + 40, 152),
        (cx + 14, 132),
        (cx - 14, 132),
        (cx - 40, 152),
        (cx - 76, 138),
    ]
    poly(d, hair_pts, HAIR)
    # 发丝
    for x0, y0, x1, y1 in [
        (cx - 86, 96, cx - 58, 130), (cx - 44, 116, cx - 30, 148),
        (cx + 44, 116, cx + 30, 148), (cx + 86, 96, cx + 58, 130),
        (cx - 2, 62, cx - 4, 100),
    ]:
        d.line([(S(x0), S(y0)), (S(x1), S(y1))], fill=HAIR_SH, width=int(S(1.5)))
    # 两侧鬓发垂到脸旁
    poly(d, [(cx - 100, 112), (cx - 104, 236), (cx - 86, 252), (cx - 74, 150)], HAIR)
    poly(d, [(cx + 100, 112), (cx + 104, 236), (cx + 86, 252), (cx + 74, 150)], HAIR)
    # 刘海在脸上的投影
    bshadow = blurred((CW, CH), lambda sd: sd.ellipse(
        box(cx - 78, 142, cx + 78, 172), fill=(204, 166, 152, 70)), blur=5)
    img.alpha_composite(bshadow)

    # ---------- 7. 发饰 ----------
    d.ellipse(box(cx + 30, 58, cx + 58, 86), fill=GREEN_DARK)
    d.ellipse(box(cx + 36, 64, cx + 52, 80), fill=GREEN_PALE)
    poly(d, [(cx + 44, 58), (cx + 30, 32), (cx + 56, 44)], GREEN)
    d.ellipse(box(cx - 76, 78, cx - 58, 96), fill=GREEN_DARK)
    d.ellipse(box(cx - 72, 82, cx - 62, 92), fill=GREEN_PALE)

    # ---------- 8. 眼窝（眼睛层移动的底） ----------
    for cxe, cye in EYE_CENTERS:
        x0, y0 = cxe - EYE_W / 2, cye - EYE_H / 2
        x1, y1 = cxe + EYE_W / 2, cye + EYE_H / 2
        d.ellipse(box(x0, y0, x1, y1), fill=SKIN_SH)
        d.ellipse(box(x0 + 1, y0 + 3, x1 - 1, y1 - 1), fill=SKIN)
        d.arc(box(x0 - 1, y0 - 1, x1 + 1, y1 + 1), start=200, end=340,
              fill=(198, 156, 146, 210), width=int(S(2.0)))

    # ---------- 9. 眉毛 ----------
    for cxe, cye in EYE_CENTERS:
        bx0 = cxe - EYE_W / 2 + 1
        d.arc(box(bx0, cye - EYE_H / 2 - 13, bx0 + EYE_W - 2, cye - EYE_H / 2 + 1),
              start=205, end=335, fill=(206, 186, 166, 235), width=int(S(1.8)))

    # ---------- 10. 腮红 + 嘴 ----------
    blush = blurred((CW, CH), lambda bd: (
        bd.ellipse(box(cx - 78, 190, cx - 46, 212), fill=BLUSH),
        bd.ellipse(box(cx + 46, 190, cx + 78, 212), fill=BLUSH),
    ), blur=4)
    img.alpha_composite(blush)
    d.arc(box(cx - 9, 196, cx + 9, 214), start=25, end=155,
          fill=(216, 142, 134, 255), width=int(S(1.7)))

    return img


def make_eye(mirror: bool = False) -> Image.Image:
    """瞳孔层：眼白 + 虹膜渐变 + 四叶草瞳孔 + 高光。"""
    size = (int(S(EYE_W)), int(S(EYE_H)))
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, h = size

    def e(x0, y0, x1, y1, fill):
        d.ellipse([x0 * w, y0 * h, x1 * w, y1 * h], fill=fill)

    e(0.00, 0.00, 1.00, 1.00, (250, 248, 245, 255))
    e(0.04, 0.05, 0.96, 0.97, (255, 255, 255, 255))
    e(0.11, 0.10, 0.89, 0.92, GREEN_DARK)
    e(0.16, 0.15, 0.84, 0.89, GREEN_DEEP)
    e(0.22, 0.21, 0.78, 0.86, GREEN)
    e(0.30, 0.29, 0.70, 0.81, (172, 224, 180, 255))
    e(0.42, 0.23, 0.58, 0.79, (36, 60, 46, 255))
    e(0.23, 0.43, 0.77, 0.57, (36, 60, 46, 255))
    e(0.37, 0.38, 0.63, 0.62, (24, 42, 32, 255))
    e(0.25, 0.17, 0.47, 0.39, (255, 255, 255, 250))
    e(0.62, 0.31, 0.74, 0.43, (255, 255, 255, 185))
    e(0.31, 0.66, 0.43, 0.78, (255, 255, 255, 115))
    d.arc([0, 0, w - 1, h - 1], start=0, end=360,
          fill=(212, 200, 190, 210), width=max(1, int(S(0.8))))

    if mirror:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    return img


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "assets", "namecard_placeholder")
    os.makedirs(out, exist_ok=True)

    make_body().resize((W, H), Image.LANCZOS).save(os.path.join(out, "body.png"))
    make_eye(False).resize((EYE_W, EYE_H), Image.LANCZOS).save(
        os.path.join(out, "eye_l.png"))
    make_eye(True).resize((EYE_W, EYE_H), Image.LANCZOS).save(
        os.path.join(out, "eye_r.png"))

    print("已生成素材 -> %s" % out)
    print("eye_layout = %r" % (EYE_LAYOUT,))


if __name__ == "__main__":
    main()
