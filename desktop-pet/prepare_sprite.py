#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把一张真实立绘做成桌宠素材（自动拆分"身体层 + 眼睛层"）。

用法:
    python prepare_sprite.py                 # 打开 GUI，自己选图
    python prepare_sprite.py 图片.png
    python prepare_sprite.py 图片.png --out assets/nahida

GUI 操作（右侧也有提示）:
    1. 拖动取景框四角 / 滚轮缩放：把角色的头脸放进黄框
       （黄框就是最终输出的 320x340，比例已锁定）
    2. 在左眼、右眼中心各点一下
    3. 拖动白色小方块微调眼睛框大小（默认按瞳距自动估算）
    4. 按 S 保存

保存时自动完成:
    * 裁剪缩放到 320x340 -> body.png
    * 按眼睛框抠出瞳孔层 -> eye_l.png / eye_r.png
    * 身体上的眼睛区域用周围肤色 + 眼窝渐变补掉
    * 计算 eye_layout 并写回 pet.json
    * 生成 preview.png 合成预览，并（若可用）自动跑一次渲染自检

坐标约定（重要，避免换算混乱）:
    * "源坐标"  = 原图像素坐标
    * "画布坐标" = 预览 Canvas 上的像素坐标
    * 变换只有两个： canvas = offset + src * scale
    * 取景框 crop 用源坐标表示，比例锁定 320:340
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_W, OUT_H = 320, 340
ASPECT = OUT_W / OUT_H
VIEW_W, VIEW_H = 780, 700
EYE_DEFAULT = 38
HANDLE = 7


# --------------------------------------------------------------------------
# 图像处理
# --------------------------------------------------------------------------


