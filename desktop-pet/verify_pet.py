#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自检：确认"逻辑尺寸 vs 物理尺寸"是否劈叉 + 窗口内容是否完整。

思路：在 pet 进程内部直接读 winfo_*（Tk 逻辑值），再用 GetWindowRect 读物理值，
然后用 PrintWindow 取窗口客户区内容，数一数非透明像素有多少。
正常情况：非透明像素数 ≈ 素材里角色的像素数；劈叉时只有约 1/4。

    python verify_pet.py [配置.json]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pet as P  # noqa: E402

cfg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(P.HERE, "pet.json")
cfg = P.load_config(cfg_path)
pet = P.Pet(cfg)

for _ in range(30):
    pet._last = time.time() - 1 / 30
    pet._update(1 / 30)
    pet.root.update_idletasks()
pet._render(y_shift=-pet.hop_z)
pet.root.update_idletasks()

root = pet.root
print("Tk 逻辑值 : screen=%dx%d  root=%dx%d  canvas=%dx%d  pos=(%d,%d)" % (
    root.winfo_screenwidth(), root.winfo_screenheight(),
    root.winfo_width(), root.winfo_height(),
    pet.canvas.winfo_width(), pet.canvas.winfo_height(),
    pet.pos_x, pet.pos_y))

u = ctypes.windll.user32
hwnd = u.GetParent(root.winfo_id()) or root.winfo_id()
r = wt.RECT()
u.GetWindowRect(hwnd, ctypes.byref(r))
print("Win32 物理: rect=(%d,%d)-(%d,%d)  尺寸=%dx%d" % (
    r.left, r.top, r.right, r.bottom, r.right - r.left, r.bottom - r.top))

# 取窗口内容，数非透明（非色键）像素
g = ctypes.windll.gdi32
w, h = r.right - r.left, r.bottom - r.top
hdc = u.GetWindowDC(hwnd)
mdc = g.CreateCompatibleDC(hdc)
bmp = g.CreateCompatibleBitmap(hdc, w, h)
g.SelectObject(mdc, bmp)
u.PrintWindow(hwnd, mdc, 2)


class BMI(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


bi = BMI()
bi.biSize = ctypes.sizeof(BMI)
bi.biWidth = w
bi.biHeight = -h
bi.biPlanes = 1
bi.biBitCount = 32
buf = ctypes.create_string_buffer(w * h * 4)
g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)

from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402

img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
img.convert("RGB").save(os.path.join(P.HERE, "verify_window.png"))
a = np.array(img).astype(int)
key = np.array([int(P.COLOR_KEY[i:i + 2], 16) for i in (1, 3, 5)])
opaque = int((np.abs(a[:, :, :3] - key).sum(axis=2) > 10).sum())

# 直接按素材算"应该"有多少不透明像素
body = Image.open(os.path.join(pet.sprite_dir, cfg["body"])).convert("RGBA")
expect = int((np.array(body)[:, :, 3] > 90).sum())
print("不透明像素: 实际=%d  素材角色=%d  比例=%.2f" % (opaque, expect, opaque / max(expect, 1)))
print("结论: %s" % ("正常（整幅画完）" if opaque > expect * 0.8 else
                  "异常：内容被裁剪或缩放，逻辑/物理尺寸劈叉"))

u.ReleaseDC(hwnd, hdc)
g.DeleteObject(bmp)
g.DeleteDC(mdc)
pet.root.destroy()
