#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拆层流水线的自检：用一张合成的"假立绘"跑 prepare_sprite.build_assets，
检查产出的图层尺寸、位置和内容是否合理。

    python test_prepare_sprite.py

不需要真实立绘，也不需要 GUI。
"""

from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prepare_sprite as PS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets", "_pipeline_test")


def make_fake_illustration() -> Image.Image:
    """画一张 900x1100 的"立绘"：白底 + 脑袋 + 两只绿眼睛 + 眼白。"""
    im = Image.new("RGBA", (900, 1100), (255, 255, 255, 255))
    d = ImageDraw.Draw(im)

    # 身体
    d.polygon([(300, 820), (600, 820), (700, 1100), (200, 1100)], fill=(240, 245, 238, 255))
    # 头发
    d.ellipse([220, 180, 680, 780], fill=(238, 231, 214, 255))
    # 脸
    d.ellipse([270, 260, 630, 700], fill=(255, 233, 219, 255))
    # 刘海
    d.polygon([(240, 380), (280, 240), (450, 190), (620, 240), (660, 380),
               (560, 300), (450, 360), (340, 300)], fill=(238, 231, 214, 255))

    # 眼睛（左 380,470  右 520,470）：眼白 + 虹膜 + 瞳孔
    for cx in (380, 520):
        d.ellipse([cx - 52, 418, cx + 52, 522], fill=(255, 255, 255, 255))
        d.ellipse([cx - 40, 426, cx + 40, 514], fill=(52, 108, 74, 255))
        d.ellipse([cx - 28, 436, cx + 28, 504], fill=(124, 194, 138, 255))
        d.ellipse([cx - 14, 450, cx + 14, 490], fill=(24, 42, 32, 255))
        d.ellipse([cx - 22, 440, cx - 4, 458], fill=(255, 255, 255, 255))
    # 眉毛
    for cx in (380, 520):
        d.arc([cx - 50, 380, cx + 50, 430], start=200, end=340, fill=(210, 190, 170, 255), width=5)
    return im


def main() -> int:
    src = make_fake_illustration()

    # 先验证抠背景：四角的白底应被抹掉，脑袋里的皮肤必须保留
    cut = PS.remove_background(src)
    cut_arr = np.array(cut)
    corner_clear = int(cut_arr[5, 5, 3]) == 0
    face_kept = int(cut_arr[500, 450, 3]) > 200
    print("抠背景: 四角透明=%s  脸部保留=%s" % (corner_clear, face_kept))

    # 模拟用户框选：整个头部区域
    crop = (200.0, 150.0, 700.0, 682.0)      # 500x532，比例≈320:340
    eyes = [(380.0, 470.0), (520.0, 470.0)]
    eye_size = 110.0

    body, layers, layout = PS.build_assets(cut, crop, eyes, eye_size)
    os.makedirs(OUT, exist_ok=True)
    body.save(os.path.join(OUT, "body.png"))
    layers["eye_l"].save(os.path.join(OUT, "eye_l.png"))
    layers["eye_r"].save(os.path.join(OUT, "eye_r.png"))
    PS.compose_preview(body, layers, layout).save(os.path.join(OUT, "preview.png"))

    print("body 尺寸:", body.size)
    print("eye_l 尺寸:", layers["eye_l"].size, " eye_r:", layers["eye_r"].size)
    print("layout:", layout)

    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("  PASS  " if cond else "  FAIL  ") + name)
        ok = ok and cond

    check("body 为 320x340", body.size == (320, 340))
    check("抠背景：四角透明", corner_clear)
    check("抠背景：脸部保留", face_kept)
    check("两个图层都非空", layers["eye_l"].width > 0 and layers["eye_r"].width > 0)
    check("左右眼睛框尺寸一致",
          layout[0]["w"] == layout[1]["w"] and layout[0]["h"] == layout[1]["h"])
    check("眼睛位置关于画面中心对称",
          abs((layout[0]["x"] + layout[0]["w"] / 2)
              - (320 - (layout[1]["x"] + layout[1]["w"] / 2))) <= 2)
    check("眼睛框落在画布内",
          all(0 <= e["x"] and 0 <= e["y"] and e["x"] + e["w"] <= 320
              and e["y"] + e["h"] <= 340 for e in layout))

    # 眼睛层应该以虹膜那种绿色为主，而不是皮肤色
    arr = np.array(layers["eye_l"].convert("RGBA"))
    opaque = arr[arr[:, :, 3] > 200][:, :3]
    check("眼睛层有内容", len(opaque) > 200)
    if len(opaque):
        mean = opaque.mean(axis=0)
        print("  眼睛层平均色:", mean.round(1))
        check("眼睛层偏绿（抓到了虹膜而不是皮肤）",
              mean[1] > mean[0] and mean[1] > mean[2])

    # 身体上原眼睛位置应该已经被填成接近肤色的颜色
    box = layout[0]
    patch = np.array(body.convert("RGBA"))[box["y"] + 4:box["y"] + box["h"] - 4,
                                            box["x"] + 4:box["x"] + box["w"] - 4][:, :, :3]
    mean_body = patch.reshape(-1, 3).mean(axis=0)
    print("  眼窝填充平均色:", mean_body.round(1))
    check("眼窝填充接近肤色（红色分量最高）", mean_body[0] >= mean_body[1] >= mean_body[2] - 6)

    print("\n结论:", "流水线自检通过" if ok else "有检查项失败")
    print("产物在:", OUT)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