def crop_fit(im: Image.Image, box) -> Image.Image:
    """把源图 box 区域等比缩放并居中到 320x340（比例不一致时留透明边）。"""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0 = max(0, min(x0, im.width - 1))
    y0 = max(0, min(y0, im.height - 1))
    x1 = max(x0 + 1, min(x1, im.width))
    y1 = max(y0 + 1, min(y1, im.height))
    part = im.crop((x0, y0, x1, y1))
    sc = min(OUT_W / part.width, OUT_H / part.height)
    new = (max(1, int(round(part.width * sc))), max(1, int(round(part.height * sc))))
    part = part.resize(new, Image.LANCZOS)
    canvas = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    canvas.alpha_composite(part, ((OUT_W - new[0]) // 2, (OUT_H - new[1]) // 2))
    return canvas


def box_transform(box):
    """返回 (sc, pad)，把"源坐标"映射到"输出画布坐标"。"""
    x0, y0, x1, y1 = box
    w, h = max(1e-6, x1 - x0), max(1e-6, y1 - y0)
    sc = min(OUT_W / w, OUT_H / h)
    pad_x = (OUT_W - w * sc) / 2.0
    pad_y = (OUT_H - h * sc) / 2.0
    return sc, (pad_x, pad_y)


def ellipse_mask(size, box) -> np.ndarray:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).ellipse(box, fill=255)
    return np.array(m) > 127


def fill_eye_socket(img: Image.Image, mask: np.ndarray,
                    ring: int = 5) -> Image.Image:
    """把 mask 区域用外圈肤色 + 径向渐变补成眼窝。"""
    arr = np.array(img.convert("RGBA"), dtype=np.float32)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return img

    tmp = Image.fromarray((mask * 255).astype(np.uint8), "L")
    grown = np.array(tmp.filter(ImageFilter.MaxFilter(ring * 2 + 1))) > 127
    ring_mask = grown & (~mask)
    if ring_mask.sum() > 20:
        skin = np.median(arr[ring_mask][:, :3], axis=0)
    else:
        skin = np.array([255, 233, 219], dtype=np.float32)

    cx, cy = xs.mean(), ys.mean()
    rx = max(1.0, (xs.max() - xs.min()) / 2.0)
    ry = max(1.0, (ys.max() - ys.min()) / 2.0)
    rad = np.clip(np.sqrt(((xs - cx) / rx) ** 2 + ((ys - cy) / ry) ** 2), 0, 1)

    inner = skin * 0.88
    for i in range(len(xs)):
        t = float(rad[i])
        col = inner * (1 - t) + skin * t
        y, x = ys[i], xs[i]
        a = arr[y, x, 3] / 255.0
        arr[y, x, :3] = col * a + arr[y, x, :3] * (1 - a)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


def eye_layer_from(src: Image.Image, canvas_box, mask: np.ndarray) -> Image.Image:
    """把 oval 区域内的眼睛像素抠成一张独立图层。"""
    x0 = max(0, int(np.floor(canvas_box[0])))
    y0 = max(0, int(np.floor(canvas_box[1])))
    x1 = min(src.width, int(np.ceil(canvas_box[2])))
    y1 = min(src.height, int(np.ceil(canvas_box[3])))
    if x1 <= x0 or y1 <= y0:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    sub = src.crop((x0, y0, x1, y1)).convert("RGBA")
    m = Image.fromarray((mask[y0:y1, x0:x1] * 255).astype(np.uint8), "L")
    # 硬边掩码，避免边缘半透明导致眼睛"糊"
    m = m.point(lambda v: 255 if v > 127 else 0)
    sub.putalpha(ImageChops.multiply(sub.split()[3], m))
    return sub


def remove_background(im: Image.Image, tol: int = 40, feather: int = 1) -> Image.Image:
    """
    从四角出发做颜色漫水，把连通的背景抹成透明。

    适合"纯色 / 近纯色背景"的立绘（白底、浅色底）。复杂背景（渐变、场景）
    抠不干净，那种情况请先用 remove.bg 之类的工具处理。

    实现上用 PIL 的 floodfill（C 实现），比 numpy 迭代快几个数量级。
    """
    src = im.convert("RGBA")
    rgb = src.convert("RGB")
    w, h = rgb.size
    marker = (1, 2, 3)          # 一个几乎不会出现在立绘里的颜色
    work = rgb.copy()

    for sx, sy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        try:
            ImageDraw.floodfill(work, (sx, sy), marker, thresh=tol)
        except Exception:
            pass

    arr = np.array(work)
    bg = (arr[:, :, 0] == marker[0]) & (arr[:, :, 1] == marker[1]) & \
         (arr[:, :, 2] == marker[2])
    if not bg.any():
        return src

    a = np.array(src)[:, :, 3].astype(np.float32)
    a[bg] = 0
    out = src.copy()
    out.putalpha(Image.fromarray(a.astype(np.uint8), "L"))
    if feather:
        out.putalpha(out.split()[3].filter(ImageFilter.GaussianBlur(feather * 0.6)))
    return out


def build_assets(src: Image.Image, crop_box, eye_centers, eye_size_src: float):
    """
    纯函数版本：把源图 + 取景框 + 眼睛标记，做成 (body, eye_l, eye_r, layout)。

    eye_centers 用源坐标；eye_size_src 是眼睛框在源坐标下的边长。
    返回 (body, {"eye_l": im, "eye_r": im}, layout_list)
    """
    out = crop_fit(src, crop_box)
    sc, (px, py) = box_transform(crop_box)
    half = max(6.0, eye_size_src * sc / 2.0)
    half = min(half, min(OUT_W, OUT_H) / 4.0)

    body = out.copy()
    layers = {}
    layout = []
    for name, c in zip(("eye_l", "eye_r"), eye_centers):
        cx = (c[0] - crop_box[0]) * sc + px
        cy = (c[1] - crop_box[1]) * sc + py
        b = (cx - half, cy - half, cx + half, cy + half)
        mask = ellipse_mask(out.size, b)
        layer = eye_layer_from(out, b, mask)
        layers[name] = layer
        layout.append({"x": max(0, int(np.floor(b[0]))),
                       "y": max(0, int(np.floor(b[1]))),
                       "w": layer.width, "h": layer.height})
        body = fill_eye_socket(body, mask)

    return body, layers, layout


def compose_preview(body: Image.Image, layers, layout) -> Image.Image:
    prev = body.copy()
    for name, key in (("eye_l", 0), ("eye_r", 1)):
        prev.alpha_composite(layers[name], (layout[key]["x"], layout[key]["y"]))
    flat = Image.new("RGBA", prev.size, (248, 248, 248, 255))
    flat.alpha_composite(prev)
    return flat.convert("RGB")


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------


class Tool:
    def __init__(self, root: tk.Tk, path: str | None, out_dir: str, cfg_path: str) -> None:
        self.root = root
        self.out_dir = out_dir
        self.cfg_path = cfg_path
        self.src: Image.Image | None = None
        self.src_orig: Image.Image | None = None
        self.bg_on = False

        self.scale = 1.0
        self.offset = [40.0, 40.0]
        self.crop = [0.0, 0.0, 320.0, 340.0]      # 源坐标
        self.eye_src: list[list[float]] = []      # 源坐标
        self.eye_size = EYE_DEFAULT               # 源坐标下的眼睛框边长
        self.eye_size_auto = True

        self.drag: str | None = None
        self.drag_start = (0.0, 0.0)
        self.drag_snapshot = None

        root.title("桌宠素材准备工具")
        root.geometry("%dx%d" % (VIEW_W + 270, VIEW_H + 80))
        root.configure(bg="#2b2b2b")

        self.canvas = tk.Canvas(root, width=VIEW_W, height=VIEW_H, bg="#2b2b2b",
                                highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        side = tk.Frame(root, width=260, bg="#f2f2f2")
        side.pack(side="right", fill="y")
        self.build_side(side)

        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        root.bind("<Key>", self.on_key)

        if path:
            self.load(path)
        else:
            root.after(150, self.pick_file)

    # ---------------- 侧栏 ----------------
    def build_side(self, parent: tk.Frame) -> None:
        tk.Label(parent, text="立绘拆层", bg="#f2f2f2",
                 font=("Microsoft YaHei", 13, "bold")).pack(pady=(14, 6), padx=14, anchor="w")
        tips = ("① 拖取景框四角，把脸放进黄框\n"
                "   滚轮缩放，黄框外拖动=平移\n"
                "② 依次点击左眼、右眼中心\n"
                "③ 拖白色方块调眼睛框大小\n"
                "④ 按 S 保存并写回 pet.json\n\n"
                "快捷键：S 保存 / R 重置取景框\n"
                "        E 清空眼睛 / O 重选图片")
        tk.Label(parent, text=tips, bg="#f2f2f2", justify="left",
                 font=("Microsoft YaHei", 9)).pack(padx=14, anchor="w")

        self.info = tk.Label(parent, text="", bg="#f2f2f2", justify="left", fg="#0a6b3d",
                             font=("Consolas", 9))
        self.info.pack(padx=14, pady=12, anchor="w")

        self.eye_var = tk.DoubleVar(value=self.eye_size)
        tk.Scale(parent, from_=12, to=140, orient="horizontal", label="眼睛框边长(源像素)",
                 variable=self.eye_var, length=210, resolution=1,
                 command=self.on_eye_scale).pack(padx=14, pady=4)

        self.bg_auto = tk.BooleanVar(value=True)
        tk.Checkbutton(parent, text="载入时自动抠背景", variable=self.bg_auto,
                       bg="#f2f2f2", font=("Microsoft YaHei", 9)).pack(padx=14, anchor="w")
        self.bg_btn = tk.Button(parent, text="一键抠背景", command=self.toggle_bg)
        self.bg_btn.pack(padx=14, pady=4, fill="x")

        tk.Button(parent, text="保存 (S)", command=self.save,
                  font=("Microsoft YaHei", 10, "bold"), bg="#d8f3e0").pack(
            padx=14, pady=8, fill="x")
        tk.Button(parent, text="重新选图 (O)", command=self.pick_file).pack(
            padx=14, pady=2, fill="x")
        tk.Button(parent, text="退出", command=self.root.destroy).pack(
            padx=14, pady=2, fill="x")

        tk.Label(parent, text="输出目录:\n" + self.out_dir, bg="#f2f2f2", fg="#666",
                 wraplength=230, justify="left",
                 font=("Microsoft YaHei", 8)).pack(side="bottom", pady=12, padx=10)

    def on_eye_scale(self, v) -> None:
        self.eye_size = float(v)
        self.eye_size_auto = False
        self.redraw()

    # ---------------- 坐标变换 ----------------
    def to_canvas(self, x: float, y: float):
        return self.offset[0] + x * self.scale, self.offset[1] + y * self.scale

    def to_src(self, x: float, y: float):
        return (x - self.offset[0]) / self.scale, (y - self.offset[1]) / self.scale

    def crop_handles(self):
        x0, y0, x1, y1 = self.crop
        pts = {
            "nw": (x0, y0), "ne": (x1, y0), "sw": (x0, y1), "se": (x1, y1),
        }
        return {k: self.to_canvas(*v) for k, v in pts.items()}

    # ---------------- 载入 ----------------
    def pick_file(self) -> None:
        p = filedialog.askopenfilename(
            title="选择立绘（建议正面、上半身、背景好抠）",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp"), ("全部", "*.*")])
        if p:
            self.load(p)

    def load(self, path: str) -> None:
        im = Image.open(path)
        self.src_orig = im.convert("RGBA")
        self.src = self.src_orig
        self.scale = (VIEW_W * 0.85) / max(1, self.src.width)
        self.offset = [40.0, 30.0]
        self.crop = [0.0, 0.0, float(OUT_W), float(OUT_H)]
        self.eye_src = []
        self.eye_size_auto = True
        self.eye_size = EYE_DEFAULT
        self.root.title("桌宠素材准备工具 —— " + os.path.basename(path))
        self.redraw()
        if self.bg_auto.get():
            self.root.after(120, self.toggle_bg)

    def toggle_bg(self) -> None:
        """在"原图"和"抠好背景"之间切换。"""
        if self.src_orig is None:
            return
        if self.bg_on:
            self.src = self.src_orig
            self.bg_on = False
        else:
            self.src = remove_background(self.src_orig)
            self.bg_on = True
        self.bg_btn.config(text="背景: 已抠" if self.bg_on else "一键抠背景")
        self.redraw()

    # ---------------- 绘制 ----------------
    def redraw(self) -> None:
        self.canvas.delete("all")
        if self.src is None:
            self.canvas.create_text(VIEW_W // 2, VIEW_H // 2, fill="#999",
                                    text="还没有载入图片\n按 O 选择一张立绘",
                                    font=("Microsoft YaHei", 12))
            return

        w = max(1, int(self.src.width * self.scale))
        h = max(1, int(self.src.height * self.scale))
        self.photo = ImageTk.PhotoImage(self.src.resize((w, h), Image.LANCZOS))
        self.canvas.create_image(self.offset, anchor="nw", image=self.photo)

        # 黄框 = 输出范围
        x0, y0 = self.to_canvas(self.crop[0], self.crop[1])
        x1, y1 = self.to_canvas(self.crop[2], self.crop[3])
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#ffd400", width=2)
        self.canvas.create_text(x0 + 6, y0 + 12, anchor="w", fill="#ffd400",
                                text="输出 320x340", font=("Consolas", 9))
        # 取景框外的暗化提示（四条遮罩）
        dark = "#000000"
        for box in ((0, 0, VIEW_W, y0), (0, y1, VIEW_W, VIEW_H),
                    (0, y0, x0, y1), (x1, y0, VIEW_W, y1)):
            if box[2] > box[0] and box[3] > box[1]:
                self.canvas.create_rectangle(*box, fill=dark, outline="",
                                             stipple="gray50")
        # 四角手柄
        for k, (hx, hy) in self.crop_handles().items():
            self.canvas.create_rectangle(hx - HANDLE, hy - HANDLE, hx + HANDLE, hy + HANDLE,
                                         fill="#ffd400", outline="#7a6200")

        # 眼睛框
        half = self.eye_size / 2
        for i, (ex, ey) in enumerate(self.eye_src):
            a = self.to_canvas(ex - half, ey - half)
            b = self.to_canvas(ex + half, ey + half)
            col = "#00e5ff" if i == 0 else "#ff7bd5"
            self.canvas.create_oval(a[0], a[1], b[0], b[1], outline=col, width=2)
            self.canvas.create_text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, fill=col,
                                    text="L" if i == 0 else "R",
                                    font=("Consolas", 10, "bold"))
            for hx, hy in ((a[0], a[1]), (b[0], a[1]), (a[0], b[1]), (b[0], b[1])):
                self.canvas.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5,
                                             fill="#ffffff", outline="#333333")

        self.info.config(text=self.status_text())

    def status_text(self) -> str:
        assert self.src is not None
        lines = ["源图 %dx%d  缩放 %.0f%%" % (self.src.width, self.src.height, self.scale * 100)]
        lines.append("取景框 %.0fx%.0f" % (self.crop[2] - self.crop[0], self.crop[3] - self.crop[1]))
        if len(self.eye_src) < 2:
            lines.append("还需标记 %d 只眼睛" % (2 - len(self.eye_src)))
        else:
            (lx, ly), (rx, ry) = self.eye_src
            lines.append("瞳距 %.0fpx  眼框 %.0fpx" % (abs(rx - lx), self.eye_size))
            lines.append("可以按 S 保存了")
        return "\n".join(lines)

    # ---------------- 交互 ----------------
    def hit_handle(self, x, y):
        for k, (hx, hy) in self.crop_handles().items():
            if abs(x - hx) <= HANDLE + 2 and abs(y - hy) <= HANDLE + 2:
                return k
        return None

    def hit_eye_handle(self, x, y):
        half = self.eye_size / 2
        for i, (ex, ey) in enumerate(self.eye_src):
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                hx, hy = self.to_canvas(ex + sx * half, ey + sy * half)
                if abs(x - hx) <= 8 and abs(y - hy) <= 8:
                    return i, (sx, sy)
        return None

    def on_hover(self, e) -> None:
        if self.src is None or self.drag:
            return
        h = self.hit_handle(e.x, e.y)
        self.canvas.configure(cursor={
            "nw": "top_left_corner", "se": "bottom_right_corner",
            "ne": "top_right_corner", "sw": "bottom_left_corner"}.get(h, "crosshair"))

    def on_press(self, e) -> None:
        if self.src is None:
            return
        self.drag_start = (e.x, e.y)
        self.drag_snapshot = (list(self.crop), list(self.offset))

        hit = self.hit_eye_handle(e.x, e.y)
        if hit:
            self.drag = "eye"
            self.drag_eye = hit
            return
        h = self.hit_handle(e.x, e.y)
        if h:
            self.drag = "crop:" + h
            return
        # 框内：平移取景框（等效搬动图片）；框外：平移图片看别处
        x0, y0 = self.to_canvas(self.crop[0], self.crop[1])
        x1, y1 = self.to_canvas(self.crop[2], self.crop[3])
        self.drag = "move" if (x0 <= e.x <= x1 and y0 <= e.y <= y1) else "pan"

    def on_move(self, e) -> None:
        if self.src is None or not self.drag:
            return
        dx, dy = (e.x - self.drag_start[0]) / self.scale, (e.y - self.drag_start[1]) / self.scale
        crop0, off0 = self.drag_snapshot

        if self.drag == "pan":
            self.offset = [off0[0] + (e.x - self.drag_start[0]),
                           off0[1] + (e.y - self.drag_start[1])]
        elif self.drag == "eye":
            i, _ = self.drag_eye
            sx, sy = self.to_src(e.x, e.y)
            self.eye_src[i] = [sx, sy]
        elif self.drag == "move":
            self.crop = [crop0[0] + dx, crop0[1] + dy, crop0[2] + dx, crop0[3] + dy]
        elif self.drag.startswith("crop:"):
            corner = self.drag.split(":")[1]
            fixed_x = crop0[0] if "e" in corner else crop0[2]
            fixed_y = crop0[1] if "s" in corner else crop0[3]
            sx, sy = self.to_src(e.x, e.y)
            w = abs(sx - fixed_x)
            h = abs(sy - fixed_y)
            # 锁定 320:340 比例，取较大的那个方向
            if w / max(h, 1e-6) > ASPECT:
                h = w / ASPECT
            else:
                w = h * ASPECT
            nx0 = fixed_x if "e" in corner else fixed_x - w
            ny0 = fixed_y if "s" in corner else fixed_y - h
            self.crop = [nx0, ny0, nx0 + w, ny0 + h]
        self.redraw()

    def on_release(self, e) -> None:
        moved = abs(e.x - self.drag_start[0]) + abs(e.y - self.drag_start[1])
        if self.drag is None and moved <= 3 and self.src is not None \
                and len(self.eye_src) < 2:
            self.eye_src.append(list(self.to_src(e.x, e.y)))
            if len(self.eye_src) == 2 and self.eye_size_auto:
                (lx, ly), (rx, ry) = self.eye_src
                d = abs(rx - lx)
                self.eye_size = max(12.0, d * 0.42)
                self.eye_var.set(self.eye_size)
        self.drag = None
        self.redraw()

    def on_wheel(self, e) -> None:
        if self.src is None:
            return
        ix, iy = self.to_src(e.x, e.y)
        f = 1.12 if e.delta > 0 else 1 / 1.12
        self.scale = max(0.03, min(12.0, self.scale * f))
        nx, ny = self.to_canvas(ix, iy)
        self.offset[0] += e.x - nx
        self.offset[1] += e.y - ny
        self.redraw()

    def on_key(self, e) -> None:
        k = e.keysym.lower()
        if k == "s":
            self.save()
        elif k == "r":
            self.crop = [0.0, 0.0, float(OUT_W), float(OUT_H)]
            self.redraw()
        elif k == "e":
            self.eye_src = []
            self.redraw()
        elif k == "o":
            self.pick_file()

    # ---------------- 保存 ----------------
    def save(self) -> None:
        if self.src is None:
            return
        if len(self.eye_src) < 2:
            messagebox.showinfo("还差一步", "请先在左眼、右眼中心各点一下。")
            return

        box = tuple(self.crop)
        body, layers, layout = build_assets(self.src, box, self.eye_src, self.eye_size)

        os.makedirs(self.out_dir, exist_ok=True)
        body.save(os.path.join(self.out_dir, "body.png"))
        layers["eye_l"].save(os.path.join(self.out_dir, "eye_l.png"))
        layers["eye_r"].save(os.path.join(self.out_dir, "eye_r.png"))

        cfg = {}
        if os.path.exists(self.cfg_path):
            with open(self.cfg_path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
        cfg["sprite_dir"] = os.path.relpath(self.out_dir, HERE).replace("\\", "/")
        cfg["body"] = "body.png"
        cfg["eyes"] = ["eye_l.png", "eye_r.png"]
        cfg["eye_layout"] = layout
        # 真实素材颜色丰富，眼神幅度收敛一点更自然
        cfg["max_pupil_offset"] = 4.0
        cfg["gaze_gain"] = 1.15
        with open(self.cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)

        compose_preview(body, layers, layout).save(os.path.join(HERE, "preview.png"))

        # 自动渲染自检，确认素材能被引擎正常合成
        report = ""
        try:
            r = subprocess.run([sys.executable, os.path.join(HERE, "verify_pet.py")],
                               capture_output=True, text=True, timeout=60)
            for line in (r.stdout or "").splitlines():
                if "结论" in line or "比例" in line:
                    report += line.strip() + "\n"
        except Exception as exc:
            report = "（自检未运行: %s）" % exc

        messagebox.showinfo(
            "完成",
            "已生成:\n%s\n\npet.json 已指向新素材。\n\n自检:\n%s\n预览: preview.png" % (
                self.out_dir, report or "见 verify_pet.py 输出"))
        self.redraw()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else None
    out_dir = os.path.join(HERE, "assets", "custom")
    if "--out" in sys.argv:
        out_dir = sys.argv[sys.argv.index("--out") + 1]
    root = tk.Tk()
    Tool(root, path, out_dir, os.path.join(HERE, "pet.json"))
    root.mainloop()


if __name__ == "__main__":
    main()
