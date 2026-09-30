#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 body + 瞳孔层按 pet.json 的坐标合成为一张静态预览图（不启动窗口）。

    python make_preview.py [配置.json] [输出.png]
"""

from __future__ import annotations

import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "pet.json")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "preview.png")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    d = os.path.join(HERE, cfg["sprite_dir"])
    body = Image.open(os.path.join(d, cfg["body"])).convert("RGBA")
    eyes = [Image.open(os.path.join(d, n)).convert("RGBA") for n in cfg["eyes"]]

    for img, layout in zip(eyes, cfg["eye_layout"]):
        body.alpha_composite(img.resize((layout["w"], layout["h"]), Image.LANCZOS),
                             (layout["x"], layout["y"]))

    flat = Image.new("RGBA", body.size, (250, 250, 250, 255))
    flat.alpha_composite(body)
    flat.convert("RGB").save(out)
    print("预览图 -> %s" % out)


if __name__ == "__main__":
    main()
